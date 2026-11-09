"""Split the bill (A5): after a booking is PAID, its owner splits the cost
with friends. Each friend pays their share through a signed, expiring link -
no account needed. Fake ledger only; no real money.

MONEY IS INTEGER CENTS. 10.00 / 3 can't be represented exactly, so equal
splits give everyone `total // n` cents and hand the leftover cents, one
each, to the first shares (so 1000 / 3 -> 334, 333, 333). Shares always add
up to the total exactly - that is checked, not assumed.

DOUBLE PAY: paying locks the share row, and ledger_entries has UNIQUE
(share_id, kind), so a second payment (double click, retry, two tabs) either
sees "paid" and returns the original result, or is rejected by Postgres.
"""
import secrets
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import BillSplit, Booking, BookingSeat, LedgerEntry, ShowtimeSeat, SplitShare
from app.security import ShareTokenExpired, ShareTokenInvalid, decode_share_token


class SplitError(Exception):
    pass


class BookingNotFound(SplitError):
    pass


class NotYourBooking(SplitError):
    pass


class BookingNotPaid(SplitError):
    pass


class SplitAlreadyExists(SplitError):
    pass


class SplitNotFound(SplitError):
    pass


class BadShares(SplitError):
    """Wrong total, non-positive share, too many shares, ..."""


class SplitClosed(SplitError):
    """Split cancelled or its booking was cancelled - nothing to pay."""


class ShareLinkExpired(SplitError):
    pass


class ShareLinkInvalid(SplitError):
    pass


def booking_total_cents(db: Session, booking_id: int) -> int:
    prices = db.scalars(
        select(ShowtimeSeat.price)
        .join(BookingSeat, BookingSeat.showtime_seat_id == ShowtimeSeat.id)
        .where(BookingSeat.booking_id == booking_id)
    ).all()
    return sum(int((Decimal(p) * 100).to_integral_value()) for p in prices)


def equal_shares(total_cents: int, n: int) -> list[int]:
    base, extra = divmod(total_cents, n)
    return [base + (1 if i < extra else 0) for i in range(n)]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_split(
    db: Session, user_id: int, booking_id: int, friends: list[str], mode: str,
    custom_cents: list[int] | None, include_owner: bool,
) -> BillSplit:
    """`friends` are display names. With include_owner the owner is share #0
    (already paid - they covered the booking up front)."""
    try:
        booking = db.scalar(select(Booking).where(Booking.id == booking_id).with_for_update())
        if booking is None:
            raise BookingNotFound(booking_id)
        if booking.user_id != user_id:
            raise NotYourBooking(booking_id)
        if booking.status != "confirmed":
            raise BookingNotPaid(booking_id)
        if db.scalar(select(BillSplit.id).where(BillSplit.booking_id == booking_id)) is not None:
            raise SplitAlreadyExists(booking_id)

        names = (["You"] if include_owner else []) + friends
        n = len(names)
        if n < 1 or n > settings.split_max_shares:
            raise BadShares(f"Need between 1 and {settings.split_max_shares} shares")
        if not friends:
            raise BadShares("Add at least one friend")

        total = booking_total_cents(db, booking_id)
        if mode == "equal":
            amounts = equal_shares(total, n)
        else:
            if custom_cents is None or len(custom_cents) != n:
                raise BadShares(f"Provide exactly {n} amounts (one per share)")
            amounts = custom_cents
            if sum(amounts) != total:
                raise BadShares(f"Shares add up to {sum(amounts)} cents but the total is {total}")
        if any(a <= 0 for a in amounts):
            raise BadShares("Every share must be more than zero")

        split = BillSplit(booking_id=booking_id, total_cents=total, status="open")
        db.add(split)
        db.flush()
        for i, (name, amount) in enumerate(zip(names, amounts)):
            owner = include_owner and i == 0
            share = SplitShare(
                split_id=split.id, name=name, amount_cents=amount, is_owner=owner,
                status="paid" if owner else "unpaid", nonce=secrets.token_hex(8),
                paid_at=_now() if owner else None,
            )
            db.add(share)
            db.flush()
            if owner:
                db.add(LedgerEntry(share_id=share.id, kind="payment", amount_cents=amount))
        _refresh_settled(split)
        db.commit()
        db.refresh(split)
        return split
    except IntegrityError as exc:  # two concurrent creates: the unique booking_id wins
        db.rollback()
        raise SplitAlreadyExists(booking_id) from exc
    except Exception:
        db.rollback()
        raise


