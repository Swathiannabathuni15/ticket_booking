"""Numbers for the admin dashboard: revenue and bookings per day, occupancy per show, top movies.

"Revenue" = the seat prices of PAID bookings (status confirmed), counted on the day they were paid, in the
cinema's time zone (DISPLAY_TIMEZONE). Cancelled or expired bookings never count, and neither do refunded
late payments (those bookings are never confirmed). Each number is ONE grouped SQL query, so the page stays
fast however many bookings there are.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import Date, case, cast, distinct, func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, BookingSeat, Movie, Screen, Showtime, ShowtimeSeat, Theatre

UPCOMING_DAYS = 7  # the occupancy table covers the window plus this many days ahead
MAX_SHOWS = 100


def _cents(x) -> int:
    return int(round(float(x or 0) * 100))


def dashboard(db: Session, days: int = 14, now: datetime | None = None) -> dict:
    days = max(1, min(days, 90))
    now = now or datetime.now(UTC)
    tz_name = settings.display_timezone
    tz = ZoneInfo(tz_name)
    today = now.astimezone(tz).date()
    first = today - timedelta(days=days - 1)
    since = datetime.combine(first, datetime.min.time(), tzinfo=tz)

    paid_day = cast(func.timezone(tz_name, Booking.paid_at), Date)
    paid = (Booking.status == "confirmed") & (Booking.paid_at >= since)

    # --- per day: bookings, tickets, revenue ---
    rows = db.execute(
        select(
            paid_day.label("day"),
            func.count(distinct(Booking.id)),
            func.count(BookingSeat.id),
            func.coalesce(func.sum(ShowtimeSeat.price), 0),
        )
        .join(BookingSeat, BookingSeat.booking_id == Booking.id)
        .join(ShowtimeSeat, ShowtimeSeat.id == BookingSeat.showtime_seat_id)
        .where(paid)
        .group_by(paid_day)
    ).all()
    by_day = {r[0]: r for r in rows}
    per_day: list[dict[str, Any]] = []
    for i in range(days):
        d: date = first + timedelta(days=i)
        r = by_day.get(d)
        per_day.append(
            {
                "date": d,
                "bookings": r[1] if r else 0,
                "tickets": r[2] if r else 0,
                "revenue_cents": _cents(r[3] if r else 0),
            }
        )

    # --- booking outcomes for bookings STARTED in the window (how many paid vs abandoned) ---
    outcome = dict(
        db.execute(
            select(Booking.status, func.count()).where(Booking.created_at >= since).group_by(Booking.status)
        ).all()
    )

    # --- occupancy per show: shows in the window and the next week ---
    show_from, show_to = since, now + timedelta(days=UPCOMING_DAYS)
    sold = func.count(case((BookingSeat.status == "confirmed", 1)))
    total = func.count(distinct(ShowtimeSeat.id))
    shows = db.execute(
        select(
            Showtime.id,
            Showtime.starts_at,
            Movie.title,
            Theatre.name,
            Theatre.city,
            Screen.name,
            total.label("total"),
            sold.label("sold"),
            func.coalesce(func.sum(case((BookingSeat.status == "confirmed", ShowtimeSeat.price))), 0),
        )
        .join(Movie, Movie.id == Showtime.movie_id)
        .join(Screen, Screen.id == Showtime.screen_id)
        .join(Theatre, Theatre.id == Screen.theatre_id)
        .join(ShowtimeSeat, ShowtimeSeat.showtime_id == Showtime.id)
        .outerjoin(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .where(Showtime.starts_at >= show_from, Showtime.starts_at < show_to)
        .group_by(Showtime.id, Movie.title, Theatre.name, Theatre.city, Screen.name)
        .order_by((sold * 1.0 / total).desc(), Showtime.starts_at)
        .limit(MAX_SHOWS)
    ).all()
    show_count, seat_total = db.execute(
        select(func.count(distinct(Showtime.id)), func.count(ShowtimeSeat.id))
        .join(ShowtimeSeat, ShowtimeSeat.showtime_id == Showtime.id)
        .where(Showtime.starts_at >= show_from, Showtime.starts_at < show_to)
    ).one()
    seats_sold = db.scalar(
        select(func.count(BookingSeat.id))
        .join(ShowtimeSeat, ShowtimeSeat.id == BookingSeat.showtime_seat_id)
        .join(Showtime, Showtime.id == ShowtimeSeat.showtime_id)
        .where(BookingSeat.status == "confirmed", Showtime.starts_at >= show_from, Showtime.starts_at < show_to)
    )

    # --- top movies by revenue in the window ---
    top = db.execute(
        select(Movie.title, func.count(BookingSeat.id), func.coalesce(func.sum(ShowtimeSeat.price), 0))
        .select_from(Booking)
        .join(BookingSeat, BookingSeat.booking_id == Booking.id)
        .join(ShowtimeSeat, ShowtimeSeat.id == BookingSeat.showtime_seat_id)
        .join(Showtime, Showtime.id == ShowtimeSeat.showtime_id)
        .join(Movie, Movie.id == Showtime.movie_id)
        .where(paid)
        .group_by(Movie.title)
        .order_by(func.sum(ShowtimeSeat.price).desc())
        .limit(8)
    ).all()

    revenue = sum(d["revenue_cents"] for d in per_day)
    bookings = sum(d["bookings"] for d in per_day)
    return {
        "days": days,
        "from_date": first,
        "to_date": today,
        "timezone": tz_name,
        "totals": {
            "revenue_cents": revenue,
            "bookings": bookings,
            "tickets": sum(d["tickets"] for d in per_day),
            "avg_ticket_cents": round(revenue / max(1, sum(d["tickets"] for d in per_day))),
            "occupancy_percent": round(100 * (seats_sold or 0) / seat_total, 1) if seat_total else 0.0,
            "shows": show_count,
            "started": sum(outcome.values()),
            "abandoned": outcome.get("cancelled", 0),
            "pending": outcome.get("pending_payment", 0),
        },
        "per_day": per_day,
        "shows": [
            {
                "showtime_id": s[0],
                "starts_at": s[1],
                "movie_title": s[2],
                "theatre_name": s[3],
                "city": s[4],
                "screen_name": s[5],
                "seats_total": s[6],
                "seats_sold": s[7],
                "occupancy_percent": round(100 * s[7] / s[6], 1) if s[6] else 0.0,
                "revenue_cents": _cents(s[8]),
            }
            for s in shows
        ],
        "top_movies": [{"title": t[0], "tickets": t[1], "revenue_cents": _cents(t[2])} for t in top],
    }
