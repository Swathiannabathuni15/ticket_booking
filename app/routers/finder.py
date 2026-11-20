"""The seat finder: across every upcoming show of a movie, the best free seats together.

Our own code on our own data: it reads our seat maps (the same ones the booking pages use) and
ranks the best group of adjacent seats in each show, with optional filters. See best_block in
app/routers/showtimes.py for how a group is scored.
"""

import math
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import redis
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user_optional
from app.models import Movie, Screen, Showtime, Theatre, User
from app.redis_client import get_redis
from app.routers.showtimes import best_block, seat_map
from app.schemas import BestShowOut

router = APIRouter(prefix="/movies", tags=["finder"])

# local hours [start, end) for each time of day; "night" wraps past midnight
TIMES_OF_DAY = {"morning": (5, 12), "afternoon": (12, 17), "evening": (17, 21), "night": (21, 29)}
MAX_SHOWS = 30


def in_time_of_day(starts_at: datetime, name: str) -> bool:
    local = starts_at.astimezone(ZoneInfo(settings.display_timezone))
    lo, hi = TIMES_OF_DAY[name]
    hour = local.hour + (24 if local.hour < lo and hi > 24 else 0)  # 1am counts as hour 25 for "night"
    return lo <= hour < hi


@router.get("/{movie_id}/best-seats", response_model=list[BestShowOut])
def best_seats_for_movie(
    movie_id: int,
    count: int = Query(2, ge=1, le=8),
    city: str | None = None,
    centre_only: bool = False,
    not_too_close: bool = False,
    max_price: float | None = Query(None, gt=0),
    time_of_day: str | None = Query(None, pattern="^(morning|afternoon|evening|night)$"),
    limit: int = Query(8, ge=1, le=20),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    r: redis.Redis = Depends(get_redis),
):
    if db.get(Movie, movie_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Movie not found")
    stmt = (
        select(Showtime)
        .where(Showtime.movie_id == movie_id, Showtime.starts_at > datetime.now(UTC))
        .order_by(Showtime.starts_at)
    )
    if city:
        stmt = (
            stmt.join(Screen, Screen.id == Showtime.screen_id)
            .join(Theatre, Theatre.id == Screen.theatre_id)
            .where(Theatre.city.ilike(city))
        )
    showtimes = [
        s
        for s in db.scalars(stmt.limit(MAX_SHOWS * 3)).all()
        if time_of_day is None or in_time_of_day(s.starts_at, time_of_day)
    ][:MAX_SHOWS]

    out = []
    for st in showtimes:
        seats = seat_map(db, r, user, st.id)
        if not seats:
            continue
        n_rows = len({s.label.rstrip("0123456789") for s in seats})
        block = best_block(
            seats,
            count,
            centre_only=centre_only,
            max_price=max_price,
            min_rows_from_screen=math.ceil(n_rows * 0.25) if not_too_close else 0,  # skip the front quarter
        )
        if block is None:
            continue
        out.append(
            BestShowOut(
                showtime_id=st.id,
                starts_at=st.starts_at,
                movie_title=st.movie_title,
                theatre_name=st.theatre_name,
                city=st.city,
                screen_name=st.screen_name,
                seat_ids=[s.id for s in block.seats],
                seat_labels=[s.label for s in block.seats],
                seat_type=block.seats[0].seat_type,
                total_price=round(sum(s.price for s in block.seats), 2),
                rows_from_screen=block.rows_from_screen,
                off_centre=block.off_centre,
                score=round(block.score, 2),
            )
        )
    out.sort(key=lambda o: (o.score, o.starts_at))
    return out[:limit]