def get_split(db: Session, user_id: int, booking_id: int) -> BillSplit:
    booking = db.get(Booking, booking_id)
    if booking is None:
        raise BookingNotFound(booking_id)
    if booking.user_id != user_id:
        raise NotYourBooking(booking_id)
    split = db.scalar(select(BillSplit).where(BillSplit.booking_id == booking_id))
    if split is None:
        raise SplitNotFound(booking_id)
    return split


def _refresh_settled(split: BillSplit) -> None:
    active = [s for s in split.shares if s.status != "cancelled"]
    if split.status == "open" and active and all(s.status == "paid" for s in active):
        split.status = "settled"


def cancel_split_in_txn(db: Session, split: BillSplit) -> None:
    """Close the split and refund paid shares (ledger entry, one per share).
    Caller owns the transaction. Idempotent."""
    if split.status == "cancelled":
        return
    split.status = "cancelled"
    for share in split.shares:
        if share.status == "paid" and not share.is_owner:
            db.add(LedgerEntry(share_id=share.id, kind="refund", amount_cents=share.amount_cents))
        share.status = "cancelled"


def cancel_split(db: Session, user_id: int, booking_id: int) -> BillSplit:
    try:
        split = get_split(db, user_id, booking_id)
        db.execute(select(BillSplit).where(BillSplit.id == split.id).with_for_update())
        cancel_split_in_txn(db, split)
        db.commit()
        return split
    except Exception:
        db.rollback()
        raise


def cancel_split_for_booking_in_txn(db: Session, booking_id: int) -> None:
    """Hook for booking cancellation: the booking is gone, so the split is too."""
    split = db.scalar(select(BillSplit).where(BillSplit.booking_id == booking_id))
    if split is not None:
        cancel_split_in_txn(db, split)


def resolve_share(db: Session, token: str, lock: bool = False) -> SplitShare:
    """Verify the signed link and load its share. Raises ShareLinkInvalid /
    ShareLinkExpired. A valid signature with the wrong nonce is invalid."""
    try:
        share_id, nonce = decode_share_token(token)
    except ShareTokenExpired as exc:
        raise ShareLinkExpired from exc
    except ShareTokenInvalid as exc:
        raise ShareLinkInvalid from exc
    stmt = select(SplitShare).where(SplitShare.id == share_id)
    if lock:
        stmt = stmt.with_for_update()
    share = db.scalar(stmt)
    if share is None or not secrets.compare_digest(share.nonce, nonce):
        raise ShareLinkInvalid
    return share


def pay_share(db: Session, token: str) -> tuple[SplitShare, bool]:
    """Pay a share via its link. Returns (share, newly_paid). Paying an
    already-paid share is a no-op that returns (share, False)."""
    try:
        share = resolve_share(db, token, lock=True)
        split = db.scalar(select(BillSplit).where(BillSplit.id == share.split_id).with_for_update())
        booking = db.get(Booking, split.booking_id)
        if share.status == "paid":
            db.rollback()
            return share, False
        if share.status == "cancelled" or split.status == "cancelled" or booking.status != "confirmed":
            raise SplitClosed(share.id)
        db.add(LedgerEntry(share_id=share.id, kind="payment", amount_cents=share.amount_cents))
        share.status = "paid"
        share.paid_at = _now()
        db.flush()
        db.refresh(split)
        _refresh_settled(split)
        db.commit()
        return share, True
    except IntegrityError:
        db.rollback()
        share = resolve_share(db, token)
        if share.status == "paid":
            return share, False
        raise
    except Exception:
        db.rollback()
        raise
