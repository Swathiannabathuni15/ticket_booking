"""Password hashing and JWT tokens."""
from datetime import datetime, timedelta, timezone

import jwt
from passlib.context import CryptContext

from app.config import settings

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _pwd.verify(password, hashed)


def create_access_token(user_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {"sub": str(user_id), "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> int | None:
    """Return the user id, or None if the token is invalid or expired."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


# --- Signed, expiring share-payment links (split the bill) -----------------
# Separate "purpose" claim so a login token can never be replayed as a share
# link or the other way round. `nonce` ties the link to the DB row.

def create_share_token(share_id: int, nonce: str, ttl_hours: int | None = None) -> str:
    ttl = settings.split_link_ttl_hours if ttl_hours is None else ttl_hours
    expire = datetime.now(timezone.utc) + timedelta(hours=ttl)
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
