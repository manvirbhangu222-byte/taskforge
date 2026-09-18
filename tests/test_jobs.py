import time
import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:10]}@example.com"


def create_authed_user() -> str:
    """Signs up a fresh user and returns a bearer token for them."""
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    login = client.post(
        "/auth/login", json={"email": email, "password": "testpass123"}
    )
    return login.json()["access_token"]


def wait_for_status(job_id: str, headers: dict, target: str, timeout: int = 15) -> dict:
    """Polls GET /jobs/{id} until it reaches the target status or times out."""
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


def test_job_completes_end_to_end():
    token = create_authed_user()
    headers = {"Authorization": f"Bearer {token}"}

    response = client.post(
        "/jobs",
        json={
            "type": "generate_report",
            "payload": {"report_id": 1, "simulate_work_seconds": 1},
        },
        headers=headers,
    )
    assert response.status_code == 202
    job_id = response.json()["id"]

    final = wait_for_status(job_id, headers, target="completed")
    assert final["status"] == "completed"
    assert final["attempt"] == 1


def test_idempotency_key_prevents_duplicate():
    token = create_authed_user()
    headers = {"Authorization": f"Bearer {token}"}
    key = f"idem-{uuid.uuid4().hex[:10]}"

    body = {
        "type": "generate_report",
        "payload": {"report_id": 2, "simulate_work_seconds": 1},
        "idempotency_key": key,
    }

    first = client.post("/jobs", json=body, headers=headers)
    assert first.status_code == 202
    first_id = first.json()["id"]

    second = client.post("/jobs", json=body, headers=headers)
    assert second.status_code == 200  # not 202 — it's a duplicate, nothing new queued
    assert second.json()["id"] == first_id
    assert second.json()["status"] == "duplicate"


def test_user_cannot_access_another_users_job():
    token_a = create_authed_user()
    token_b = create_authed_user()
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    response = client.post(
        "/jobs",
        json={"type": "send_email", "payload": {"to": "x@example.com"}},
        headers=headers_a,
    )
    job_id = response.json()["id"]

    # User B tries to read User A's job — must be forbidden.
    forbidden = client.get(f"/jobs/{job_id}", headers=headers_b)
    assert forbidden.status_code == 403


def test_list_jobs_only_shows_own_jobs():
    token_a = create_authed_user()
    token_b = create_authed_user()
    headers_a = {"Authorization": f"Bearer {token_a}"}
    headers_b = {"Authorization": f"Bearer {token_b}"}

    client.post(
        "/jobs",
        json={"type": "send_email", "payload": {"to": "a@example.com"}},
        headers=headers_a,
    )

    # User B's job list should not contain User A's job — check the whole
    # list is empty for a freshly created user with no jobs of their own.
    response = client.get("/jobs", headers=headers_b)
    assert response.status_code == 200
    assert response.json() == []