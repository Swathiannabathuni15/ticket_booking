"""The RQ queue that payment-expiry jobs are pushed onto and pulled from.

Separate from app.redis_client, which serves request-time Redis use (holds,
rate limiting): this one serves a long-running background worker, where
blocking is fine, and `is_async=False` lets tests run a job inline without a
real worker process.
"""

import redis
from rq import Queue

from app.config import settings

QUEUE_NAME = "payments"


def get_queue(connection: redis.Redis | None = None, is_async: bool = True) -> Queue:
    return Queue(QUEUE_NAME, connection=connection or redis.Redis.from_url(settings.redis_url), is_async=is_async)
