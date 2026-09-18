import json
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID
from app.config import settings
import redis

from app.database import SessionLocal
from app.models import Job

r = redis.Redis.from_url(settings.redis_url, decode_responses=True)

JOB_QUEUES_BY_PRIORITY = ["job_queue:high", "job_queue:normal", "job_queue:low"]
FAILED_JOB_QUEUE = "failed_job_queue"
RETRY_SCHEDULE = "retry_schedule"
MAX_ATTEMPTS = 3

HEARTBEAT_INTERVAL_SECONDS = 5
STALE_THRESHOLD_SECONDS = 20
REAP_CHECK_INTERVAL_SECONDS = 10

WORKER_NAME = f"{socket.gethostname()}-{os.getpid()}"

_shutdown_requested = False


def _handle_shutdown_signal(signum, frame):
    global _shutdown_requested
    print(f"[{WORKER_NAME}] Shutdown signal received - finishing current cycle, then exiting.")
    _shutdown_requested = True


class TemporaryJobError(Exception):
    """An error that may succeed if TaskForge retries later."""


def update_database_job(
    job: dict,
    status: str,
    error_message: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
    failed_at: Optional[datetime] = None,
) -> bool:
    job_id = job.get("id")

    if not job_id:
        print(f"[{WORKER_NAME}] Job has no ID, so it cannot be tracked in PostgreSQL.")
        return False

    try:
        job_uuid = UUID(job_id)
    except (ValueError, TypeError):
        print(f"[{WORKER_NAME}] Invalid job ID: {job_id}")
        return False

    db = SessionLocal()

    try:
        database_job = db.get(Job, job_uuid)

        if database_job is None:
            print(f"[{WORKER_NAME}] No PostgreSQL record found for job {job_id}.")
            return False

        database_job.status = status
        database_job.attempt = job.get("attempt", 1)
        database_job.error_message = error_message

        if started_at is not None:
            database_job.started_at = started_at

        if completed_at is not None:
            database_job.completed_at = completed_at

        if failed_at is not None:
            database_job.failed_at = failed_at

        if status == "processing":
            database_job.last_heartbeat = datetime.now(timezone.utc)
        else:
            database_job.last_heartbeat = None

        db.commit()
        return True

    except Exception as error:
        db.rollback()
        print(f"[{WORKER_NAME}] PostgreSQL update failed: {error}")
        return False

    finally:
        db.close()


def send_heartbeat(job_id: str):
    try:
        job_uuid = UUID(job_id)
    except (ValueError, TypeError):
        return

    db = SessionLocal()
    try:
        database_job = db.get(Job, job_uuid)
        if database_job is not None and database_job.status == "processing":
            database_job.last_heartbeat = datetime.now(timezone.utc)
            db.commit()
    except Exception as error:
        db.rollback()
        print(f"[{WORKER_NAME}] Heartbeat update failed for {job_id}: {error}")
    finally:
        db.close()


def validate_job(job: object) -> dict:
    if not isinstance(job, dict):
        raise ValueError("Job must be a JSON object")

    if not isinstance(job.get("type"), str) or not job["type"].strip():
        raise ValueError("Job needs a non-empty 'type' string")

    if not isinstance(job.get("payload"), dict):
        raise ValueError("Job needs a 'payload' object")

    if "attempt" in job:
        if not isinstance(job["attempt"], int) or job["attempt"] < 1:
            raise ValueError("'attempt' must be a positive integer")

    return job


def process_job(job: dict):
    job_id = job.get("id", "legacy-job")
    job_type = job["type"]
    payload = job["payload"]
    attempt = job.get("attempt", 1)

    update_database_job(
        job,
        status="processing",
        started_at=datetime.now(timezone.utc),
    )

    print(
        f"[{WORKER_NAME}] Processing {job_id}: "
        f"{job_type} (attempt {attempt}/{MAX_ATTEMPTS})"
    )

    if payload.get("simulate_temporary_failure") and attempt < MAX_ATTEMPTS:
        raise TemporaryJobError("Report service is temporarily unavailable")

    stop_heartbeat = threading.Event()

    def heartbeat_loop():
        while not stop_heartbeat.wait(HEARTBEAT_INTERVAL_SECONDS):
            send_heartbeat(job_id)

    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()

    try:
        work_seconds = payload.get("simulate_work_seconds", 1)
        elapsed = 0
        while elapsed < work_seconds:
            time.sleep(min(1, work_seconds - elapsed))
            elapsed += 1
    finally:
        stop_heartbeat.set()
        heartbeat_thread.join(timeout=1)

    update_database_job(
        job,
        status="completed",
        completed_at=datetime.now(timezone.utc),
    )

    print(f"[{WORKER_NAME}] Completed {job_id}: {job_type}")


