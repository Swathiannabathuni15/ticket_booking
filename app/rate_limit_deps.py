"""FastAPI dependencies wrapping app.services.rate_limit for specific routes.

Reads limits from `settings` on every call (not at import time) so tests can
tighten them per-test with `settings.rate_limit_x = N` and see the new value
take effect immediately.

Every limited response carries RateLimit-Limit / RateLimit-Remaining / RateLimit-Reset, so a client
can slow down BEFORE it is refused; a refusal (429) also has Retry-After.
"""

import redis
from fastapi import Depends, Response

from app.config import settings
from app.deps import get_current_user
from app.models import User
from app.observability import ApiError
from app.redis_client import get_redis
from app.schemas import Credentials, OtpRequest, OtpVerify
from app.services import rate_limit


def _limit(r: redis.Redis, response: Response, key: str, limit: int) -> None:
    try:
        st = rate_limit.check(r, key, limit, settings.rate_limit_window_seconds)
    except rate_limit.RateLimitExceeded as exc:
        raise ApiError(429, "Too many requests, try again later", code="rate_limited", headers=exc.headers()) from None
    response.headers.update(st.headers())


def _limit_identity(r: redis.Redis, response: Response, bucket: str, identity: str, limit: int) -> None:
    _limit(r, response, f"{bucket}:{identity.strip().lower()}", limit)


def limit_login_per_account(body: Credentials, response: Response, r: redis.Redis = Depends(get_redis)) -> None:
    """Per EMAIL, not per IP: guessing one account's password is throttled
    wherever the guesses come from."""
    _limit_identity(r, response, "login", body.email, settings.rate_limit_login_per_window)


def limit_signup_per_email(body: Credentials, response: Response, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, response, "signup", body.email, settings.rate_limit_login_per_window)


def limit_otp_request_per_phone(body: OtpRequest, response: Response, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, response, "otp-req", body.identity, settings.rate_limit_login_per_window)


def limit_otp_verify_per_phone(body: OtpVerify, response: Response, r: redis.Redis = Depends(get_redis)) -> None:
    _limit_identity(r, response, "otp-verify", body.identity, settings.rate_limit_login_per_window)


def limit_payments_per_user(
    response: Response, user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)
) -> None:
    _limit(r, response, f"payment:{user.id}", settings.rate_limit_payment_per_window)


def limit_holds_per_user(
    response: Response, user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)
) -> None:
    _limit(r, response, f"hold:{user.id}", settings.rate_limit_hold_per_window)


def limit_bookings_per_user(
    response: Response, user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)
) -> None:
    _limit(r, response, f"booking:{user.id}", settings.rate_limit_booking_per_window)


def limit_agent_per_user(
    response: Response, user: User = Depends(get_current_user), r: redis.Redis = Depends(get_redis)
) -> None:
    _limit(r, response, f"agent:{user.id}", settings.rate_limit_agent_per_window)
