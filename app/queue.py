import json
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID, uuid4

import redis
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models import Job

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)


VALID_PRIORITIES = {"high", "normal", "low"}


def queue_name_for(priority: str) -> str:
    return f"job_queue:{priority}"


def enqueue_job(
    job_type: str,
    payload: dict,
    idempotency_key: Optional[str] = None,
    priority: str = "normal",
    user_id: Optional[UUID] = None,
) -> dict:

    if priority not in VALID_PRIORITIES:
        raise ValueError(
            f"priority must be one of {VALID_PRIORITIES}, got '{priority}'"
        )

    job_id = uuid4()
    created_at = datetime.now(timezone.utc)

    db = SessionLocal()

    try:
        database_job = Job(
            id=job_id,
            job_type=job_type,
            payload=payload,
            idempotency_key=idempotency_key,
            priority=priority,
            user_id=user_id,
            status="queued",
            attempt=1,
            created_at=created_at,
        )

        db.add(database_job)
        db.commit()

    except IntegrityError:
        db.rollback()

        existing = (
            db.query(Job)
            .filter(Job.idempotency_key == idempotency_key)
            .first()
        )

        db.close()

        if existing is not None:
            return {
                "id": str(existing.id),
                "type": existing.job_type,
                "payload": existing.payload,
                "attempt": existing.attempt,
                "priority": existing.priority,
                "created_at": existing.created_at.isoformat(),
                "duplicate": True,
            }

        raise

    except Exception:
        db.rollback()
        db.close()
        raise

    else:
        db.close()

    job = {
        "id": str(job_id),
        "type": job_type,
        "payload": payload,
        "attempt": 1,
        "priority": priority,
        "created_at": created_at.isoformat(),
        "duplicate": False,
    }

    redis_client.lpush(
        queue_name_for(priority),
        json.dumps(job),
    )

    return job