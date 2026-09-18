import time
from uuid import uuid4

import redis

from app.config import settings

redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)

RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60


def check_rate_limit(user_id: str) -> bool:
    """
    Sliding-window rate limit using a Redis sorted set: each allowed
    request adds an entry scored by its own timestamp. Before counting,
    entries older than the window are trimmed off — so the count always
    reflects "requests in the last N seconds", not a fixed clock-aligned
    bucket. This avoids the classic fixed-window problem where a user
    could send double their limit right across a window boundary.

    Returns True if this request is allowed (and records it), False if
    the user is over the limit (and does NOT record it).
    """
    key = f"ratelimit:{user_id}"
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SECONDS

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, window_start)  # drop anything outside the window
    pipe.zcard(key)  # count what's left
    _, current_count = pipe.execute()

    if current_count >= RATE_LIMIT_MAX_REQUESTS:
        return False

    # ZADD needs a unique member per entry — two requests landing on the
    # exact same float timestamp would otherwise collide and only count
    # as one, so a random suffix is appended.
    redis_client.zadd(key, {f"{now}-{uuid4()}": now})
    redis_client.expire(key, RATE_LIMIT_WINDOW_SECONDS)
    return True