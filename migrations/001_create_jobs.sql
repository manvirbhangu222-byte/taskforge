CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    job_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    CREATE TABLE jobs (
    id UUID PRIMARY KEY,
    job_type VARCHAR(100) NOT NULL,
    payload JSONB NOT NULL,
    idempotency_key VARCHAR(255) UNIQUE,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 1,
    priority VARCHAR(10) NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('high', 'normal', 'low')),
    last_heartbeat TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE INDEX idx_jobs_status_created_at
ON jobs (status, created_at);
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    attempt INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ,
    failed_at TIMESTAMPTZ,
    error_message TEXT
);

CREATE INDEX idx_jobs_status_created_at
ON jobs (status, created_at);