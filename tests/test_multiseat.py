"""Multi-seat holds and bookings (Stage 2): one order can cover several
seats at once, atomically - see app/services/hold_service.py and
app/services/booking_service.py's module docstrings for the two different
atomicity guarantees (all-or-nothing for Redis holds, fixed lock order for
the Postgres row locks) and why each is needed.
"""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.config import settings
from app.db import SessionLocal
from app.models import Movie, Screen, Showtime, ShowtimeSeat, Theatre, User
from app.services.booking_service import SeatAlreadyBooked, book_seats
from tests.conftest import hold_seats, make_showtime_with_seats, signup_and_login


def test_max_eight_seats_per_request_is_enforced(client):
    h = signup_and_login(client, "a@example.com")
    r = client.post("/holds", json={"seat_ids": list(range(1, 10))}, headers=h)  # 9 seats
    assert r.status_code == 422


def test_duplicate_seat_ids_in_one_request_are_rejected(client):
    h = signup_and_login(client, "a@example.com")
    r = client.post("/holds", json={"seat_ids": [1, 1]}, headers=h)
    assert r.status_code == 422


def test_all_or_nothing_hold_leaves_no_partial_result(client):
    """If even one seat in the request is already held by someone else, the
    whole request must fail - and none of the OTHER seats should end up
    held either, even though they were free."""
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    seats = make_showtime_with_seats(client, rows=1, seats_per_row=3, price=10)["seats"]
    seat_ids = [s["id"] for s in seats]

    hold_seats(client, h2, [seat_ids[1]])  # h2 takes the middle seat

    r = client.post("/holds", json={"seat_ids": seat_ids}, headers=h1)
    assert r.status_code == 409

    # Seats 0 and 2 were free and must still be free - not left half-held by h1.
    assert hold_seats(client, h1, [seat_ids[0]])
    assert hold_seats(client, h1, [seat_ids[2]])


def test_multi_seat_hold_and_book_succeeds_together(client):
    h = signup_and_login(client, "a@example.com")
    seats = make_showtime_with_seats(client, rows=1, seats_per_row=3, price=10)["seats"]
    seat_ids = [s["id"] for s in seats]

    hold_seats(client, h, seat_ids)
    r = client.post("/bookings", json={"seat_ids": seat_ids}, headers=h)
    assert r.status_code == 201
    assert sorted(r.json()["seat_ids"]) == sorted(seat_ids)


def test_booking_fails_entirely_if_any_seat_was_never_held(client):
    h = signup_and_login(client, "a@example.com")
    seats = make_showtime_with_seats(client, rows=1, seats_per_row=3, price=10)["seats"]
    seat_ids = [s["id"] for s in seats]

    hold_seats(client, h, seat_ids[:2])  # only hold 2 of the 3 seats
    r = client.post("/bookings", json={"seat_ids": seat_ids}, headers=h)
    assert r.status_code == 409


def test_seats_from_different_showtimes_cannot_be_booked_together(client):
    h = signup_and_login(client, "a@example.com")
    seat_a = make_showtime_with_seats(client, rows=1, seats_per_row=1, price=10)["seats"][0]["id"]
    seat_b = make_showtime_with_seats(client, rows=1, seats_per_row=1, price=10)["seats"][0]["id"]

    hold_seats(client, h, [seat_a, seat_b])
    r = client.post("/bookings", json={"seat_ids": [seat_a, seat_b]}, headers=h)
    assert r.status_code == 400


def test_seat_map_reports_available_held_held_by_me_and_booked(client):
    h1 = signup_and_login(client, "a@example.com")
    h2 = signup_and_login(client, "b@example.com")
    result = make_showtime_with_seats(client, rows=1, seats_per_row=3, price=10)
    showtime_id, seats = result["showtime_id"], result["seats"]
    free, held_by_other, booked = [s["id"] for s in seats]

    hold_seats(client, h2, [held_by_other])
    hold_seats(client, h1, [booked])
    client.post("/bookings", json={"seat_ids": [booked]}, headers=h1)

    seen_by_h1 = {s["id"]: s["status"] for s in client.get(f"/showtimes/{showtime_id}/seats", headers=h1).json()}
    assert seen_by_h1[free] == "available"
    assert seen_by_h1[held_by_other] == "held"
    assert seen_by_h1[booked] == "booked"

    # Someone with no token at all still sees the map, just without "held_by_me".
    seen_logged_out = {s["id"]: s["status"] for s in client.get(f"/showtimes/{showtime_id}/seats").json()}
    assert seen_logged_out[held_by_other] == "held"


