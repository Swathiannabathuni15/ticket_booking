"""FastAPI dependencies wrapping app.services.rate_limit for specific routes.

Reads limits from `settings` on every call (not at import time) so tests can
tighten them per-test with `settings.rate_limit_x = N` and see the new value
take effect immediately.
"""
from fastapi import Depends, HTTPException, Request, status
import redis

from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.redis_client import get_redis
from app.services import rate_limit


def _too_many_requests(retry_after_seconds: int):
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests, try again later",
        headers={"Retry-After": str(retry_after_seconds)},
    )


def limit_login_per_ip(request: Request, r: redis.Redis = Depends(get_redis)) -> None:
    ip = request.client.host if request.client else "unknown"
    # Keyed by the request path too: signup and login share a limit value
    # but must not share a bucket, or one endpoint's traffic eats the other's quota.
    try:
        rate_limit.check(
            r, f"{request.url.path}:{ip}", settings.rate_limit_login_per_window, settings.rate_limit_window_seconds
        )
    except rate_limit.RateLimitExceeded as exc:
        _too_many_requests(exc.retry_after_seconds)


def limit_holds_per_user(user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)) -> None:
    try:
        rate_limit.check(
            r, f"hold:{user.id}", settings.rate_limit_hold_per_window, settings.rate_limit_window_seconds
        )
    except rate_limit.RateLimitExceeded as exc:
        _too_many_requests(exc.retry_after_seconds)


def limit_bookings_per_user(user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)) -> None:
    try:
        rate_limit.check(
            r, f"booking:{user.id}", settings.rate_limit_booking_per_window, settings.rate_limit_window_seconds
        )
    except rate_limit.RateLimitExceeded as exc:
        _too_many_requests(exc.retry_after_seconds)
