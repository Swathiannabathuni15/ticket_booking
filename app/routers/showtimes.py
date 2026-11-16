"""Showtimes: a movie on a screen at a time. Creating one generates its
ShowtimeSeat rows from the screen's layout - see create_showtime.

GET /showtimes/{id}/seats is the seat map: each seat is "available", "held"
(by someone else right now), "held_by_me", or "booked". It works for anyone
(no login needed to browse), but reports "held_by_me" instead of plain
"held" when a valid token identifies whose hold it is.

It is also, deliberately, best-effort about Redis: if Redis can't be
reached, every seat just falls back to "available" instead of the whole page
failing. This is a read-only listing - the real double-booking protection
lives in Postgres (Layer 1/2, see booking_service) and in the fail-closed
503 that /holds and /bookings return when Redis is down. Losing the "held"
distinction here for a moment is a cosmetic downgrade, not a safety one.
"""
from typing import NamedTuple

from fastapi import APIRouter, Depends, HTTPException, status
import redis
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user_optional, require_admin
from app.models import BookingSeat, Movie, Screen, Showtime, ShowtimeSeat, Theatre, User
from app.redis_client import get_redis
from app.schemas import SeatOut, ShowtimeCreate, ShowtimeOut
from app.services.booking_service import ACTIVE_STATUSES

router = APIRouter(prefix="/showtimes", tags=["showtimes"])


@router.post("", response_model=ShowtimeOut, status_code=status.HTTP_201_CREATED)
def create_showtime(
    body: ShowtimeCreate, db: Session = Depends(get_db), _admin: User = Depends(require_admin)
):
    if db.get(Movie, body.movie_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Movie not found")
    screen = db.get(Screen, body.screen_id)
    if screen is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Screen not found")

    # Validate prices cover every seat type in the layout BEFORE creating
    # anything, so a bad request never leaves a half-built showtime behind.
    seat_types_in_layout = {
        seat_type
        for row in screen.layout
        for seat_type in row["seats"]
        if seat_type is not None
    }
    missing = seat_types_in_layout - body.prices.keys()
    if missing:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"Missing a price for seat type(s): {sorted(missing)}"
        )

    showtime = Showtime(
        movie_id=body.movie_id, screen_id=body.screen_id, starts_at=body.starts_at, prices=body.prices
    )
    db.add(showtime)
    db.flush()  # gives showtime.id without committing yet

    db.add_all(
        ShowtimeSeat(
            showtime_id=showtime.id,
            row_label=row["row"],
            seat_number=col,
            seat_type=seat_type,
            price=body.prices[seat_type],
        )
        for row in screen.layout
        for col, seat_type in enumerate(row["seats"], start=1)
        if seat_type is not None  # None = aisle gap, no seat there
    )
    db.commit()
    return showtime


@router.get("", response_model=list[ShowtimeOut])
def list_showtimes(
    limit: int = 20, offset: int = 0, movie_id: int | None = None, city: str | None = None, db: Session = Depends(get_db)
):
    limit = max(1, min(limit, 100))
    stmt = select(Showtime).order_by(Showtime.starts_at).limit(limit).offset(max(offset, 0))
    if movie_id is not None:
        stmt = stmt.where(Showtime.movie_id == movie_id)
    if city:
        stmt = stmt.where(Showtime.screen_id.in_(
            select(Screen.id).join(Theatre, Theatre.id == Screen.theatre_id)
            .where(func.lower(Theatre.city) == city.lower())
        ))
    return db.scalars(stmt).all()


# A showtime's seats never change after creation, so the (id, label, type,
# price) list is cached per process; only "who has what" is read live. This was
# the hot path in the load test (every viewer polls it).
_SEAT_CACHE: dict[int, list[tuple]] = {}


def clear_seat_cache() -> None:
    _SEAT_CACHE.clear()


