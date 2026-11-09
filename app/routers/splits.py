"""Split the bill (see app/services/split_service.py).

Owner endpoints (login): create / view / cancel a split on a paid booking.
Public endpoints (signed link, NO login): view and pay one share.
"""
import hashlib

import redis
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import get_current_user
from app.models import BillSplit, Booking, BookingSeat, Movie, Showtime, ShowtimeSeat, User
from app.redis_client import get_redis
from app.schemas import ShareLinkView, ShareOut, SplitCreate, SplitOut
from app.security import create_share_token
from app.services import rate_limit
from app.services import split_service as svc

router = APIRouter(tags=["split-the-bill"])


def _split_out(split: BillSplit, with_links: bool) -> SplitOut:
    shares = []
    for s in split.shares:
        link = None
        if with_links and not s.is_owner and s.status == "unpaid":
            link = f"/pay/share/{create_share_token(s.id, s.nonce)}"
        shares.append(ShareOut(
            id=s.id, name=s.name, amount_cents=s.amount_cents, is_owner=s.is_owner,
            status=s.status, paid_at=s.paid_at, link=link,
        ))
    paid = sum(s.amount_cents for s in split.shares if s.status == "paid")
    return SplitOut(
        id=split.id, booking_id=split.booking_id, total_cents=split.total_cents,
        status=split.status, paid_cents=paid, shares=shares,
    )


def _owner_errors(exc: svc.SplitError):
    if isinstance(exc, svc.BookingNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Booking not found")
    if isinstance(exc, svc.NotYourBooking):
        return HTTPException(status.HTTP_403_FORBIDDEN, "Not your booking")
    if isinstance(exc, svc.SplitNotFound):
        return HTTPException(status.HTTP_404_NOT_FOUND, "This booking has no split")
    if isinstance(exc, svc.BookingNotPaid):
        return HTTPException(status.HTTP_409_CONFLICT, "Pay for the booking before splitting it")
    if isinstance(exc, svc.SplitAlreadyExists):
        return HTTPException(status.HTTP_409_CONFLICT, "This booking is already split")
    if isinstance(exc, svc.BadShares):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    return exc


@router.post("/bookings/{booking_id}/split", response_model=SplitOut, status_code=status.HTTP_201_CREATED)
def create_split(
    booking_id: int, body: SplitCreate,
    db: Session = Depends(get_db), user: User = Depends(get_current_user),
):
    try:
        split = svc.create_split(
            db, user.id, booking_id, body.friends, body.mode, body.custom_cents, body.include_owner
        )
    except svc.SplitError as exc:
        raise _owner_errors(exc)
    return _split_out(split, with_links=True)


@router.get("/bookings/{booking_id}/split", response_model=SplitOut)
def read_split(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return _split_out(svc.get_split(db, user.id, booking_id), with_links=True)
    except svc.SplitError as exc:
        raise _owner_errors(exc)


@router.delete("/bookings/{booking_id}/split", response_model=SplitOut)
def cancel_split(booking_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        return _split_out(svc.cancel_split(db, user.id, booking_id), with_links=False)
    except svc.SplitError as exc:
        raise _owner_errors(exc)


# ---- public, signed-link endpoints -------------------------------------

def _link_errors(exc: svc.SplitError):
    if isinstance(exc, svc.ShareLinkExpired):
        return HTTPException(status.HTTP_410_GONE, "This payment link has expired")
    if isinstance(exc, svc.ShareLinkInvalid):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Invalid payment link")
    if isinstance(exc, svc.SplitClosed):
        return HTTPException(status.HTTP_409_CONFLICT, "This split was cancelled; nothing to pay")
    return exc


def _limit_by_link(token: str, r: redis.Redis):
    """Per payment LINK (there is no account and we don't count by IP): hammering
    one link is throttled, other links are unaffected."""
    ident = hashlib.sha256(token.encode()).hexdigest()[:24]
    try:
        rate_limit.check(
            r, f"sharepay:{ident}", settings.rate_limit_share_pay_per_window, settings.rate_limit_window_seconds
        )
    except rate_limit.RateLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, try again later",
            headers={"Retry-After": str(exc.retry_after_seconds)},
        )


@router.get("/pay/share/{token}", response_model=ShareLinkView)
def view_share(token: str, db: Session = Depends(get_db)):
    try:
        share = svc.resolve_share(db, token)
    except svc.SplitError as exc:
        raise _link_errors(exc)
    split = db.get(BillSplit, share.split_id)
    booking = db.get(Booking, split.booking_id)
    showtime = db.get(Showtime, booking.showtime_id)
    movie = db.get(Movie, showtime.movie_id)
    seats = db.scalars(
        select(ShowtimeSeat).join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .where(BookingSeat.booking_id == booking.id)
    ).all()
    return ShareLinkView(
        name=share.name, amount_cents=share.amount_cents, status=share.status,
        movie_title=movie.title, starts_at=showtime.starts_at, seats=[s.label for s in seats],
    )


@router.post("/pay/share/{token}", response_model=ShareOut)
def pay_share(
    token: str, db: Session = Depends(get_db), r: redis.Redis = Depends(get_redis),
):
    _limit_by_link(token, r)
    try:
        share, _newly = svc.pay_share(db, token)
    except svc.SplitError as exc:
        raise _link_errors(exc)
    return ShareOut(
        id=share.id, name=share.name, amount_cents=share.amount_cents,
        is_owner=share.is_owner, status=share.status, paid_at=share.paid_at,
    )
