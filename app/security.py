"""Password hashing and JWT tokens."""

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd.verify(password, hashed)


def create_access_token(user_id: int, session_start: int | None = None) -> str:
    """A login token. `jti` is its unique id (so a logout can cancel THIS token);
    `sst` is when the original sign-in happened and is carried over by /auth/refresh,
    so a session cannot be extended forever (see settings.max_session_days)."""
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        "jti": secrets.token_hex(16),
        "sst": int(session_start if session_start is not None else now.timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    """All the claims of a valid, unexpired login token, or None."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        int(payload["sub"])
        return payload
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


def decode_access_token(token: str) -> int | None:
    """Return the user id, or None if the token is invalid or expired."""
    claims = decode_token(token)
    return int(claims["sub"]) if claims else None


# --- Signed, expiring share-payment links (split the bill) -----------------
# Separate "purpose" claim so a login token can never be replayed as a share
# link or the other way round. `nonce` ties the link to the DB row.


def create_share_token(share_id: int, nonce: str, ttl_hours: int | None = None) -> str:
    ttl = settings.split_link_ttl_hours if ttl_hours is None else ttl_hours
    expire = datetime.now(UTC) + timedelta(hours=ttl)
    payload = {"sid": share_id, "n": nonce, "purpose": "share", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class ShareTokenExpired(Exception):
    pass


class ShareTokenInvalid(Exception):
    pass


def decode_share_token(token: str) -> tuple[int, str]:
    """Return (share_id, nonce); raise ShareTokenExpired / ShareTokenInvalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise ShareTokenExpired from exc
    except jwt.PyJWTError as exc:
        raise ShareTokenInvalid from exc
    if payload.get("purpose") != "share" or "sid" not in payload or "n" not in payload:
        raise ShareTokenInvalid
    return int(payload["sid"]), str(payload["n"])
