"""Pricing rules (weekend / evening prices) and the admin dashboard numbers."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models import PricingRule, Showtime
from app.services import pricing_service, stats_service
from tests.conftest import admin_headers, book_seat_pending, make_showtime_with_seats, signup_and_login

IST = ZoneInfo("Asia/Kolkata")


def _rule(**kw) -> PricingRule:
    base = dict(id=1, name="Rule", days=[], start_minute=None, end_minute=None, seat_type=None, percent=10, active=True)
    return PricingRule(**{**base, **kw})


def _at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=IST)


SAT_EVENING = _at(2030, 1, 5, 19)  # a Saturday
TUE_MORNING = _at(2030, 1, 1, 10)  # a Tuesday


# ---------- the rule engine ----------


def test_weekend_and_evening_rules_add_up():
    weekend = _rule(id=1, name="Weekend", days=[5, 6], percent=20)
    evening = _rule(id=2, name="Evening", start_minute=18 * 60, end_minute=23 * 60, percent=10)
    p = pricing_service.apply_rules([weekend, evening], SAT_EVENING, {"gold": 200, "silver": 150})
    assert p.prices == {"gold": 260, "silver": 195}  # +30%
    assert p.note == "Weekend +20%, Evening +10%"
    # A weekday morning: nothing applies, base prices, no note.
    p = pricing_service.apply_rules([weekend, evening], TUE_MORNING, {"gold": 200})
    assert p.prices == {"gold": 200} and p.note is None


def test_rule_for_one_seat_type_and_a_discount():
    morning = _rule(name="Morning", start_minute=0, end_minute=12 * 60, percent=-15)
    recliner = _rule(id=2, name="Recliner weekend", days=[5, 6], seat_type="recliner", percent=25)
    p = pricing_service.apply_rules([morning, recliner], _at(2030, 1, 5, 9), {"recliner": 400, "gold": 200})
    assert p.prices == {"recliner": 440, "gold": 170}  # recliner +10% (25-15), gold -15%


def test_time_window_can_wrap_past_midnight_and_uses_local_time():
    late = _rule(name="Late night", start_minute=22 * 60, end_minute=2 * 60, percent=-20)
    assert pricing_service.matches(late, _at(2030, 1, 1, 23, 30))
    assert pricing_service.matches(late, _at(2030, 1, 2, 1, 0))
    assert not pricing_service.matches(late, _at(2030, 1, 1, 20))
    # 17:00 UTC is 22:30 in India: the rule follows the cinema's clock, not UTC.
    assert pricing_service.matches(late, datetime(2030, 1, 1, 17, 0, tzinfo=UTC))


def test_total_change_is_capped_and_inactive_rules_do_nothing():
    rules = [_rule(id=i, percent=-50) for i in range(1, 4)] + [_rule(id=9, percent=500, active=False)]
    assert pricing_service.apply_rules(rules, TUE_MORNING, {"gold": 100}).prices == {"gold": 10}  # -90% floor


# ---------- through the API ----------


def test_admin_rules_change_new_showtimes_only(client, db):
    adm = admin_headers(client, "pricing-admin@example.com")
    before = make_showtime_with_seats(client, price=100, starts_at="2030-01-05T19:00:00")  # Saturday evening
    r = client.post("/admin/pricing-rules", json={"name": "Weekend", "days": [5, 6], "percent": 20}, headers=adm)
    assert r.status_code == 201, r.text
    assert r.json()["summary"] == "Sat, Sun · any time · all seats · +20%"

    after = make_showtime_with_seats(client, price=100, starts_at="2030-01-05T19:00:00")
    assert before["seats"][0]["price"] == 100  # an already-created show keeps its prices
    assert after["seats"][0]["price"] == 120
    show = db.get(Showtime, after["showtime_id"])
    assert show.prices == {"standard": 100} and show.price_note == "Weekend +20%"  # the base is kept
    assert client.get(f"/showtimes/{after['showtime_id']}").json()["price_note"] == "Weekend +20%"

    weekday = make_showtime_with_seats(client, price=100, starts_at="2030-01-01T19:00:00")
    assert weekday["seats"][0]["price"] == 100


def test_rule_validation_and_admin_only(client):
    adm = admin_headers(client, "pricing-admin@example.com")
    bad = [
        {"name": "x", "percent": 0},
        {"name": "x", "percent": 500},
        {"name": "x", "percent": 10, "days": [7]},
        {"name": "x", "percent": 10, "start_minute": 600},
    ]
    for body in bad:
        assert client.post("/admin/pricing-rules", json=body, headers=adm).status_code == 422, body
    user = signup_and_login(client, "someone@example.com")
    assert client.post("/admin/pricing-rules", json={"name": "x", "percent": 10}, headers=user).status_code == 403
    assert client.get("/admin/dashboard", headers=user).status_code == 403


def test_edit_disable_delete_and_preview(client):
    adm = admin_headers(client, "pricing-admin@example.com")
    st = make_showtime_with_seats(client, price=100)
    screen_id = client.get(f"/showtimes/{st['showtime_id']}").json()["screen_id"]
    rid = client.post("/admin/pricing-rules", json={"name": "Evening", "percent": 10}, headers=adm).json()["id"]

    p = client.get(f"/admin/pricing-rules/preview?screen_id={screen_id}&starts_at=2030-01-01T19:00:00", headers=adm)
    assert p.json() == {"base": {"standard": 100}, "final": {"standard": 110}, "note": "Evening +10%"}

    r = client.put(f"/admin/pricing-rules/{rid}", json={"name": "Evening", "percent": 10, "active": False}, headers=adm)
    assert r.status_code == 200 and r.json()["active"] is False
    p = client.get(f"/admin/pricing-rules/preview?screen_id={screen_id}&starts_at=2030-01-01T19:00:00", headers=adm)
    assert p.json()["final"] == {"standard": 100}

    assert client.delete(f"/admin/pricing-rules/{rid}", headers=adm).status_code == 204
    assert client.get("/admin/pricing-rules", headers=adm).json() == []


def test_automatic_schedule_does_not_compound_rules(client, db):
    """The schedule copies the last show's BASE prices; copying the final ones would raise them every time."""
    from app.services.schedule_service import prices_for

    adm = admin_headers(client, "pricing-admin@example.com")
    client.post("/admin/pricing-rules", json={"name": "Every show", "percent": 50}, headers=adm)
    st = make_showtime_with_seats(client, price=100)
    show = db.get(Showtime, st["showtime_id"])
    assert st["seats"][0]["price"] == 150
    assert prices_for(db, show.screen) == {"standard": 100}


