import logging
import secrets

import redis
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.rate_limit_deps import (
    limit_login_per_account, limit_otp_request_per_phone, limit_otp_verify_per_phone, limit_signup_per_email,
)
from app.config import settings
from app.redis_client import get_redis
from app.schemas import Credentials, OtpRequest, OtpRequestOut, OtpVerify, TokenOut, UserOut
from app.services import rate_limit
from app.security import create_access_token, hash_password, verify_password

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
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")
    return user


@router.post("/auth/login", response_model=TokenOut, dependencies=[Depends(limit_login_per_account)])
def login(body: Credentials, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    # Same message for "no such user" and "wrong password": don't leak which emails exist.
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong email or password")
    return TokenOut(access_token=create_access_token(user.id))


@router.post("/auth/refresh", response_model=TokenOut)
def refresh(user: User = Depends(get_current_user)):
    """Swap a still-valid token for a fresh one (a sliding session): anyone who
    keeps using the site stays signed in; a token that already expired cannot be
    refreshed, so a stolen old token does not live forever."""
    return TokenOut(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


# ---- mobile OTP sign-in -------------------------------------------------
# Demo-grade: no SMS provider. In otp_dev_mode the code comes back in the
# response (and the log) so the flow can be tried; with it off, the code is
# only generated and stored, and a real SMS sender would have to be added
# where marked. Codes: 6 digits, single use, 5 minutes, 5 wrong guesses max.
log = logging.getLogger("auth.otp")


def _norm(phone: str) -> str:
    return "+" + phone.lstrip("+")


@router.post("/auth/otp/request", response_model=OtpRequestOut, dependencies=[Depends(limit_otp_request_per_phone)])
def otp_request(body: OtpRequest, r: redis.Redis = Depends(get_redis)):
    phone = _norm(body.phone)
    try:
        # at most 3 codes per phone per minute: stops SMS-bombing someone's number
        rate_limit.check(r, f"otp-req:{phone}", 3, 60)
        code = f"{secrets.randbelow(1_000_000):06d}"
        r.set(f"otp:{phone}", code, ex=settings.otp_ttl_seconds)
        r.delete(f"otp-tries:{phone}")
    except rate_limit.RateLimitExceeded as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many codes requested, try again later",
                            headers={"Retry-After": str(exc.retry_after_seconds)})
    except redis.exceptions.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in by phone is temporarily unavailable")
    # TODO real deployment: send `code` by SMS here and keep otp_dev_mode off.
    if settings.otp_dev_mode:
        log.info("DEV OTP for %s: %s", phone, code)
    return OtpRequestOut(expires_in_seconds=settings.otp_ttl_seconds, dev_otp=code if settings.otp_dev_mode else None)


@router.post("/auth/otp/verify", response_model=TokenOut, dependencies=[Depends(limit_otp_verify_per_phone)])
def otp_verify(body: OtpVerify, db: Session = Depends(get_db), r: redis.Redis = Depends(get_redis)):
    phone = _norm(body.phone)
    bad = HTTPException(status.HTTP_401_UNAUTHORIZED, "Wrong or expired code")
    try:
        tries = r.incr(f"otp-tries:{phone}")
        r.expire(f"otp-tries:{phone}", settings.otp_ttl_seconds)
        if tries > settings.otp_max_attempts:
            r.delete(f"otp:{phone}")  # too many guesses: burn the code
            raise bad
        stored = r.get(f"otp:{phone}")
        if stored is None or not secrets.compare_digest(stored, body.code):
            raise bad
        r.delete(f"otp:{phone}", f"otp-tries:{phone}")  # single use
    except redis.exceptions.RedisError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Sign-in by phone is temporarily unavailable")
    user = db.scalar(select(User).where(User.phone == phone))
    if user is None:
        # An unguessable password: phone accounts sign in by OTP only.
        user = User(phone=phone, password_hash=hash_password(secrets.token_urlsafe(32)))
        db.add(user)
        try:
            db.commit()
        except IntegrityError:  # two first-time verifies racing
            db.rollback()
            user = db.scalar(select(User).where(User.phone == phone))
    return TokenOut(access_token=create_access_token(user.id))
