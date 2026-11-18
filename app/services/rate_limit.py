"""Fixed-window rate limiting backed by Redis.

A fixed window counts hits in buckets of wall-clock time: `INCR` a counter
keyed by action+identity+window-number, and let it expire when the window
ends. It's one round trip per check and needs no extra data structure, which
is why it's the default choice for a simple API.

THE WEAKNESS
    A fixed window admits bursts at the boundary. A client that sends `limit`
    requests at 0:59 and another `limit` at 1:00 gets 2x the limit through in
    under two seconds, because those requests land in two different windows.
    A sliding-window or token-bucket limiter avoids this, at the cost of more
    Redis round trips or a Lua script. Documented here rather than fixed,
    since for login/booking traffic an occasional 2x burst is an acceptable
    trade for the simplicity.

FAIL OPEN
    If Redis is unreachable, `check` lets the request through and logs a
    warning instead of raising. A rate limiter is a defense against abuse;
    it should not become a second way for the whole API to go down whenever
    Redis has a bad moment. (Seat holds make the opposite choice - see
    hold_service - because there the cost of failing open is a double
    booking, not just a bit of extra load.)
"""

import logging
import time

import redis

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int):
        self.retry_after_seconds = retry_after_seconds


def check(r: redis.Redis, key: str, limit: int, window_seconds: int) -> None:
    now = time.time()
    window_id = int(now // window_seconds)
    redis_key = f"rl:{key}:{window_id}"
    try:
        count = r.incr(redis_key)
        if count == 1:
            r.expire(redis_key, window_seconds)
    except redis.exceptions.RedisError:
        logger.warning("Rate limiter: Redis unreachable, failing open for key=%s", key)
        return
    if count > limit:
        retry_after = window_seconds - int(now % window_seconds)
        raise RateLimitExceeded(max(retry_after, 1))
