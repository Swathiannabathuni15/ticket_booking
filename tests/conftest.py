import os
from pathlib import Path
from uuid import uuid4

# Must be set BEFORE the app is imported, so the app talks to the TEST database.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://postgres:postgres@localhost:5432/ticketdb_test"
)

import pytest  # noqa: E402
import redis  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.redis_client import get_redis  # noqa: E402

TEST_REDIS_DB = 15  # separate from the dev DB (0) so test runs never collide with it

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Build the test schema through the real Alembic migrations, not
    Base.metadata.create_all - this is what proves the migrations actually
    match the models, instead of just trusting that they do."""
    alembic_cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    command.upgrade(alembic_cfg, "head")
    yield
    # Downgrading old migrations can't re-add NOT NULL columns to tables that
    # still hold rows from the last test, so empty everything first.
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE pricing_rules, reminders_sent, push_subscriptions, webhook_events, gateway_charges, payment_attempts, ledger_entries, split_shares, "
                "bill_splits, payments, booking_seats, bookings, showtime_seats, showtimes, screens, theatres, "
                "movies, users RESTART IDENTITY CASCADE"
            )
        )
    command.downgrade(alembic_cfg, "base")


@pytest.fixture(autouse=True)
def _clean_tables():
    """Empty every table before each test so tests don't affect each other."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "TRUNCATE pricing_rules, reminders_sent, push_subscriptions, webhook_events, gateway_charges, payment_attempts, ledger_entries, split_shares, bill_splits, payments, booking_seats, bookings, showtime_seats, showtimes, "
                "screens, theatres, movies, users RESTART IDENTITY CASCADE"
            )
        )
    from app.routers.showtimes import clear_seat_cache

    clear_seat_cache()  # ids restart from 1 after TRUNCATE, so a stale cache would lie
    yield


@pytest.fixture(autouse=True)
def _restore_rate_limit_settings():
    """Tests that tighten a rate limit to make it easy to trip must not leak
    that change into other tests."""
    original = {
        "rate_limit_window_seconds": settings.rate_limit_window_seconds,
        "rate_limit_login_per_window": settings.rate_limit_login_per_window,
        "rate_limit_hold_per_window": settings.rate_limit_hold_per_window,
        "rate_limit_booking_per_window": settings.rate_limit_booking_per_window,
        "rate_limit_share_pay_per_window": settings.rate_limit_share_pay_per_window,
        "rate_limit_user_per_second": settings.rate_limit_user_per_second,
        "rate_limit_payment_per_window": settings.rate_limit_payment_per_window,
    }
    yield
    for key, value in original.items():
        setattr(settings, key, value)


@pytest.fixture(autouse=True)
def _no_real_ai_keys():
    """A key in .env must never make a test call a real AI service."""
    saved = (settings.gemini_api_key, settings.anthropic_api_key, settings.agent_provider)
    settings.gemini_api_key = settings.anthropic_api_key = ""
    settings.agent_provider = "auto"
    yield
    settings.gemini_api_key, settings.anthropic_api_key, settings.agent_provider = saved


@pytest.fixture(autouse=True)
def _clean_redis():
    """Flush the test Redis DB before each test, same reasoning as _clean_tables."""
    r = redis.Redis(host="localhost", port=6379, db=TEST_REDIS_DB, decode_responses=True)
    r.flushdb()
    yield
    r.close()


def _test_redis() -> redis.Redis:
    return redis.Redis(host="localhost", port=6379, db=TEST_REDIS_DB, decode_responses=True)


app.dependency_overrides[get_redis] = _test_redis


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def signup_and_login(client: TestClient, email: str, password: str = "password123") -> dict:
    client.post("/auth/signup", json={"email": email, "password": password})
    token = client.post("/auth/login", json={"email": email, "password": password}).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def admin_headers(client: TestClient, email: str = "admin@example.com") -> dict:
    """Sign up, promote to admin directly in the DB (there's no HTTP endpoint
    for that on purpose - see scripts/make_admin.py), then log in."""
    headers = signup_and_login(client, email)
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == email.lower()).one()
        user.is_admin = True
        db.commit()
    return headers


def make_showtime_with_seats(
    client: TestClient,
    rows: int = 1,
    seats_per_row: int = 1,
    price: float = 10.0,
    starts_at: str = "2030-01-01T18:00:00",
) -> dict:
    """Admin-creates a movie, theatre, screen and showtime in one call, and
    returns the showtime id plus its seats (as returned by GET
    /showtimes/{id}/seats) - the fixture every booking/hold/payment test
    needs to get a bookable seat_id without repeating the catalogue setup.
    """
    headers = admin_headers(client, email=f"catalog-admin-{uuid4().hex}@example.com")
    movie_id = client.post("/movies", json={"title": "Test Movie", "duration_minutes": 120}, headers=headers).json()[
        "id"
    ]
    theatre_id = client.post("/theatres", json={"name": "Test Theatre", "city": "Testville"}, headers=headers).json()[
        "id"
    ]
    row_letters = "ABCDEFGHIJ"
    screen_rows = [{"row": row_letters[i], "seats": ["standard"] * seats_per_row} for i in range(rows)]
    screen_id = client.post(
        "/screens",
        json={"theatre_id": theatre_id, "name": "Screen 1", "rows": screen_rows},
        headers=headers,
    ).json()["id"]
    showtime_id = client.post(
        "/showtimes",
        json={
            "movie_id": movie_id,
            "screen_id": screen_id,
            "starts_at": starts_at,
            "prices": {"standard": price},
        },
        headers=headers,
    ).json()["id"]
    seats = client.get(f"/showtimes/{showtime_id}/seats").json()
    return {"showtime_id": showtime_id, "seats": seats}


def hold_seats(client: TestClient, headers: dict, seat_ids: list[int]) -> dict:
    r = client.post("/holds", json={"seat_ids": seat_ids}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def hold_seat(client: TestClient, headers: dict, seat_id: int) -> dict:
    return hold_seats(client, headers, [seat_id])


def book_seats_pending(client: TestClient, headers: dict, seat_ids: list[int]) -> dict:
    """Hold then book, leaving the booking in pending_payment - the state
    every payment test starts from."""
    hold_seats(client, headers, seat_ids)
    r = client.post("/bookings", json={"seat_ids": seat_ids}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def book_seat_pending(client: TestClient, headers: dict, seat_id: int) -> dict:
    return book_seats_pending(client, headers, [seat_id])