def test_cumulative_holds_on_one_showtime_are_capped(client):
    """The per-request cap (max 8 seat_ids) only bounds one call. This caps
    the TOTAL held at once on a showtime, across however many calls it took
    to get there."""
    h = signup_and_login(client, "a@example.com")
    seats = make_showtime_with_seats(client, rows=2, seats_per_row=5, price=10)["seats"]  # 10 seats
    seat_ids = [s["id"] for s in seats]
    assert settings.max_held_seats_per_showtime == 8

    hold_seats(client, h, seat_ids[:8])  # exactly at the cap
    r = client.post("/holds", json={"seat_ids": [seat_ids[8]]}, headers=h)  # one more -> over
    assert r.status_code == 409


def test_reholding_the_same_seats_does_not_count_twice_against_the_cap(client):
    h = signup_and_login(client, "a@example.com")
    seats = make_showtime_with_seats(client, rows=2, seats_per_row=5, price=10)["seats"]
    seat_ids = [s["id"] for s in seats]
    held_eight = seat_ids[:8]

    hold_seats(client, h, held_eight)
    # Re-holding the exact same 8 seats refreshes them - it must not look
    # like 16 seats held and trip the cap.
    r = client.post("/holds", json={"seat_ids": held_eight}, headers=h)
    assert r.status_code == 201


def _make_showtime_with_n_seats(db, n: int) -> list[int]:
    movie = Movie(title="Multi-seat test", duration_minutes=100)
    theatre = Theatre(name="Test Theatre", city="Testville")
    db.add_all([movie, theatre])
    db.flush()
    screen = Screen(theatre_id=theatre.id, name="Screen 1", layout=[{"row": "A", "seats": ["standard"] * n}])
    db.add(screen)
    db.flush()
    showtime = Showtime(
        movie_id=movie.id,
        screen_id=screen.id,
        starts_at=datetime.now(UTC),
        prices={"standard": 10},
    )
    db.add(showtime)
    db.flush()
    seats = [
        ShowtimeSeat(showtime_id=showtime.id, row_label="A", seat_number=i + 1, seat_type="standard", price=10)
        for i in range(n)
    ]
    db.add_all(seats)
    db.commit()
    return sorted(seat.id for seat in seats)


def _make_two_users(db) -> tuple[int, int]:
    users = [User(email=f"overlap-{i}-{uuid4().hex}@example.com", password_hash="x") for i in range(2)]
    db.add_all(users)
    db.commit()
    return users[0].id, users[1].id


def test_two_overlapping_multi_seat_bookings_never_deadlock_or_double_book(db):
    """A books seats [X, Y], B books seats [Y, Z] at the same moment - they
    share seat Y. Run it repeatedly: a fixed, id-sorted lock order
    (book_seats) means these two transactions only ever queue up behind each
    other, never wait on each other in a cycle. If the lock order were
    wrong (e.g. "lock in request order"), this would occasionally surface as
    a Postgres deadlock error instead of a clean win/lose split."""
    for _ in range(20):
        x, y, z = _make_showtime_with_n_seats(db, 3)
        u1, u2 = _make_two_users(db)

        def book(seat_ids, user_id):
            with SessionLocal() as session:
                try:
                    book_seats(session, user_id, seat_ids)
                    return "won"
                except SeatAlreadyBooked:
                    return "lost"

        with ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(book, [x, y], u1)
            f2 = pool.submit(book, [y, z], u2)
            r1, r2 = f1.result(timeout=10), f2.result(timeout=10)

        # Both touch seat y, so exactly one of the two orders can win.
        assert {r1, r2} == {"won", "lost"}


