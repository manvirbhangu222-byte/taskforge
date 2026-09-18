import uuid

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def unique_email() -> str:
    return f"test_{uuid.uuid4().hex[:10]}@example.com"


def test_signup_creates_user():
    response = client.post(
        "/auth/signup",
        json={"email": unique_email(), "password": "testpass123"},
    )
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert "email" in body


def test_signup_duplicate_email_returns_409():
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    response = client.post(
        "/auth/signup", json={"email": email, "password": "testpass123"}
    )
    assert response.status_code == 409


def test_login_with_wrong_password_returns_401():
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    response = client.post(
        "/auth/login", json={"email": email, "password": "wrongpassword"}
    )
    assert response.status_code == 401


def test_login_returns_token():
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    response = client.post(
        "/auth/login", json={"email": email, "password": "testpass123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()


def test_create_job_without_auth_returns_401():
    response = client.post(
        "/jobs",
        json={"type": "send_email", "payload": {"to": "x@example.com"}},
    )
    assert response.status_code == 401


def test_create_job_with_invalid_priority_returns_422():
    email = unique_email()
    client.post("/auth/signup", json={"email": email, "password": "testpass123"})
    login = client.post(
        "/auth/login", json={"email": email, "password": "testpass123"}
    )
    token = login.json()["access_token"]

    response = client.post(
        "/jobs",
        json={
            "type": "send_email",
            "payload": {"to": "x@example.com"},
            "priority": "urgent",  # not a valid value — only high/normal/low
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422