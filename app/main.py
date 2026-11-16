from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import TimeoutError as PoolTimeout
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.gateway_middleware import PerUserRateLimit

from app.routers import admin, agent, auth, bookings, catalog, finder, holds, payments, push, showtimes, splits


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


app = FastAPI(title="Ticket Booking API", version="0.1.0", lifespan=lifespan)
# Added BEFORE CORS so CORS wraps it: a 429 from the gateway still carries CORS headers.
app.add_middleware(PerUserRateLimit)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_origin_regex=settings.cors_origin_regex or None,
    allow_methods=["*"],
    allow_headers=["*"],  # Authorization, Idempotency-Key, Content-Type
    expose_headers=["Retry-After"],  # so the UI can show "try again in N s" on a 429
)
app.include_router(admin.router)
app.include_router(agent.router)
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(showtimes.router)
app.include_router(finder.router)
app.include_router(bookings.router)
app.include_router(holds.router)
app.include_router(payments.router)
app.include_router(push.router)
app.include_router(splits.router)


@app.exception_handler(PoolTimeout)
def _db_pool_exhausted(_req: Request, _exc: PoolTimeout):
    """All DB connections busy for longer than the pool timeout: we are
    overloaded. Say so (503 + Retry-After) instead of an opaque 500."""
    return JSONResponse(
        {"detail": "Server is busy, try again shortly"}, status_code=503, headers={"Retry-After": "2"}
    )


@app.get("/config", tags=["meta"])
def public_config():
    """Settings the website needs to know. test_cards = the checkout may offer the demo
    "card" menu; when false the server ignores whichever card the browser asks for."""
    return {"test_cards": settings.gateway_allow_test_methods}


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