def test_without_sorted_locking_order_a_deadlock_can_happen():
    """The break-it test: prove the `ORDER BY id` in book_seats() is load-
    bearing, not cosmetic. This locks two seats ONE AT A TIME, in the exact
    (unsorted) order each side asks for - the shape of the bug book_seats()
    avoids - with a barrier forcing both sides to hold their first lock
    before either tries for the second. That's a guaranteed deadlock cycle:
    thread 1 holds x, waits for y; thread 2 holds y, waits for x. Postgres
    itself detects this and kills one side with a "deadlock detected" error
    - we're not simulating that error, we're triggering the real one."""
    x, y, _ = _make_showtime_with_n_seats_standalone(3)

    first_lock_done = threading.Barrier(2, timeout=10)
    outcomes: list[str] = []
    outcomes_lock = threading.Lock()

    def lock_one_at_a_time(first_id: int, second_id: int):
        with SessionLocal() as session:
            try:
                session.scalar(select(ShowtimeSeat).where(ShowtimeSeat.id == first_id).with_for_update())
                first_lock_done.wait()
                session.scalar(select(ShowtimeSeat).where(ShowtimeSeat.id == second_id).with_for_update())
                session.commit()
                outcome = "ok"
            except OperationalError as exc:
                session.rollback()
                outcome = "deadlock" if "deadlock detected" in str(exc).lower() else f"other: {exc}"
            with outcomes_lock:
                outcomes.append(outcome)

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(lock_one_at_a_time, x, y)  # locks x, then wants y
        f2 = pool.submit(lock_one_at_a_time, y, x)  # locks y, then wants x - the cycle
        f1.result(timeout=15)
        f2.result(timeout=15)

    assert "deadlock" in outcomes, outcomes


def _make_showtime_with_n_seats_standalone(n: int) -> list[int]:
    """Same as _make_showtime_with_n_seats but opens its own session and
    commits immediately, so the showtime/seats exist for every thread
    before the deadlock test's two sessions start locking rows."""
    with SessionLocal() as db:
        return _make_showtime_with_n_seats(db, n)


def test_cap_holds_even_when_requests_arrive_at_the_same_instant(client, db):
    """The old check-then-hold let two simultaneous requests both pass the cap. Now the count and the
    write are one atomic Redis step: 12 parallel single-seat requests -> exactly 8 succeed."""
    import threading

    import redis as redis_lib

    from app.models import User
    from app.services import hold_service as holds

    signup_and_login(client, "racer@example.com")
    user_id = db.query(User).filter(User.email == "racer@example.com").one().id
    st = make_showtime_with_seats(client, rows=2, seats_per_row=6, price=10)
    seat_ids = [s["id"] for s in st["seats"]]
    showtime_of = {sid: st["showtime_id"] for sid in seat_ids}
    results, barrier = [], threading.Barrier(len(seat_ids))

    def grab(sid):
        r = redis_lib.Redis(host="localhost", port=6379, db=15, decode_responses=True)
        barrier.wait()
        try:
            holds.acquire_holds(r, [sid], user_id, showtime_of)
            results.append("held")
        except holds.HoldCapExceeded:
            results.append("capped")
        finally:
            r.close()

    threads = [threading.Thread(target=grab, args=(sid,)) for sid in seat_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("held") == settings.max_held_seats_per_showtime == 8
    assert results.count("capped") == 4


def test_released_seats_free_up_the_cap(client):
    h = signup_and_login(client, "a@example.com")
    seat_ids = [s["id"] for s in make_showtime_with_seats(client, rows=2, seats_per_row=5, price=10)["seats"]]
    hold_seats(client, h, seat_ids[:8])
    assert client.delete(f"/holds/{seat_ids[0]}", headers=h).status_code == 204
    assert client.post("/holds", json={"seat_ids": [seat_ids[8]]}, headers=h).status_code == 201
