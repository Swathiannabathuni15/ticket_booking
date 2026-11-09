"""The tests that PROVE the booking is safe under pressure."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.models import Booking, BookingSeat, Movie, Screen, Showtime, ShowtimeSeat, Theatre, User
from app.services.booking_service import ACTIVE_STATUSES, SeatAlreadyBooked, book_seats

N_USERS = 200


def _make_event_with_one_seat(db) -> int:
    movie = Movie(title="Last Seat", duration_minutes=90)
    theatre_screen = Screen(
        theatre_id=_make_theatre(db), name="Screen 1", layout=[{"row": "A", "seats": ["standard"]}]
    )
    db.add_all([movie, theatre_screen])
    db.flush()
    showtime = Showtime(
        movie_id=movie.id, screen_id=theatre_screen.id, starts_at=datetime.now(timezone.utc),
        prices={"standard": 10},
    )
    db.add(showtime)
    db.flush()
    seat = ShowtimeSeat(showtime_id=showtime.id, row_label="A", seat_number=1, seat_type="standard", price=10)
    db.add(seat)
    db.commit()
    return seat.id


def _make_theatre(db) -> int:
    theatre = Theatre(name="Test Theatre", city="Testville")
    db.add(theatre)
    db.flush()
    return theatre.id


def _make_users(db, n: int) -> list[int]:
    users = [User(email=f"u{i}@example.com", password_hash="x") for i in range(n)]
    db.add_all(users)
    db.commit()
    return [u.id for u in users]


def test_200_users_fight_for_one_seat_exactly_one_wins(db):
    seat_id = _make_event_with_one_seat(db)
    user_ids = _make_users(db, N_USERS)

    def try_book(user_id: int) -> str:
        with SessionLocal() as session:  # each thread gets its own DB session
            try:
                book_seats(session, user_id, [seat_id])
                return "won"
            except SeatAlreadyBooked:
                return "lost"

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(try_book, user_ids))

    assert results.count("won") == 1
    assert results.count("lost") == N_USERS - 1

    # book_seats() only gets a booking to pending_payment - that alone is enough
    # to prove the race is won by exactly one person; paying is a separate step
    # (see test_payments.py), covered by Layer 2 (the index) just the same.
    active = db.scalar(
        select(func.count()).select_from(BookingSeat).where(
            BookingSeat.showtime_seat_id == seat_id, BookingSeat.status.in_(ACTIVE_STATUSES)
        )
    )
    assert active == 1


def test_database_constraint_blocks_double_booking_even_without_the_lock(db):
    """Layer 2: bypass the service entirely and insert two active booking_seats by hand."""
    seat_id = _make_event_with_one_seat(db)
    u1, u2 = _make_users(db, 2)
    booking1 = Booking(user_id=u1, showtime_id=db.get(ShowtimeSeat, seat_id).showtime_id, status="pending_payment")
    db.add(booking1)
    db.flush()
    db.add(BookingSeat(booking_id=booking1.id, showtime_seat_id=seat_id, status="pending_payment"))
    db.commit()

    booking2 = Booking(user_id=u2, showtime_id=db.get(ShowtimeSeat, seat_id).showtime_id, status="confirmed")
    db.add(booking2)
    db.flush()
    db.add(BookingSeat(booking_id=booking2.id, showtime_seat_id=seat_id, status="confirmed"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_same_user_same_key_in_parallel_creates_one_booking(db):
    seat_id = _make_event_with_one_seat(db)
    (user_id,) = _make_users(db, 1)

    def try_book(_):
        with SessionLocal() as session:
            booking, created = book_seats(session, user_id, [seat_id], idempotency_key="same-key")
            return booking.id, created

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(try_book, range(20)))

    assert len({booking_id for booking_id, _ in results}) == 1  # everyone got the same booking
    assert [created for _, created in results].count(True) == 1  # only one actually created it
    assert db.scalar(select(func.count()).select_from(Booking)) == 1
