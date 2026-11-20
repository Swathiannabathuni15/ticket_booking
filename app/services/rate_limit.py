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
from dataclasses import dataclass

import redis

logger = logging.getLogger(__name__)


class RateLimitExceeded(Exception):
    def __init__(self, retry_after_seconds: int, limit: int = 0):
        self.retry_after_seconds = retry_after_seconds
        self.limit = limit

    def headers(self) -> dict[str, str]:
        return {
            "Retry-After": str(self.retry_after_seconds),
            **RateLimitStatus(self.limit, 0, self.retry_after_seconds).headers(),
        }


@dataclass
class RateLimitStatus:
    limit: int
    remaining: int
    reset_seconds: int  # until this window ends

    def headers(self) -> dict[str, str]:
        """The standard rate-limit headers (IETF draft "RateLimit header fields")."""
        return {
            "RateLimit-Limit": str(self.limit),
            "RateLimit-Remaining": str(self.remaining),
            "RateLimit-Reset": str(self.reset_seconds),
        }


def check(r: redis.Redis, key: str, limit: int, window_seconds: int) -> RateLimitStatus:
    """Count one hit. Raises RateLimitExceeded past the limit; otherwise returns how much is left."""
    now = time.time()
    window_id = int(now // window_seconds)
    redis_key = f"rl:{key}:{window_id}"
    reset = max(1, window_seconds - int(now % window_seconds))
    try:
        count = r.incr(redis_key)
        if count == 1:
            r.expire(redis_key, window_seconds)
    except redis.exceptions.RedisError:
        logger.warning("Rate limiter: Redis unreachable, failing open for key=%s", key)
        return RateLimitStatus(limit, limit, reset)
    if count > limit:
        raise RateLimitExceeded(reset, limit)
    return RateLimitStatus(limit, max(0, limit - count), reset)
