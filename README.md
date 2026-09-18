# TaskForge

A production-oriented asynchronous job processing platform that allows authenticated clients to submit background jobs and reliably execute them through horizontally scalable workers, with retries, exponential backoff, idempotency, dead-letter handling, priority queuing, rate limiting, and monitoring.

This is a small distributed job-processing platform — not a massive distributed system — built to understand the real engineering problems that arise when work moves from an HTTP request into a background queue.

## Why this exists

Most CRUD APIs stop at "save this to the database." TaskForge exists to answer a harder question: what happens when work can't finish inside a single HTTP request — sending an email, generating a report, processing an upload? That requires a producer/consumer split, and once you have that, a whole set of real distributed-systems problems shows up: what if the worker crashes mid-job? What if the same request arrives twice? What if the queue backs up faster than workers can drain it?

TaskForge is built around answering those questions directly, rather than around a CRUD demo.

## Architecture

```
                         CLIENT
                           |
                           v
                    +-------------+
                    |   FastAPI   |
                    +------+------+
                           |
             +-------------+-------------+
             |             |             |
             v             v             v
          Auth        Rate Limit     Validation
             |             |
             +------+------+
                    v
               PostgreSQL
              /           \
          Users            Jobs
                             |
                             v
                          Redis
                    +------+------+
                    |             |
               Active Queue   Delayed Queue
              (high/normal/low)  (retry_schedule)
                    |             |
                    +------+------+
                           v
              +------------+------------+
              v            v            v
           Worker 1     Worker 2     Worker N
              |            |            |
              +------------+------------+
                           v
                     Job Execution
                           |
                           v
                       PostgreSQL
                           |
                           v
                      Job Result
```

Redis holds fast, temporary operational state: the active queues (per priority), the delayed-retry schedule, and rate-limit counters. PostgreSQL is the durable source of truth: users, jobs, attempts, statuses, timestamps, and results.

## Features

- **JWT authentication** — signup/login, jobs scoped to their owning user, 403 on cross-user access attempts
- **Priority queues** — `high` / `normal` / `low`, implemented via Redis `BRPOP` checking multiple keys in priority order
- **Retries with exponential backoff** — failed jobs are automatically retried (2s, 4s, ... capped at `MAX_ATTEMPTS`), then moved to a dead-letter queue
- **Idempotency keys** — duplicate submissions (same `idempotency_key`) return the original job instead of creating a second one; enforced at the database level via a UNIQUE constraint, not a racy application-level check
- **Worker crash recovery** — workers send a heartbeat while processing; a reaper detects stale `processing` jobs (worker died) and requeues them automatically
- **Rate limiting** — per-user sliding-window limit on job submission, backed by a Redis sorted set
- **Monitoring** — a `/stats` endpoint reporting jobs by status, success rate, average queue-wait time vs. average processing time (tracked separately, since they point to different bottlenecks), and current queue depth per priority
- **Graceful shutdown** — workers catch `SIGTERM`/`SIGINT`, finish their current cycle, and exit cleanly instead of being hard-killed mid-job
- **Health and readiness endpoints** — `/health` (is the process alive) and `/ready` (can it actually reach Postgres and Redis right now)
- **Automated tests** — `pytest` suite covering auth, job lifecycle, idempotency, and cross-user authorization

## Delivery semantics

TaskForge provides **at-least-once** job processing, not exactly-once. If a worker crashes after producing a side effect but before marking the job complete, the reaper will requeue and retry it — meaning a job can, in rare cases, run more than once. Idempotency keys exist specifically to make retried side effects safe to repeat.

## Known limitations

- **Enqueue consistency**: `enqueue_job` writes to PostgreSQL and pushes to Redis as two separate operations, not one atomic transaction. If the process crashes between them, a job record could exist in Postgres with no corresponding Redis message. This isn't solved here — a production system would use a pattern like a transactional outbox or a reconciliation job to close that gap.
- **Redis Lists as the queue primitive**: chosen deliberately to understand queue mechanics directly, not as a production recommendation. A system with stricter durability or delivery-guarantee requirements would likely use a dedicated broker.
- **JWT_SECRET** is a plain development-style secret set via `.env`, not rotated or vaulted — fine for a learning project, not for production.
- **Priority ordering** relies on Redis `BRPOP` checking queue keys in a fixed order; under sustained high-priority load, lower-priority jobs could theoretically starve. Not mitigated here, but understood as a real tradeoff (fair-scheduling/aging are the standard fixes).

## Tech stack

FastAPI, PostgreSQL (via SQLAlchemy), Redis, Docker Compose, `bcrypt` + `pyjwt` for auth, `pytest` for testing.

## Running locally

```bash
docker compose up --build -d
```

This starts four services: `api` (port 8000), `worker`, `postgres`, `redis`. Interactive API docs are available at `http://127.0.0.1:8000/docs`.

## Running tests

```bash
pip install pytest httpx
pytest tests/ -v
```

Tests run against the live Dockerized Postgres/Redis (via their published `localhost` ports), so Docker Compose must be running first.

## API overview

| Endpoint | Description |
|---|---|
| `POST /auth/signup` | Create an account |
| `POST /auth/login` | Get a JWT access token |
| `POST /jobs` | Submit a job (requires auth) |
| `GET /jobs` | List your own jobs |
| `GET /jobs/{id}` | Get one job's status (owner only) |
| `GET /stats` | Job statistics and queue depth |
| `GET /health` | Liveness check |
| `GET /ready` | Readiness check (DB + Redis reachability) |