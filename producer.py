from app.queue import enqueue_job


def push_job(job_type: str, payload: dict):
    job = enqueue_job(job_type, payload)
    print(f"Queued job {job['id']}: {job['type']}")


if __name__ == "__main__":
    push_job("send_email", {"to": "test@example.com", "subject": "Hello"})
    push_job("generate_report", {"report_id": 42})
    print("Done.")