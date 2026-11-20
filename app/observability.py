"""Seeing problems on a live server: a request id on every request and log line, structured (JSON)
logs, consistent error bodies, and optional error tracking (Sentry).

REQUEST ID. Each request gets an id (the caller's X-Request-ID if it looks sane, else a new one). It is
sent back in the X-Request-ID header and in every error body, and stamped on every log line written
while handling that request. A user who reports "it said error, request id 3f2a..." can be matched to
the exact log lines and Sentry event.

ACCESS LOG. One line per request: method, ROUTE PATTERN (e.g. /pay/share/{token}, never the real URL,
which can contain secrets like share-link tokens or ticket codes), status, duration, user id.

ERRORS always look like {"detail": ..., "code": ..., "request_id": ...}. `detail` is what FastAPI used
before (a message, or a list for validation errors) so existing clients keep working; `code` is a
stable machine-readable name; an unexpected crash returns a plain 500 with no stack trace.
"""

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import settings

CRASH_MESSAGE = "Something went wrong on our side. Please try again; if it keeps happening, quote the request id."
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("http.access")
log = logging.getLogger("app.errors")
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

CODES = {
    400: "bad_request",
    401: "not_signed_in",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "too_large",
    422: "validation_error",
    428: "captcha_required",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
}


# ---------------------------------------------------------------- logging


class _RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy for any log service to search and graph."""

    RESERVED = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "request_id"}

    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        out.update({k: v for k, v in vars(record).items() if k not in self.RESERVED and not k.startswith("_")})
        if record.exc_info:
            out["error"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str)


def setup_logging() -> None:
    fmt = settings.log_format or ("json" if settings.environment == "production" else "text")
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    if fmt == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s [%(request_id)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if not getattr(h, "_ours", False)]
    handler._ours = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(settings.log_level.upper())
    # uvicorn prints its own access line; ours has the route pattern, request id and user, so keep one.
    logging.getLogger("uvicorn.access").disabled = True


def scrub_event(event: dict, _hint: Any = None) -> dict:
    """Sentry before_send: no tokens, cookies, bodies or query strings leave the server."""
    req = event.get("request") or {}
    for key in ("cookies", "data", "query_string"):
        req.pop(key, None)
    headers = req.get("headers") or {}
    for h in list(headers):
        if h.lower() in ("authorization", "cookie", "x-csrf-token"):
            headers[h] = "[removed]"
    event.setdefault("tags", {})["request_id"] = request_id_var.get()
    return event


def setup_error_tracking() -> bool:
    """Send crashes to Sentry if SENTRY_DSN is set. Off (and not even imported) otherwise."""
    if not settings.sentry_dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=settings.environment,
        send_default_pii=False,
        traces_sample_rate=0.0,
        before_send=scrub_event,  # type: ignore[arg-type]  # sentry Event is a TypedDict of the same dict
    )
    return True


# ---------------------------------------------------------------- request id + access log


class RequestContext:
    """Pure ASGI middleware (no buffering, works with streaming): sets the request id, adds the
    X-Request-ID header, and writes the access log line."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)
        scope.setdefault("state", {})["request_id"] = rid
        started = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", rid.encode()))
                if not scope["path"].startswith(("/api/v1/", "/health", "/docs", "/openapi.json", "/redoc")):
                    # The old un-versioned paths still work, but say they are on the way out.
                    message["headers"].append((b"deprecation", b"true"))
            await send(message)

        started_response = False

        async def tracking_send(message: Message):
            nonlocal started_response
            started_response = started_response or message["type"] == "http.response.start"
            await send_wrapper(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception as exc:
            # Answer the crash HERE, while the request id is still set (Starlette's own handler runs
            # outside this middleware, after it). Logged with the stack trace and sent to Sentry.
            log.exception("unhandled error: %s", exc.__class__.__name__)
            if settings.sentry_dsn:
                import sentry_sdk

                sentry_sdk.capture_exception(exc)
            if started_response:
                raise
            body = json.dumps(error_body(500, CRASH_MESSAGE)).encode()
            await send_wrapper(
                {
                    "type": "http.response.start",
                    "status": 500,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
                }
            )
            await send_wrapper({"type": "http.response.body", "body": body})
        finally:
            route = getattr(scope.get("route"), "path", None)
            if route and scope["path"].startswith("/api/v1/") and not route.startswith("/api/v1"):
                route = "/api/v1" + route  # included routers keep their own path; add the version back
            access_log.info(
                "%s %s %s",
                scope["method"],
                route or "(no route)",
                status_code,
                extra={
                    "method": scope["method"],
                    "route": route,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "user_id": scope.get("state", {}).get("user_id"),
                },
            )
            request_id_var.reset(token)


# ---------------------------------------------------------------- error bodies


def error_body(status_code: int, detail: Any, code: str | None = None) -> dict:
    return {"detail": detail, "code": code or CODES.get(status_code, "error"), "request_id": request_id_var.get()}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_req: Request, exc: StarletteHTTPException):
        code = getattr(exc, "code", None)
        return JSONResponse(
            error_body(exc.status_code, exc.detail, code), status_code=exc.status_code, headers=exc.headers
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_req: Request, exc: RequestValidationError):
        return JSONResponse(jsonable_encoder(error_body(422, exc.errors())), status_code=422)

    @app.exception_handler(Exception)
    async def _crash(_req: Request, exc: Exception):
        # Logged with the stack trace (and sent to Sentry if set up); the caller only gets the id.
        log.exception("unhandled error: %s", exc.__class__.__name__)
        return JSONResponse(
            error_body(500, CRASH_MESSAGE),
            status_code=500,
        )


class ApiError(HTTPException):
    """An HTTPException with an explicit machine-readable `code`, e.g. ApiError(428, "...", code="captcha_required")."""

    def __init__(self, status_code: int, detail: Any, *, code: str, headers: dict[str, str] | None = None):
        super().__init__(status_code, detail, headers=headers)
        self.code = code
