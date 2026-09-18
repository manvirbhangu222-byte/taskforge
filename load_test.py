"""
Load test for TaskForge. Logs in, fires a batch of jobs rapidly across
mixed priorities, then polls /stats to watch the queue drain.

Run with: python load_test.py
"""
import random
import time

import requests

BASE_URL = "http://127.0.0.1:8000"
EMAIL = "you@test.com"
PASSWORD = "testpass123"
NUM_JOBS = 30
PRIORITIES = ["high", "normal", "normal", "low", "low"]  # weighted: mostly normal/low


def login() -> str:
    response = requests.post(
        f"{BASE_URL}/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
    )
    response.raise_for_status()
    return response.json()["access_token"]


def submit_jobs(token: str, count: int) -> float:
    headers = {"Authorization": f"Bearer {token}"}
    start = time.time()

    for i in range(count):
        priority = random.choice(PRIORITIES)
        payload = {
            "type": "generate_report",
            "payload": {"report_id": i, "simulate_work_seconds": 1},
            "priority": priority,
        }
        response = requests.post(f"{BASE_URL}/jobs", json=payload, headers=headers)
        if response.status_code == 429:
            print(f"  [job {i}] rate limited — backing off 1s")
            time.sleep(1)
        elif response.status_code not in (200, 202):
            print(f"  [job {i}] unexpected status {response.status_code}: {response.text}")

    elapsed = time.time() - start
    return elapsed


def watch_drain(token: str, duration_seconds: int = 30):
    headers = {"Authorization": f"Bearer {token}"}
    print("\nWatching queue drain (checking every 3s):")

    for _ in range(duration_seconds // 3):
        response = requests.get(f"{BASE_URL}/stats", headers=headers)
        stats = response.json()
        depth = stats["current_queue_depth"]
        total_depth = depth["high"] + depth["normal"] + depth["low"]
        print(
            f"  queue depth: high={depth['high']} normal={depth['normal']} "
            f"low={depth['low']} (total={total_depth}) | "
            f"completed so far: {stats['jobs_by_status'].get('completed', 0)}"
        )
        if total_depth == 0:
            print("  Queue empty.")
            break
        time.sleep(3)


if __name__ == "__main__":
    print("Logging in...")
    token = login()

    print(f"Submitting {NUM_JOBS} jobs...")
    submit_elapsed = submit_jobs(token, NUM_JOBS)
    print(f"Submission took {submit_elapsed:.2f}s ({NUM_JOBS / submit_elapsed:.1f} jobs/sec)")

    watch_drain(token)

    print("\nFinal stats:")
    headers = {"Authorization": f"Bearer {token}"}
    final_stats = requests.get(f"{BASE_URL}/stats", headers=headers).json()
    for key, value in final_stats.items():
        print(f"  {key}: {value}")