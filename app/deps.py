"""Shared FastAPI dependencies (current logged-in user)."""

import redis
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.redis_client import get_redis
from app.security import decode_token
from app.services import session_service

bearer = HTTPBearer(auto_error=False)
_bearer = bearer


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> User:
    unauthorized = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing token")
    if creds is None:
        raise unauthorized
    claims = decode_token(creds.credentials)
    if claims is None or session_service.is_revoked(r, claims):
        raise unauthorized
    user = db.get(User, int(claims["sub"]))
    if user is None:
        raise unauthorized
    return user


def get_current_user_optional(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    r: redis.Redis = Depends(get_redis),
) -> User | None:
    """Same as get_current_user but returns None instead of raising, for
    endpoints (the seat map) that work for anyone but show extra detail
    (which seats are YOUR holds) when logged in."""
    if creds is None:
        return None
    claims = decode_token(creds.credentials)
    if claims is None or session_service.is_revoked(r, claims):
        return None
    return db.get(User, int(claims["sub"]))


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    return user
