from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as PoolTimeout

from app.config import settings
from app.gateway_middleware import VERSION_PREFIX, PerUserRateLimit
from app.observability import RequestContext, error_body, install_error_handlers, setup_error_tracking, setup_logging
from app.routers import admin, agent, auth, bookings, catalog, finder, holds, payments, push, showtimes, splits, tickets

setup_logging()
setup_error_tracking()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Schema is created and changed by Alembic migrations now (see migrations/
    # and README "Migrations"), not by the app at startup. Run
    # `alembic upgrade head` before starting the API.
    if settings.environment == "production":
        problems = settings.production_problems()
        if problems:
            raise RuntimeError("Refusing to start in production:\n - " + "\n - ".join(problems))
    if settings.background_jobs == "inprocess":
        from app.jobs import inprocess

        inprocess.start()
    yield


app = FastAPI(title="Ticket Booking API", version="1.0.0", lifespan=lifespan)
install_error_handlers(app)
# Middleware added LATER runs FURTHER OUT. Order, outside in: CORS -> request id/logs -> per-user limit -> app.
# Request id is inside CORS so even our 500 answers carry CORS headers (the browser can read them).
app.add_middleware(PerUserRateLimit)  # a 429 from here still gets a request id and CORS headers
app.add_middleware(RequestContext)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],  # Authorization, Idempotency-Key, Content-Type, X-Request-ID
    # so the UI can show "try again in N s", and quote the request id when something fails
    expose_headers=["Retry-After", "RateLimit-Limit", "RateLimit-Remaining", "RateLimit-Reset", "X-Request-ID"],
)

meta = APIRouter(tags=["meta"])


@meta.get("/config")
def public_config():
    """Settings the website needs to know. test_cards = the checkout may offer the demo
    "card" menu; when false the server ignores whichever card the browser asks for."""
    return {
        "test_cards": settings.gateway_allow_test_methods and settings.payment_provider == "simulated",
        "payment_provider": settings.payment_provider,
    }


@meta.get("/health")
def health():
    return {"status": "ok"}


ROUTERS = [
    admin.router,
    agent.router,
    auth.router,
    catalog.router,
    showtimes.router,
    finder.router,
    bookings.router,
    holds.router,
    payments.router,
    push.router,
    splits.router,
    tickets.router,
    meta,
]
# VERSIONED PATHS. Everything lives under /api/v1 (the website uses these). The same endpoints also
# answer at the old un-versioned paths, hidden from the docs and marked with a `Deprecation: true`
# header, so links already sent, scripts and the bank's webhook URL keep working. A future
# incompatible change goes in /api/v2 while /api/v1 keeps working.
for _r in ROUTERS:
    app.include_router(_r, prefix=VERSION_PREFIX)
for _r in ROUTERS:
    app.include_router(_r, include_in_schema=False)


@app.exception_handler(PoolTimeout)
def _db_pool_exhausted(_req: Request, _exc: PoolTimeout):
    """All DB connections busy for longer than the pool timeout: we are
    overloaded. Say so (503 + Retry-After) instead of an opaque 500."""
    return JSONResponse(
        error_body(503, "Server is busy, try again shortly", "busy"), status_code=503, headers={"Retry-After": "2"}
    )
