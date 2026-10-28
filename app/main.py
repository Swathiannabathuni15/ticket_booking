from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import auth, bookings, events, holds, payments


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Schema is created and changed by Alembic migrations now (see migrations/
    # and README "Migrations"), not by the app at startup. Run
    # `alembic upgrade head` before starting the API.
    yield


app = FastAPI(title="Ticket Booking API", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(bookings.router)
app.include_router(holds.router)
app.include_router(payments.router)


@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok"}
