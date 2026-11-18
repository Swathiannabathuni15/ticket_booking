"""Showtimes: creating one (with all its seats), and keeping a rolling schedule going.

A cinema listing is only useful if there are shows still to come. `ensure_schedule`
looks at every screen and makes sure the next few days have showtimes, creating the
missing ones. It is safe to run as often as you like (it never doubles up), so it can
run from a daily script or from the server's background loop (AUTO_SCHEDULE_DAYS).

HONEST NOTE: these showtimes are generated, not real cinema schedules. Real listings
need an agreement with cinemas or a data provider.
"""
import logging
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import redis
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Booking, Movie, Screen, Showtime, ShowtimeSeat

log = logging.getLogger(__name__)

SLOTS = [time(10, 30), time(14, 0), time(17, 30), time(21, 0)]     # local clock times, 3.5 hours apart
DEFAULT_PRICES = {"recliner": 600, "platinum": 350, "premium": 300, "gold": 250, "silver": 190, "standard": 180, "regular": 150}
SCHEDULE_LOCK = "schedule:last-run"
MIN_MOVIES_WITH_POSTERS = 6        # below this, every movie is scheduled (so a fresh install still has shows)


def seat_types(screen: Screen) -> set[str]:
    return {t for row in screen.layout for t in row["seats"] if t is not None}


def create_showtime_with_seats(
    db: Session, movie_id: int, screen: Screen, starts_at: datetime, prices: dict, *, commit: bool = True
) -> Showtime:
    """A showtime plus one ShowtimeSeat per seat in the screen's layout. Raises ValueError if the
    prices do not cover every seat type (checked BEFORE anything is created)."""
    missing = seat_types(screen) - prices.keys()
    if missing:
        raise ValueError(f"Missing a price for seat type(s): {sorted(missing)}")
    showtime = Showtime(movie_id=movie_id, screen_id=screen.id, starts_at=starts_at, prices=prices)
    db.add(showtime)
    db.flush()
    db.add_all(
        ShowtimeSeat(showtime_id=showtime.id, row_label=row["row"], seat_number=col, seat_type=kind, price=prices[kind])
        for row in screen.layout
        for col, kind in enumerate(row["seats"], start=1)
        if kind is not None  # None = aisle gap, no seat there
    )
    if commit:
        db.commit()
    return showtime


def _prices_for(db: Session, screen: Screen) -> dict:
    """Same prices as the screen's latest showtime, else defaults by seat type."""
    last = db.scalar(select(Showtime.prices).where(Showtime.screen_id == screen.id).order_by(Showtime.id.desc()).limit(1))
    if last and seat_types(screen) <= last.keys():
        return last
    return {t: DEFAULT_PRICES.get(t, 150) for t in seat_types(screen)}


def ensure_schedule(db: Session, days_ahead: int = 4, now: datetime | None = None) -> int:
    """Create the missing showtimes for today and the next `days_ahead` days. Returns how many
    were created. Movies rotate across screens and slots; a movie is never shown before its
    release date; a slot is skipped if that screen already has a show within 3 hours of it."""
    now = now or datetime.now(timezone.utc)
    tz = ZoneInfo(settings.display_timezone)
    today = now.astimezone(tz).date()
    screens = db.scalars(select(Screen).order_by(Screen.id)).all()
    movies = db.scalars(select(Movie).order_by(Movie.id)).all()
    if not screens or not movies:
        return 0
    window_end = datetime.combine(today + timedelta(days=days_ahead + 1), time(0), tzinfo=tz)
    existing: dict[int, list[datetime]] = {}
    for sid, at in db.execute(select(Showtime.screen_id, Showtime.starts_at).where(
            Showtime.starts_at >= now - timedelta(hours=4), Showtime.starts_at < window_end)):
        existing.setdefault(sid, []).append(at)

    created = 0
    for si, screen in enumerate(screens):
        prices = None
        for d in range(days_ahead + 1):
            day = today + timedelta(days=d)
            pool = [m for m in movies if m.release_date is None or m.release_date <= day]
            # Once there are enough movies WITH artwork, schedule only those: a listing full of plain
            # generated posters looks unfinished.
            with_poster = [m for m in pool if m.poster_url]
            if len(with_poster) >= MIN_MOVIES_WITH_POSTERS:
                pool = with_poster
            if not pool:
                continue
            for k, slot in enumerate(SLOTS):
                starts = datetime.combine(day, slot, tzinfo=tz)
                if starts <= now:
                    continue
                if any(abs((at - starts).total_seconds()) < 3 * 3600 for at in existing.get(screen.id, [])):
                    continue
                movie = pool[(si * 5 + k * 2 + d) % len(pool)]
                prices = prices or _prices_for(db, screen)
                create_showtime_with_seats(db, movie.id, screen, starts, prices, commit=False)
                existing.setdefault(screen.id, []).append(starts)
                created += 1
        db.commit()
    if created:
        log.info("schedule: created %s showtimes", created)
    return created


def clear_unbooked_future_showtimes(db: Session, now: datetime | None = None, *, dry_run: bool = True,
                                    buffer_hours: int = 1) -> int:
    """Remove showtimes that have not started (and do not start within `buffer_hours`) and that nobody has
    booked, with their seats, so ensure_schedule can restock them with a better movie mix. A showtime with
    ANY booking (even a cancelled one) is kept. Returns how many were (or, in a dry run, would be) removed."""
    now = now or datetime.now(timezone.utc)
    booked = select(Booking.id).where(Booking.showtime_id == Showtime.id).exists()
    ids = list(db.scalars(select(Showtime.id).where(Showtime.starts_at > now + timedelta(hours=buffer_hours), ~booked)))
    if dry_run or not ids:
        return len(ids)
    for i in range(0, len(ids), 500):                    # in chunks: each showtime owns hundreds of seat rows
        chunk = ids[i:i + 500]
        db.execute(delete(ShowtimeSeat).where(ShowtimeSeat.showtime_id.in_(chunk)))
        db.execute(delete(Showtime).where(Showtime.id.in_(chunk)))
        db.commit()
    return len(ids)


def extend_schedule_if_due(db: Session, r: redis.Redis | None) -> int:
    """For the background loop: at most once an hour, and only if AUTO_SCHEDULE_DAYS is set."""
    if settings.auto_schedule_days <= 0 or r is None:
        return 0
    try:
        if not r.set(SCHEDULE_LOCK, "1", nx=True, ex=3600):
            return 0
    except redis.exceptions.RedisError:
        return 0
    return ensure_schedule(db, settings.auto_schedule_days)
