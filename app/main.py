from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
    
)
from app.rate_limit import check_rate_limit
from app.database import SessionLocal
from app.models import Job, User
from app.queue import enqueue_job , redis_client


app = FastAPI(
    title="TaskForge API",
    version="0.1.0",
)


# ============================================================
# Authentication schemas
# ============================================================

class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: str
    password: str


# ============================================================
# Job schemas
# ============================================================

class JobCreate(BaseModel):
    type: str = Field(min_length=1)
    payload: dict[str, Any]
    idempotency_key: Optional[str] = Field(
        default=None,
        max_length=255,
    )
    priority: str = Field(
        default="normal",
        pattern="^(high|normal|low)$",
    )


# ============================================================
# Helper functions
# ============================================================

def job_to_response(job: Job) -> dict:
    return {
        "id": str(job.id),
        "type": job.job_type,
        "payload": job.payload,
        "status": job.status,
        "attempt": job.attempt,
        "priority": job.priority,
        "created_at": job.created_at.isoformat(),
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": (
            job.completed_at.isoformat()
            if job.completed_at
            else None
        ),
        "failed_at": (
            job.failed_at.isoformat()
            if job.failed_at
            else None
        ),
        "error_message": job.error_message,
    }


# ============================================================
# Health
# ============================================================

@app.get("/health")
def health_check() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "taskforge-api",
    }
@app.get("/ready")
def readiness_check() -> dict:
    # /health just proves the process is alive. /ready proves it can
    # actually do its job right now — reachable database, reachable
    # Redis. A load balancer or orchestrator uses this to decide whether
    # to send traffic here at all.
    checks = {"database": False, "redis": False}

    db = SessionLocal()
    try:
        db.execute(select(1))
        checks["database"] = True
    except Exception:
        pass
    finally:
        db.close()

    try:
        redis_client.ping()
        checks["redis"] = True
    except Exception:
        pass

    all_ready = all(checks.values())
    if not all_ready:
        raise HTTPException(status_code=503, detail={"ready": False, "checks": checks})

    return {"ready": True, "checks": checks}

# ============================================================
# Authentication
# ============================================================

@app.post("/auth/signup", status_code=status.HTTP_201_CREATED)
def signup(request: SignupRequest) -> dict:
    email = request.email.strip().lower()

    db = SessionLocal()

    try:
        existing_user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if existing_user is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email is already registered",
            )

        user = User(
            id=uuid4(),
            email=email,
            hashed_password=hash_password(request.password),
            created_at=datetime.now(timezone.utc),
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        return {
            "id": str(user.id),
            "email": user.email,
            "created_at": user.created_at.isoformat(),
        }

    except HTTPException:
        db.rollback()
        raise

    except IntegrityError:
        db.rollback()

        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email is already registered",
        )

    finally:
        db.close()


@app.post("/auth/login")
def login(request: LoginRequest) -> dict:
    email = request.email.strip().lower()

    db = SessionLocal()

    try:
        user = (
            db.query(User)
            .filter(User.email == email)
            .first()
        )

        if user is None or not verify_password(
            request.password,
            user.hashed_password,
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        access_token = create_access_token(user.id)

        return {
            "access_token": access_token,
            "token_type": "bearer",
        }

    finally:
        db.close()


# ============================================================
# Jobs
# ============================================================

@app.post(
    "/jobs",
    status_code=status.HTTP_202_ACCEPTED,
)
def create_job(
    job_request: JobCreate,
    response: Response,
    current_user: User = Depends(get_current_user),
) -> dict:
    if not check_rate_limit(str(current_user.id)):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded: max 10 job submissions per 60 seconds",
        )

    job = enqueue_job(
        job_type=job_request.type,
        payload=job_request.payload,
        idempotency_key=job_request.idempotency_key,
        priority=job_request.priority,
        user_id=current_user.id,
    )

    if job.get("duplicate"):
        response.status_code = status.HTTP_200_OK

    return {
        "id": job["id"],
        "type": job["type"],
        "status": (
            "duplicate"
            if job.get("duplicate")
            else "queued"
        ),
        "created_at": job["created_at"],
    }


@app.get("/jobs")
def list_jobs(
    job_status: Optional[str] = Query(
        default=None,
        alias="status",
    ),
    limit: int = Query(
        default=20,
        ge=1,
        le=100,
    ),
    offset: int = Query(
        default=0,
        ge=0,
    ),
    current_user: User = Depends(get_current_user),
) -> list[dict]:

    db = SessionLocal()

    try:
        statement = select(Job).where(
            Job.user_id == current_user.id
        )

        if job_status is not None:
            statement = statement.where(
                Job.status == job_status
            )

        statement = (
            statement
            .order_by(Job.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        jobs = db.scalars(statement).all()

        return [
            job_to_response(job)
            for job in jobs
        ]

    finally:
        db.close()


@app.get("/jobs/{job_id}")
def get_job(
    job_id: UUID,
    current_user: User = Depends(get_current_user),
) -> dict:

    db = SessionLocal()

    try:
        job = db.get(Job, job_id)

        if job is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Job not found",
            )

        if job.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this job",
            )

        return job_to_response(job)

    finally:
        db.close()
        
@app.get("/stats")
def get_stats(current_user: User = Depends(get_current_user)) -> dict:
    db = SessionLocal()
    try:
        status_counts = dict(
            db.query(Job.status, func.count(Job.id))
            .filter(Job.user_id == current_user.id)
            .group_by(Job.status)
            .all()
        )

        total_completed = status_counts.get("completed", 0)
        total_failed = status_counts.get("failed", 0)
        total_finished = total_completed + total_failed
        success_rate = (
            round(total_completed / total_finished, 3)
            if total_finished > 0
            else None
        )

        avg_attempts = (
            db.query(func.avg(Job.attempt))
            .filter(Job.user_id == current_user.id, Job.status == "completed")
            .scalar()
        )

        # Queue wait: time between a job being created and a worker
        # actually starting it. Processing time: time the worker actually
        # spent on it. Keeping these separate shows WHERE time goes —
        # a slow system could be either "jobs wait too long in the queue"
        # (needs more workers) or "jobs take too long to run" (needs
        # faster job logic), and those need different fixes.
        avg_queue_wait = (
            db.query(func.avg(func.extract("epoch", Job.started_at - Job.created_at)))
            .filter(Job.user_id == current_user.id, Job.started_at.isnot(None))
            .scalar()
        )

        avg_processing_time = (
            db.query(func.avg(func.extract("epoch", Job.completed_at - Job.started_at)))
            .filter(
                Job.user_id == current_user.id,
                Job.completed_at.isnot(None),
                Job.started_at.isnot(None),
            )
            .scalar()
        )

        return {
            "jobs_by_status": status_counts,
            "success_rate": success_rate,
            "average_attempts_per_completed_job": (
                round(float(avg_attempts), 2) if avg_attempts is not None else None
            ),
            "average_queue_wait_seconds": (
                round(float(avg_queue_wait), 2) if avg_queue_wait is not None else None
            ),
            "average_processing_seconds": (
                round(float(avg_processing_time), 2)
                if avg_processing_time is not None
                else None
            ),
            # Queue depth is infrastructure-wide (Redis lists aren't
            # per-user), not scoped to current_user like the rest above.
            "current_queue_depth": {
                "high": redis_client.llen("job_queue:high"),
                "normal": redis_client.llen("job_queue:normal"),
                "low": redis_client.llen("job_queue:low"),
            },
        }

    finally:
        db.close()