import time
import uuid

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Job
from consumer import reap_stale_jobs

client = TestClient(app)


def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:10]}@example.com"


def create_authed_user() -> str:
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    login = client.post(
        "/auth/login", json={"email": email, "password": "testpass123"}
    )
    return login.json()["access_token"]


def wait_for_status(job_id: str, headers: dict, target: str, timeout: int = 20) -> dict:
    deadline = time.time() + timeout
    last_body = None
    while time.time() < deadline:
        response = client.get(f"/jobs/{job_id}", headers=headers)
        last_body = response.json()
        if last_body.get("status") == target:
            return last_body
        time.sleep(0.5)
    raise AssertionError(
        f"Job {job_id} never reached status '{target}'; last seen: {last_body}"
    )


def test_job_recovers_after_temporary_failure_and_retries():
    """
    simulate_temporary_failure makes the job fail on its first attempts
    and succeed once attempt reaches MAX_ATTEMPTS. This proves the
    exponential-backoff retry path actually runs end-to-end, not just
    that the happy path works.
    """
    token = create_authed_user()
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/jobs",
        json={
            "type": "generate_report",
            "payload": {
                "report_id": 1,
                "simulate_work_seconds": 1,
                "simulate_temporary_failure": True,
            },
        },
        headers=headers,
    )
    job_id = response.json()["id"]

    final = wait_for_status(job_id, headers, target="completed", timeout=20)
    assert final["status"] == "completed"
    assert final["attempt"] > 1, "job should have needed more than one attempt"


def test_rate_limit_returns_429_after_limit_exceeded():
    """
    Submits requests past the per-user limit (10/60s) and confirms the
    API actually enforces it, rather than just trusting the code exists.
    """
    token = create_authed_user()
    headers = {"Authorization": f"Bearer {token}"}

    statuses = []
    for i in range(12):
        response = client.post(
            "/jobs",
            json={"type": "send_email", "payload": {"to": "x@example.com"}},
            headers=headers,
        )
        statuses.append(response.status_code)

    assert 429 in statuses, f"expected a 429 among the responses, got: {statuses}"
    assert statuses[0] == 202


def test_reaper_recovers_a_stale_processing_job():
    """
    Directly simulates a crashed worker: create a job row stuck in
    'processing' with an old last_heartbeat (as if the worker died
    mid-job), call reap_stale_jobs() directly (the same function the
    real worker calls every REAP_CHECK_INTERVAL_SECONDS), and confirm
    the job gets requeued and eventually completes.
    """
    from datetime import datetime, timedelta, timezone

    token = create_authed_user()
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/jobs",
        json={"type": "send_email", "payload": {"to": "x@example.com"}},
        headers=headers,
    )
    job_id = response.json()["id"]

    wait_for_status(job_id, headers, target="completed", timeout=15)

    db = SessionLocal()
    try:
        db_job = db.get(Job, uuid.UUID(job_id))
        db_job.status = "processing"
        db_job.attempt = 1
        db_job.last_heartbeat = datetime.now(timezone.utc) - timedelta(seconds=60)
        db.commit()
    finally:
        db.close()

    reap_stale_jobs()

    final = wait_for_status(job_id, headers, target="completed", timeout=15)
    assert final["status"] == "completed"
    assert final["attempt"] >= 2, "reaped job should show a second attempt"