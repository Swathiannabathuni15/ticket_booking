"""Database tables.

MOVIE DOMAIN
    Movie, Theatre and Screen are catalogue data (admin-created, rarely
    changed). Showtime ties a movie to a screen at a time, with a price per
    seat type. ShowtimeSeat is generated once, when the showtime is created,
    from the screen's layout - one row per bookable seat at that showtime.
    The same physical seat is a DIFFERENT ShowtimeSeat at 6pm and at 9pm,
    which is exactly what lets two showings of the same movie, on the same
    screen, be booked independently without colliding.

BOOKING AND THE DOUBLE-BOOKING GUARD
    A Booking can now cover several seats (one order, Stage 2 onward), so
    there's no single seat_id on Booking any more - BookingSeat is the
    junction table, one row per seat in the order. The partial unique index
    that used to live on bookings(seat_id) now lives on
    booking_seats(showtime_seat_id). Postgres partial indexes can't look
    across tables, which is why BookingSeat.status is a denormalized COPY of
    its parent Booking.status - every place that changes a Booking's status
    (payment_service, booking_service.cancel_booking) must write the same
    status onto its BookingSeat rows in the same transaction. That copy is
    the price of getting the real backstop (Layer 2: a DB constraint that
    holds even if the app logic above it has a bug) on a child table.
"""

from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # email OR phone identifies a user (phone users sign in with an OTP and
    # have no email); both are unique when present.
    email: Mapped[str | None] = mapped_column(String(255), unique=True, index=True, nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), unique=True, index=True, nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    duration_minutes: Mapped[int] = mapped_column()
    poster_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    genre: Mapped[str] = mapped_column(String(100), default="", server_default="")
    language: Mapped[str] = mapped_column(String(50), default="English", server_default="English")
    rating: Mapped[float | None] = mapped_column(Numeric(3, 1), nullable=True)
    # Not set = already showing. A date in the future = "coming soon" (not bookable yet).
    release_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def status(self) -> str:
        return "coming_soon" if self.release_date and self.release_date > date.today() else "now_showing"


class Theatre(Base):
    __tablename__ = "theatres"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    screens: Mapped[list["Screen"]] = relationship(back_populates="theatre")


class Screen(Base):
    """`layout` is a list of rows, each `{"row": "A", "seats": [...]}` where
    `seats` is a list of seat types (e.g. "regular", "premium") in column
    order, with `null` marking an aisle gap (no seat at that position).
    Column numbers used in ShowtimeSeat.seat_number are 1-based positions in
    THIS list, so a gap leaves a hole in the numbering rather than shifting
    later seats down - which is what lets a frontend later detect "there's an
    aisle after seat 6" just from the seat numbers present, without a
    separate x/y field.
    """

    __tablename__ = "screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    theatre_id: Mapped[int] = mapped_column(ForeignKey("theatres.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    layout: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    theatre: Mapped[Theatre] = relationship(back_populates="screens")


