"""Ending a login for real.

A login token is a signed message the browser holds; the server remembers nothing
about it, so deleting it in the browser does not stop a COPY of it working. Logout
therefore puts the token's unique id (`jti`) on a denylist in Redis until the token
would have expired anyway; every request checks the list.

Trade-off, on purpose: if Redis is unreachable we fail OPEN (a revoked token works
again until Redis is back) rather than lock every user out of the site. Tokens made
before this feature have no `jti`, so they cannot be revoked and simply expire.
"""

import logging
import time

import redis

log = logging.getLogger(__name__)


def _key(jti: str) -> str:
    return f"revoked:{jti}"


def revoke(r: redis.Redis, claims: dict) -> None:
    jti = claims.get("jti")
    if not jti:
        return
    ttl = max(1, int(claims.get("exp", 0) - time.time()))
    try:
        r.set(_key(jti), "1", ex=ttl)
    except redis.exceptions.RedisError:
        log.warning("could not record a logout in Redis; the token stays valid until it expires")


def is_revoked(r: redis.Redis, claims: dict) -> bool:
    jti = claims.get("jti")
    if not jti:
        return False
    try:
        return bool(r.exists(_key(jti)))
    except redis.exceptions.RedisError:
        log.warning("Redis unreachable: not checking the logout list (failing open)")
        return False
