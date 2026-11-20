import logging
import secrets
import time

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db, required
from app.deps import bearer, get_current_user
from app.models import User
from app.rate_limit_deps import (
    limit_login_per_account,
    limit_otp_request_per_phone,
    limit_otp_verify_per_phone,
    limit_signup_per_email,
)
from app.redis_client import get_redis
from app.schemas import Credentials, OtpRequest, OtpRequestOut, OtpVerify, TokenOut, UserOut
from app.security import create_access_token, decode_token, hash_password, verify_password
from app.services import otp_delivery, rate_limit, session_service

router = APIRouter(tags=["auth"])


@router.post(
    "/auth/signup",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_signup_per_email)],
)
def signup(body: Credentials, db: Session = Depends(get_db)):
    email = body.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    user = User(email=email, password_hash=hash_password(body.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:  # two signups with the same email at the same moment
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    return user


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(limit_login_per_account)])
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Same message for "no such user" and "wrong password": don't leak which emails exist.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(
    creds: HTTPAuthorizationCredentials | None = Depends(bearer),
    user: User = Depends(get_current_user),
):
    """Swap a still-valid token for a fresh one (a sliding session): anyone who keeps using
    the site stays signed in. But never past `max_session_days` from the ORIGINAL sign-in,
    and a token that already expired or was logged out cannot be refreshed, so a stolen
    token does not live forever."""
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not signed in")
    claims = decode_token(creds.credentials) or {}
    started = int(claims.get("sst", time.time()))
    if time.time() - started > settings.max_session_days * 86400:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "This session is too old. Please sign in again.")
    return TokenOut(access_token=create_access_token(user.id, session_start=started))


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(creds: HTTPAuthorizationCredentials | None = Depends(bearer), r: redis.Redis = Depends(get_redis)):
    """End THIS login for real: the token stops working everywhere, not just in this browser.
    Always succeeds (a missing or already-dead token is fine), so signing out never gets stuck."""
    claims = decode_token(creds.credentials) if creds else None
    if claims:
        session_service.revoke(r, claims)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---- one-time-code sign-in (phone by SMS, or email) -----------------------
# Codes: 6 digits, single use, 5 minutes, 5 wrong guesses max, 3 codes/minute per
# phone or email. Delivery: SMS (Twilio / MSG91) or email (SMTP) when configured
# (see app/services/otp_delivery.py). With nothing configured and otp_dev_mode on,
# the code comes back in the response so the demo works - never do that in production.
log = logging.getLogger("auth.otp")


@router.post("/auth/otp/request", response_model=OtpRequestOut, dependencies=[Depends(limit_otp_request_per_phone)])
def otp_request(body: OtpRequest, r: redis.Redis = Depends(get_redis)):
    who = body.identity
    is_email = body.email is not None
    can_send = otp_delivery.email_configured() if is_email else otp_delivery.sms_configured()
    if not can_send and not settings.otp_dev_mode:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Sending codes by {'email' if is_email else 'SMS'} isn't set up on this server",
        )
    try:
        # at most 3 codes per identity per minute: stops flooding someone's phone or inbox
        rate_limit.check(r, f"otp-send:{who}", 3, 60)
        code = f"{secrets.randbelow(1_000_000):06d}"
        r.set(f"otp:{who}", code, ex=settings.otp_ttl_seconds)
        r.delete(f"otp-tries:{who}")
    except rate_limit.RateLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many codes requested, try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except redis.exceptions.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in by code is temporarily unavailable") from None

    channel = "screen"
    if can_send:
        try:
            (otp_delivery.send_email if is_email else otp_delivery.send_sms)(who, code)
            channel = "email" if is_email else "sms"
        except otp_delivery.DeliveryFailed:
            r.delete(f"otp:{who}")  # nothing was delivered, so the code is useless
            if not settings.otp_dev_mode:
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, "We couldn't send the code. Please try again."
                ) from None
    if settings.otp_dev_mode and channel == "screen":
        r.set(f"otp:{who}", code, ex=settings.otp_ttl_seconds)
        log.info("DEV code for %s: %s", who, code)
    return OtpRequestOut(
        channel=channel,
        destination=otp_delivery.mask(who),
        expires_in_seconds=settings.otp_ttl_seconds,
        dev_otp=code if (settings.otp_dev_mode and channel == "screen") else None,
    )


@router.post("/auth/otp/verify", response_model=TokenOut, dependencies=[Depends(limit_otp_verify_per_phone)])
def otp_verify(body: OtpVerify, db: Session = Depends(get_db), r: redis.Redis = Depends(get_redis)):
    who = body.identity
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or expired code")
    try:
        tries = r.incr(f"otp-tries:{who}")
        r.expire(f"otp-tries:{who}", settings.otp_ttl_seconds)
        if tries > settings.otp_max_attempts:
            r.delete(f"otp:{who}")  # too many guesses: burn the code
            raise bad
        stored = r.get(f"otp:{who}")
        if stored is None or not secrets.compare_digest(stored, body.code):
            raise bad
        r.delete(f"otp:{who}", f"otp-tries:{who}")  # single use
    except redis.exceptions.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in by code is temporarily unavailable") from None
    column = User.email if body.email is not None else User.phone
    user = db.scalar(select(User).where(column == who))
    if user is not None and user.is_admin:
        # A code sent to an inbox/phone is a weaker proof than a password; never let it open an admin account.
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin accounts must sign in with a password")
    if user is None:
        # An unguessable password: code-only accounts sign in with a code.
        user = User(password_hash=hash_password(secrets.token_urlsafe(32)))
        if body.email is not None:
            user.email = who
        else:
            user.phone = who
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # two first-time verifies racing
            db.rollback()
            user = required(db.scalar(select(User).where(column == who)), "user")
    return TokenOut(access_token=create_access_token(user.id))