# ---------- dashboard ----------


def _pay(client, h, seat_id, key):
    b = book_seat_pending(client, h, seat_id)
    r = client.post("/payments", json={"booking_id": b["id"]}, headers={**h, "Idempotency-Key": key})
    assert r.status_code == 201, r.text
    return b


def test_dashboard_counts_only_paid_bookings(client):
    adm = admin_headers(client, "dash-admin@example.com")
    tomorrow = (datetime.now(IST) + timedelta(days=1)).replace(hour=18, minute=0, second=0, microsecond=0)
    st = make_showtime_with_seats(client, rows=1, seats_per_row=4, price=150, starts_at=tomorrow.isoformat())
    seats = [s["id"] for s in st["seats"]]
    h = signup_and_login(client, "fan@example.com")
    _pay(client, h, seats[0], "k1")
    _pay(client, h, seats[1], "k2")
    book_seat_pending(client, h, seats[2])  # started, not paid

    r = client.get("/admin/dashboard?days=7", headers=adm)
    assert r.status_code == 200, r.text
    d = r.json()
    t = d["totals"]
    assert t["revenue_cents"] == 30000 and t["bookings"] == 2 and t["tickets"] == 2
    assert t["avg_ticket_cents"] == 15000
    assert t["started"] == 3 and t["pending"] == 1
    assert len(d["per_day"]) == 7 and d["per_day"][-1]["revenue_cents"] == 30000
    assert sum(x["bookings"] for x in d["per_day"]) == 2
    show = next(s for s in d["shows"] if s["showtime_id"] == st["showtime_id"])
    assert show["seats_total"] == 4 and show["seats_sold"] == 2 and show["occupancy_percent"] == 50.0
    assert show["revenue_cents"] == 30000
    assert d["top_movies"][0] == {"title": "Test Movie", "tickets": 2, "revenue_cents": 30000}


def test_dashboard_day_boundary_is_the_cinemas_clock(client, db):
    """Paid at 23:30 India time (18:00 UTC) belongs to THAT India day, not the next/previous UTC day."""
    from app.models import Booking

    admin_headers(client, "dash-admin@example.com")
    st = make_showtime_with_seats(client, rows=1, seats_per_row=2, price=100)
    h = signup_and_login(client, "fan@example.com")
    b = _pay(client, h, st["seats"][0]["id"], "k1")
    now = datetime(2030, 1, 10, 12, 0, tzinfo=UTC)
    booking = db.get(Booking, b["id"])
    booking.paid_at = datetime(2030, 1, 9, 18, 0, tzinfo=UTC)  # 9 Jan 23:30 in India
    booking.created_at = booking.paid_at
    db.commit()
    d = stats_service.dashboard(db, days=3, now=now)
    assert [(str(x["date"]), x["revenue_cents"]) for x in d["per_day"]] == [
        ("2030-01-08", 0),
        ("2030-01-09", 10000),
        ("2030-01-10", 0),
    ]


def test_empty_dashboard(client):
    adm = admin_headers(client, "dash-admin@example.com")
    d = client.get("/admin/dashboard", headers=adm).json()
    assert d["totals"]["revenue_cents"] == 0 and d["totals"]["occupancy_percent"] == 0.0
    assert len(d["per_day"]) == 14 and d["shows"] == [] and d["top_movies"] == []
