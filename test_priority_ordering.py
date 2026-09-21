"""
Proves priority ordering: submits several low-priority jobs to occupy
the worker, then submits normal and high priority jobs while the queue
is backed up, and checks they were STARTED in priority order regardless
of submission order.

Run with: python test_priority_ordering.py
(Not a pytest file on purpose — this needs precise timing control that's
easier to reason about as a plain script.)
"""
import time
import uuid

import requests

BASE_URL = "http://127.0.0.1:8000"


def signup_and_login() -> str:
    email = f"priority_test_{uuid.uuid4().hex[:8]}@example.com"
    password = "testpass123"
    requests.post(f"{BASE_URL}/auth/signup", json={"email": email, "password": password})
    login = requests.post(f"{BASE_URL}/auth/login", json={"email": email, "password": password})
    return login.json()["access_token"]


def submit(headers, report_id, priority, work_seconds=3):
    response = requests.post(
        f"{BASE_URL}/jobs",
        json={
            "type": "generate_report",
            "payload": {"report_id": report_id, "simulate_work_seconds": work_seconds},
            "priority": priority,
        },
        headers=headers,
    )
    return response.json()["id"]


if __name__ == "__main__":
    token = signup_and_login()
    headers = {"Authorization": f"Bearer {token}"}

    print("Submitting a long low-priority job to occupy the worker...")
    blocker_id = submit(headers, report_id=0, priority="low", work_seconds=6)

    print("Submitting 2 low, 1 normal, 1 high — all while the worker is busy...")
    ids = {
        "low_a": submit(headers, report_id=1, priority="low", work_seconds=1),
        "low_b": submit(headers, report_id=2, priority="low", work_seconds=1),
        "normal": submit(headers, report_id=3, priority="normal", work_seconds=1),
        "high": submit(headers, report_id=4, priority="high", work_seconds=1),
    }

    print("Waiting for all jobs to complete...")
    all_ids = [blocker_id] + list(ids.values())
    started_at = {}
    deadline = time.time() + 30
    while time.time() < deadline and len(started_at) < len(all_ids):
        for label, job_id in {**{"blocker": blocker_id}, **ids}.items():
            if label in started_at:
                continue
            response = requests.get(f"{BASE_URL}/jobs/{job_id}", headers=headers)
            body = response.json()
            if body.get("started_at"):
                started_at[label] = body["started_at"]
        time.sleep(0.3)

    print("\nActual start order (should be: blocker, then high, normal, low_a/low_b in some order):")
    for label, ts in sorted(started_at.items(), key=lambda item: item[1]):
        print(f"  {ts}  {label}")

    order = [label for label, _ in sorted(started_at.items(), key=lambda item: item[1])]
    high_index = order.index("high")
    normal_index = order.index("normal")
    low_a_index = order.index("low_a")
    low_b_index = order.index("low_b")

    if high_index < normal_index < min(low_a_index, low_b_index):
        print("\nPASS: high started before normal, normal started before both low jobs.")
    else:
        print(f"\nFAIL: expected high < normal < low, got order: {order}")