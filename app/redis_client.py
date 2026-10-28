"""Redis connection for seat holds.

One connection pool for the process; each request borrows a client from it.
socket_connect_timeout/socket_timeout keep a dead Redis from hanging a
request — callers get a redis.exceptions.RedisError quickly instead of
waiting on a default (much longer) TCP timeout.
"""
import redis

from app.config import settings

_pool = redis.ConnectionPool.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=1,
    socket_timeout=1,
)


def get_redis() -> redis.Redis:
    """FastAPI dependency: a Redis client from the shared pool."""
    return redis.Redis(connection_pool=_pool)