def save_failed_job(
    raw_job_data: str,
    reason: str,
    job: Optional[dict] = None,
):
    if job is not None:
        update_database_job(
            job,
            status="failed",
            error_message=reason,
            failed_at=datetime.now(timezone.utc),
        )

    failed_job = {
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "raw_job_data": raw_job_data,
    }

    r.lpush(FAILED_JOB_QUEUE, json.dumps(failed_job))
    print(f"[{WORKER_NAME}] Moved job to {FAILED_JOB_QUEUE}: {reason}")


def schedule_retry(job: dict, reason: str):
    current_attempt = job.get("attempt", 1)
    job_id = job.get("id", "legacy-job")

    if current_attempt >= MAX_ATTEMPTS:
        save_failed_job(
            json.dumps(job),
            f"Failed after {current_attempt} attempts: {reason}",
            job,
        )
        return

    delay_seconds = 2 ** current_attempt
    job["attempt"] = current_attempt + 1
    raw_job_data = json.dumps(job)
    retry_at = time.time() + delay_seconds

    update_database_job(
        job,
        status="retrying",
        error_message=reason,
    )

    r.zadd(RETRY_SCHEDULE, {raw_job_data: retry_at})

    print(
        f"[{WORKER_NAME}] Temporary failure: {reason}. "
        f"Retrying {job_id} in {delay_seconds} seconds."
    )


def promote_due_retries():
    due_jobs = r.zrangebyscore(RETRY_SCHEDULE, "-inf", time.time())

    for raw_job_data in due_jobs:
        if r.zrem(RETRY_SCHEDULE, raw_job_data):
            job = json.loads(raw_job_data)
            target_queue = f"job_queue:{job.get('priority', 'normal')}"
            r.lpush(target_queue, raw_job_data)

            print(
                f"[{WORKER_NAME}] Retry is due for "
                f"{job.get('id', 'legacy-job')}; returned it to {target_queue}."
            )


def reap_stale_jobs():
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc).timestamp() - STALE_THRESHOLD_SECONDS

        stale_jobs = db.query(Job).filter(Job.status == "processing").all()

        for database_job in stale_jobs:
            if database_job.last_heartbeat is None:
                continue
            if database_job.last_heartbeat.timestamp() >= cutoff:
                continue

            job_id = str(database_job.id)
            print(
                f"[{WORKER_NAME}] Reaping {job_id}: no heartbeat since "
                f"{database_job.last_heartbeat.isoformat()} - worker likely crashed."
            )

            reconstructed_job = {
                "id": job_id,
                "type": database_job.job_type,
                "payload": database_job.payload,
                "attempt": database_job.attempt,
                "priority": database_job.priority,
            }
            schedule_retry(reconstructed_job, "Worker crashed (stale heartbeat)")

    finally:
        db.close()


def run_worker():
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    print(f"Worker {WORKER_NAME} started. Waiting for jobs... (Ctrl+C to stop)")
    last_reap_check = 0.0

    while not _shutdown_requested:
        promote_due_retries()

        if time.time() - last_reap_check >= REAP_CHECK_INTERVAL_SECONDS:
            reap_stale_jobs()
            last_reap_check = time.time()

        result = r.brpop(JOB_QUEUES_BY_PRIORITY, timeout=1)

        if result is None:
            continue

        _, raw_job_data = result
        validated_job = None

        try:
            job = json.loads(raw_job_data)
            validated_job = validate_job(job)
            process_job(validated_job)

        except TemporaryJobError as error:
            schedule_retry(validated_job, str(error))

        except Exception as error:
            save_failed_job(raw_job_data, str(error), validated_job)

    print(f"[{WORKER_NAME}] Shut down cleanly.")


if __name__ == "__main__":
    run_worker()