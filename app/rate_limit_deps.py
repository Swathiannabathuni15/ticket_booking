"""FastAPI dependencies wrapping app.services.rate_limit for specific routes.

Reads limits from `settings` on every call (not at import time) so tests can
tighten them per-test with `settings.rate_limit_x = N` and see the new value
take effect immediately.
"""
from fastapi import Depends, HTTPException, status
import redis

from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.redis_client import get_redis
from app.schemas import Credentials, OtpRequest, OtpVerify
from app.services import rate_limit


def _too_many_requests(retry_after_seconds: int):
    raise HTTPException(
        status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many requests, try again later",
        headers={"Retry-After": str(retry_after_seconds)},
    )


def _limit_identity(r: redis.Redis, bucket: str, identity: str, limit: int) -> None:
    try:
        rate_limit.check(r, f"{bucket}:{identity.strip().lower()}", limit, settings.rate_limit_window_seconds)
    except rate_limit.RateLimitExceeded as exc:
        _too_many_requests(exc.retry_after_seconds)


def limit_login_per_account(body: Credentials, r: redis.Redis = Depends(get_redis)) -> None:
    """Per EMAIL, not per IP: guessing one account's password is throttled
    wherever the guesses come from."""
    _limit_identity(r, "login", body.email, settings.rate_limit_login_per_window)


def limit_signup_per_email(body: Credentials, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, "signup", body.email, settings.rate_limit_login_per_window)


def limit_otp_request_per_phone(body: OtpRequest, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, "otp-req", body.phone, settings.rate_limit_login_per_window)


def limit_otp_verify_per_phone(body: OtpVerify, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, "otp-verify", body.phone, settings.rate_limit_login_per_window)


def limit_payments_per_user(user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)) -> None:
    try:
        rate_limit.check(
            r, f"payment:{user.id}", settings.rate_limit_payment_per_window, settings.rate_limit_window_seconds
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
