"""The API "gateway" check: every request from a signed-in user is counted in
Redis, per user, per second. Past the limit the request never reaches the
endpoint - the caller gets 429 + Retry-After.

  key   = rl:user-sec:<user id>:<unix second>      (INCR, expires in 2 s)
  limit = settings.rate_limit_user_per_second

IDENTITY, NOT IP ADDRESS. The key is the account (user id from the login
token). Sign-in itself is limited per email / per phone number in
app/rate_limit_deps.py, because there is no user id yet at that point.

What this does NOT cover (honest limits):
  - Anonymous traffic to public pages (movie list, seat map) has no identity
    to count, so it is not limited here.
  - It is a fixed one-second window: a user can briefly send up to 2x the limit
    across a second boundary (see app/services/rate_limit.py).
  - It fails open: if Redis is down the request is allowed (and logged).
Webhooks (/webhooks/*), /health and the API docs are exempt: the bank is
authenticated by its signature, not by a user token.
"""

import json

from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings
from app.redis_client import get_redis
from app.security import decode_access_token
from app.services import rate_limit

EXEMPT_PREFIXES = ("/webhooks/", "/health", "/docs", "/openapi.json", "/redoc")


def _user_id(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"authorization":
            parts = value.decode("latin-1").split(" ", 1)
            if len(parts) == 2 and parts[0].lower() == "bearer":
                return decode_access_token(parts[1])
    return None


class PerUserRateLimit:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http" or scope["method"] == "OPTIONS" or scope["path"].startswith(EXEMPT_PREFIXES):
            return await self.app(scope, receive, send)
        uid = _user_id(scope)
        if uid is None:
            return await self.app(scope, receive, send)

        # tests swap the Redis client through FastAPI's dependency_overrides
        factory = scope["app"].dependency_overrides.get(get_redis, get_redis)
        try:
            await run_in_threadpool(
                rate_limit.check, factory(), f"user-sec:{uid}", settings.rate_limit_user_per_second, 1
            )
        except rate_limit.RateLimitExceeded as exc:
            body = json.dumps({"detail": "Too many requests, slow down"}).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 429,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                        (b"retry-after", str(exc.retry_after_seconds).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
