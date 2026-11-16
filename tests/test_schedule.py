"""The rolling schedule, and the now-showing / coming-soon split."""
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select

from app.models import Movie, Showtime, ShowtimeSeat
from app.services.schedule_service import SLOTS, ensure_schedule
from tests.conftest import admin_headers

UTC = timezone.utc


def _setup(client, titles=("Alpha", "Beta"), layout_types=("std", "std"), release=None):
    adm = admin_headers(client, "sched-admin@example.com")
    ids = []
    for i, t in enumerate(titles):
        body = {"title": t, "duration_minutes": 100}
        if release and release.get(t):
            body["release_date"] = release[t]
        ids.append(client.post("/movies", json=body, headers=adm).json()["id"])
    th = client.post("/theatres", json={"name": "T", "city": "Pune"}, headers=adm).json()
    rows = [{"row": "AB"[i], "seats": [layout_types[i]] * 4} for i in range(2)]
    sc = client.post("/screens", json={"theatre_id": th["id"], "name": "S", "rows": rows}, headers=adm).json()
    return adm, ids, sc["id"]


# 07:30 in India on 1 June 2030 (the cinemas' clock), so every slot that day is still ahead
NOW = datetime(2030, 6, 1, 2, 0, tzinfo=UTC)


def test_creates_the_missing_showtimes_once_with_all_their_seats(client, db):
    _setup(client)
    n = ensure_schedule(db, days_ahead=2, now=NOW)
    assert n == 3 * len(SLOTS)                                           # 3 days x 4 slots on one screen
    assert ensure_schedule(db, days_ahead=2, now=NOW) == 0               # nothing doubles up
    assert db.scalar(select(func.count()).select_from(Showtime)) == n
    assert db.scalar(select(func.count()).select_from(ShowtimeSeat)) == n * 8   # 2 rows x 4 seats each


def test_slots_already_in_the_past_are_skipped(client, db):
    _setup(client)
    midday = datetime(2030, 6, 1, 6, 30, tzinfo=UTC)                     # 12:00 in India: the 10:30 show has gone
    assert ensure_schedule(db, days_ahead=0, now=midday) == len(SLOTS) - 1


def test_a_movie_is_not_shown_before_its_release_date(client, db):
    _, (alpha, beta), _ = _setup(client, release={"Beta": "2030-06-03"})
    ensure_schedule(db, days_ahead=3, now=NOW)
    first_beta = db.scalar(select(func.min(Showtime.starts_at)).where(Showtime.movie_id == beta))
    assert first_beta is not None and first_beta.date() >= date(2030, 6, 3)
    assert db.scalar(select(func.count()).select_from(Showtime).where(Showtime.movie_id == alpha)) > 0


def test_it_does_not_double_book_a_screen_with_a_show_an_admin_made(client, db):
    adm, (alpha, _), screen = _setup(client)
    client.post("/showtimes", json={"movie_id": alpha, "screen_id": screen, "starts_at": "2030-06-01T14:30:00+05:30",
                                    "prices": {"std": 77}}, headers=adm)
    created = ensure_schedule(db, days_ahead=0, now=NOW)
    assert created == len(SLOTS) - 1                                     # the 14:00 slot is within 3 hours of 14:30
    times = sorted(t.astimezone(timezone(timedelta(hours=5, minutes=30))).strftime("%H:%M")
                   for t in db.scalars(select(Showtime.starts_at)))
    assert "14:00" not in times and "14:30" in times


def test_new_shows_copy_the_prices_of_the_screens_latest_show(client, db):
    adm, (alpha, _), screen = _setup(client)
    client.post("/showtimes", json={"movie_id": alpha, "screen_id": screen, "starts_at": "2030-05-01T10:00:00+05:30",
                                    "prices": {"std": 77}}, headers=adm)
    ensure_schedule(db, days_ahead=0, now=NOW)
    prices = {float(p) for p in db.scalars(select(ShowtimeSeat.price).join(Showtime).where(Showtime.starts_at > NOW))}
    assert prices == {77.0}


def test_default_prices_are_used_when_a_screen_has_never_had_a_show(client, db):
    _setup(client, layout_types=("gold", "standard"))
    ensure_schedule(db, days_ahead=0, now=NOW)
    got = {(t, float(p)) for t, p in db.execute(select(ShowtimeSeat.seat_type, ShowtimeSeat.price))}
    assert got == {("gold", 250.0), ("standard", 180.0)}


def test_nothing_happens_with_no_screens_or_no_movies(db):
    assert ensure_schedule(db, 2, now=NOW) == 0


# ---- now showing / coming soon ------------------------------------------------

def test_movies_report_their_status_and_split_by_release_date(client):
    adm = admin_headers(client, "status-admin@example.com")
    future = (date.today() + timedelta(days=10)).isoformat()
    now_id = client.post("/movies", json={"title": "Out Now", "duration_minutes": 90}, headers=adm).json()["id"]
    soon = client.post("/movies", json={"title": "Soon", "duration_minutes": 90, "release_date": future}, headers=adm).json()
    assert soon["status"] == "coming_soon" and soon["release_date"] == future
    assert [m["title"] for m in client.get("/movies?status=coming_soon").json()] == ["Soon"]
    assert [m["title"] for m in client.get("/movies?status=now_showing").json()] == ["Out Now"]
    assert {m["title"] for m in client.get("/movies").json()} == {"Out Now", "Soon"}            # no filter: everything, as before
    assert client.get("/movies?status=bogus").status_code == 422
    # an admin can release it: set the date to today
    client.patch(f"/movies/{soon['id']}", json={"release_date": date.today().isoformat()}, headers=adm)
    assert client.get("/movies?status=coming_soon").json() == []
    assert now_id


def test_a_city_only_lists_movies_with_a_show_still_to_come(client):
    adm, (alpha, beta), screen = _setup(client)
    client.post("/showtimes", json={"movie_id": alpha, "screen_id": screen, "starts_at": "2030-06-01T18:00:00+05:30",
                                    "prices": {"std": 10}}, headers=adm)
    client.post("/showtimes", json={"movie_id": beta, "screen_id": screen, "starts_at": "2020-06-01T18:00:00+05:30",
                                    "prices": {"std": 10}}, headers=adm)           # long gone
    assert [m["title"] for m in client.get("/movies?city=Pune").json()] == ["Alpha"]


def test_one_movie_can_be_fetched_by_id(client):
    adm = admin_headers(client, "one-admin@example.com")
    m = client.post("/movies", json={"title": "Solo", "duration_minutes": 90}, headers=adm).json()
    assert client.get(f"/movies/{m['id']}").json()["title"] == "Solo"
    assert client.get("/movies/99999").status_code == 404