def _static_seats(db: Session, showtime_id: int) -> list[tuple]:
    cached = _SEAT_CACHE.get(showtime_id)
    if cached is not None:
        return cached
    rows = db.execute(
        select(ShowtimeSeat.id, ShowtimeSeat.row_label, ShowtimeSeat.seat_number,
               ShowtimeSeat.seat_type, ShowtimeSeat.price)
        .where(ShowtimeSeat.showtime_id == showtime_id)
        .order_by(ShowtimeSeat.row_label, ShowtimeSeat.seat_number)
    ).all()
    if not rows:
        if db.get(Showtime, showtime_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Showtime not found")
        return []
    data = [(r.id, f"{r.row_label}{r.seat_number}", r.seat_type, float(r.price)) for r in rows]
    if len(_SEAT_CACHE) > 500:
        _SEAT_CACHE.clear()
    _SEAT_CACHE[showtime_id] = data
    return data


def seat_map(db: Session, r: redis.Redis, user: User | None, showtime_id: int) -> list[SeatOut]:
    static = _static_seats(db, showtime_id)
    if not static:
        return []
    booked = set(db.scalars(
        select(BookingSeat.showtime_seat_id)
        .join(ShowtimeSeat, ShowtimeSeat.id == BookingSeat.showtime_seat_id)
        .where(ShowtimeSeat.showtime_id == showtime_id, BookingSeat.status.in_(ACTIVE_STATUSES))
    ).all())
    # One MGET for every seat's hold key. Best-effort: a Redis error falls back
    # to "available" (cosmetic only - the real guard is Postgres, see module docstring).
    hold_values: dict[int, str | None] = {}
    try:
        values = r.mget([f"seat_hold:{sid}" for sid, *_ in static])
        hold_values = {row[0]: v for row, v in zip(static, values)}
    except redis.exceptions.RedisError:
        pass
    out = []
    for sid, label, seat_type, price in static:
        if sid in booked:
            st = "booked"
        else:
            held_by = hold_values.get(sid)
            owner = held_by.split(":", 1)[0] if held_by else None
            if owner is None:
                st = "available"
            elif user is not None and owner == str(user.id):
                st = "held_by_me"
            else:
                st = "held"
        out.append(SeatOut(id=sid, label=label, seat_type=seat_type, price=price,
                           status=st, available=st == "available"))
    return out


@router.get("/{showtime_id}", response_model=ShowtimeOut)
def get_showtime(showtime_id: int, db: Session = Depends(get_db)):
    showtime = db.get(Showtime, showtime_id)
    if showtime is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Showtime not found")
    return showtime


@router.get("/{showtime_id}/seats", response_model=list[SeatOut])
def list_seats(
    showtime_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    r: redis.Redis = Depends(get_redis),
):
    return seat_map(db, r, user, showtime_id)


def _split_label(label: str) -> tuple[str, int]:
    i = next(k for k, ch in enumerate(label) if ch.isdigit())
    return label[:i], int(label[i:])


class Block(NamedTuple):
    """A group of adjacent seats and how good a spot it is (lower score = better)."""
    seats: list
    score: float
    rows_from_screen: int
    off_centre: float        # how many seats left or right of the middle of the row


def best_block(
    seats: list[SeatOut], count: int, seat_type: str | None = None, *,
    centre_only: bool = False, min_rows_from_screen: int = 0, max_price: float | None = None,
) -> Block | None:
    """`count` ADJACENT available seats in one row (an aisle gap or any unavailable seat
    breaks adjacency), as close as possible to the best spot: about 60% of the way back,
    centred left-right. Optional filters:
      centre_only          only seats in the middle 40% of the row
      min_rows_from_screen skip the rows closest to the screen ("not too close")
      max_price            every seat in the block must cost at most this
    None if no such group exists."""
    rows: dict[str, list[tuple[int, SeatOut]]] = {}
    for s in seats:
        row, num = _split_label(s.label)
        rows.setdefault(row, []).append((num, s))
    order = sorted(rows)
    ideal = 0.6 * (len(order) - 1)
    best: Block | None = None
    for idx, row in enumerate(order):
        rows_from_screen = len(order) - 1 - idx
        if rows_from_screen < min_rows_from_screen:
            continue
        line = sorted(rows[row], key=lambda t: t[0])
        centre = (line[0][0] + line[-1][0]) / 2
        band = 0.2 * (line[-1][0] - line[0][0] + 1)          # the middle 40% of the row
        run: list[tuple[int, SeatOut]] = []
        for num, s in line + [(-10, None)]:  # sentinel flushes the last run
            ok = (s is not None and s.status == "available" and (seat_type is None or s.seat_type == seat_type)
                  and (max_price is None or s.price <= max_price))
            if ok and run and num == run[-1][0] + 1:
                run.append((num, s))
            else:
                if len(run) >= count:
                    for k in range(len(run) - count + 1):
                        win = run[k:k + count]
                        mid = (win[0][0] + win[-1][0]) / 2
                        if centre_only and abs(mid - centre) > band:
                            continue
                        score = abs(idx - ideal) * 2 + abs(mid - centre)
                        if best is None or score < best.score:
                            best = Block([w[1] for w in win], score, rows_from_screen, round(mid - centre, 1))
                run = [(num, s)] if ok else []
    return best


def pick_best_seats(seats: list[SeatOut], count: int, seat_type: str | None = None) -> list[SeatOut]:
    """The seats of the best block, or [] (see best_block)."""
    block = best_block(seats, count, seat_type)
    return block.seats if block else []


@router.get("/{showtime_id}/best-seats", response_model=list[SeatOut])
def best_seats(
    showtime_id: int, count: int, seat_type: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
    r: redis.Redis = Depends(get_redis),
):
    if not 1 <= count <= 8:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "count must be 1-8")
    picked = pick_best_seats(seat_map(db, r, user, showtime_id), count, seat_type)
    if not picked:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No {count} adjacent seats available")
    return picked