class Showtime(Base):
    """`prices` maps seat type -> price (e.g. {"regular": 10.0, "premium":
    15.0}), used once, at creation, to stamp a price onto every
    ShowtimeSeat generated from the screen's layout. Changing `prices`
    afterwards does not retroactively change already-generated seats -
    same reasoning as a Seat's price not changing after it exists today.
    """

    __tablename__ = "showtimes"

    id: Mapped[int] = mapped_column(primary_key=True)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), index=True)
    screen_id: Mapped[int] = mapped_column(ForeignKey("screens.id"), index=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    prices: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    movie: Mapped[Movie] = relationship()
    screen: Mapped[Screen] = relationship()
    seats: Mapped[list["ShowtimeSeat"]] = relationship(back_populates="showtime")

    @property
    def movie_title(self) -> str:
        return self.movie.title

    @property
    def screen_name(self) -> str:
        return self.screen.name

    @property
    def theatre_name(self) -> str:
        return self.screen.theatre.name

    @property
    def theatre_id(self) -> int:
        return self.screen.theatre_id

    @property
    def city(self) -> str:
        return self.screen.theatre.city


class ShowtimeSeat(Base):
    """One row per bookable seat at one showtime. `(showtime_id, row_label,
    seat_number)` is unique - the same identity a real showtime seat map has.
    This is the row `SELECT ... FOR UPDATE` locks during booking (Layer 1).
    """

    __tablename__ = "showtime_seats"
    __table_args__ = (UniqueConstraint("showtime_id", "row_label", "seat_number", name="uq_seat_per_showtime"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    showtime_id: Mapped[int] = mapped_column(ForeignKey("showtimes.id"), index=True)
    row_label: Mapped[str] = mapped_column(String(5))
    seat_number: Mapped[int] = mapped_column()
    seat_type: Mapped[str] = mapped_column(String(20))
    price: Mapped[float] = mapped_column(Numeric(10, 2))

    showtime: Mapped[Showtime] = relationship(back_populates="seats")

    @property
    def label(self) -> str:
        return f"{self.row_label}{self.seat_number}"


class Booking(Base):
    """One order: a user, a showtime, a status, and the seats in
    `BookingSeat`. Two-step: `pending_payment` on creation, `confirmed` once
    POST /payments succeeds, or `cancelled` - either by the user or by the
    background job once `payment_due_at` passes unpaid (see
    app/services/payment_service.py and app/jobs/).
    """

    __tablename__ = "bookings"
    __table_args__ = (
        # Same user + same idempotency key = same booking (safe retries).
        UniqueConstraint("user_id", "idempotency_key", name="uq_user_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    showtime_id: Mapped[int] = mapped_column(ForeignKey("showtimes.id"), index=True)
    # pending_payment | confirmed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending_payment")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # When an unpaid booking becomes cancellable by the sweep job. Only meaningful
    # while status == 'pending_payment'.
    payment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # E-ticket (app/services/ticket_service.py): a random code issued when the booking is paid.
    # The QR code on the ticket points at it; scanning it at the door marks the ticket used.
    ticket_code: Mapped[str | None] = mapped_column(String(16), unique=True, index=True, nullable=True)
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmation_emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    seats: Mapped[list["BookingSeat"]] = relationship(back_populates="booking")

    showtime: Mapped[Showtime] = relationship()
    attempts: Mapped[list["PaymentAttempt"]] = relationship(order_by="PaymentAttempt.id")

    @property
    def payment_status(self) -> str:
        """paid | processing (a charge is in flight / webhook awaited) | failed | unpaid"""
        if self.status == "confirmed":
            return "paid"
        if any(a.status == "pending" for a in self.attempts):
            return "processing"
        if self.attempts and self.attempts[-1].status == "failed":
            return "failed"
        return "unpaid"

    @property
    def total_cents(self) -> int:
        return sum(int(round(float(s.showtime_seat.price) * 100)) for s in self.seats)

    @property
    def seat_labels(self) -> list[str]:
        return [s.showtime_seat.label for s in self.seats]

    @property
    def movie_title(self) -> str:
        return self.showtime.movie_title

    @property
    def theatre_name(self) -> str:
        return self.showtime.theatre_name

    @property
    def city(self) -> str:
        return self.showtime.city

    @property
    def starts_at(self) -> datetime:
        return self.showtime.starts_at

    @property
    def seat_ids(self) -> list[int]:
        """One booking can now cover several seats (Stage 2) - BookingOut
        reads this instead of a single seat_id."""
        return [s.showtime_seat_id for s in self.seats]


class BookingSeat(Base):
    """`status` is a denormalized copy of the parent Booking's status - see
    the module docstring for why. `showtime_seat_id` is unique while status
    is active: that partial index is Layer 2, the DB-level backstop against
    double-booking a seat, independent of whether the app code above it
    (booking_service.book_seat's row lock) has a bug.
    """

    __tablename__ = "booking_seats"
    __table_args__ = (
        Index(
            "uq_one_active_booking_seat_per_seat",
            "showtime_seat_id",
            unique=True,
            postgresql_where=text("status IN ('pending_payment', 'confirmed')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    showtime_seat_id: Mapped[int] = mapped_column(ForeignKey("showtime_seats.id"), index=True)
    # Mirrors Booking.status - see module docstring.
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    booking: Mapped[Booking] = relationship(back_populates="seats")
    showtime_seat: Mapped[ShowtimeSeat] = relationship()


class Payment(Base):
    """One row per successful payment. `booking_id` is unique: Postgres is
    the backstop against ever recording two payments for the same booking,
    the same role the partial unique index on booking_seats plays against
    double booking a seat - see payment_service.pay_booking.
    """

    __tablename__ = "payments"
    __table_args__ = (
        # Same user + same idempotency key = same payment (safe retries).
        UniqueConstraint("user_id", "idempotency_key", name="uq_payment_user_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BillSplit(Base):
    """A paid booking's cost split between friends (A5). One split per
    booking (`booking_id` unique). Money is integer cents, never floats, so
    the shares can be made to add up to the total exactly.
    status: open | settled (every share paid) | cancelled
    """

    __tablename__ = "bill_splits"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True, index=True)
    total_cents: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    booking: Mapped[Booking] = relationship()
    shares: Mapped[list["SplitShare"]] = relationship(back_populates="split", order_by="SplitShare.id")


class SplitShare(Base):
    """One person's part of a BillSplit. `nonce` is baked into the signed
    link; changing it would invalidate every link already handed out.
    status: unpaid | paid | cancelled
    """

    __tablename__ = "split_shares"

    id: Mapped[int] = mapped_column(primary_key=True)
    split_id: Mapped[int] = mapped_column(ForeignKey("bill_splits.id"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    amount_cents: Mapped[int] = mapped_column()
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    status: Mapped[str] = mapped_column(String(20), default="unpaid")
    nonce: Mapped[str] = mapped_column(String(32))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    split: Mapped[BillSplit] = relationship(back_populates="shares")


class LedgerEntry(Base):
    """The fake ledger: one row per money movement on a share. UNIQUE
    (share_id, kind) is the database backstop against charging a share twice,
    the same pattern as payments.booking_id.
    kind: payment | refund
    """

    __tablename__ = "ledger_entries"
    __table_args__ = (UniqueConstraint("share_id", "kind", name="uq_ledger_share_kind"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    share_id: Mapped[int] = mapped_column(ForeignKey("split_shares.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    amount_cents: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaymentAttempt(Base):
    """One try at paying a booking. Written (as `pending`) BEFORE the bank is
    called, so a crash or a lost reply never leaves us without a record of
    what we asked the bank to do. `idempotency_key` is the client's UUID;
    `gateway_key` ("<user_id>:<uuid>") is what the bank sees, so two users'
    keys can never collide there.
    status: pending | succeeded | failed | refunded
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (UniqueConstraint("user_id", "idempotency_key", name="uq_attempt_user_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100))
    gateway_key: Mapped[str] = mapped_column(String(140), unique=True)
    amount_cents: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20), default="pending")
    charge_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GatewayCharge(Base):
    """THE BANK'S OWN RECORDS (simulated). In real life this table lives in
    the payment provider's system, not ours; it is here only so the demo can
    show what a bank does: one charge per idempotency key, ever.
    status: succeeded | failed | refunded
    """

    __tablename__ = "gateway_charges"

    id: Mapped[int] = mapped_column(primary_key=True)
    charge_id: Mapped[str] = mapped_column(String(60), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(140), unique=True)
    amount_cents: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(20))
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WebhookEvent(Base):
    """Every webhook event id we have processed. UNIQUE(event_id) is what makes
    a re-delivered webhook (banks retry!) harmless."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(80), unique=True)
    type: Mapped[str] = mapped_column(String(60))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PushSubscription(Base):
    """One browser/phone that agreed to get notifications (Web Push). `endpoint`
    is the push service's address for that device and is unique; p256dh/auth
    are the keys used to encrypt messages to it."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReminderSent(Base):
    """One row per booking whose "starts soon" reminder was sent. UNIQUE
    booking_id is what stops a second worker (or a re-run) sending it twice."""

    __tablename__ = "reminders_sent"

    id: Mapped[int] = mapped_column(primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"), unique=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
