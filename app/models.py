"""Database tables.

The most important line in this project is the partial unique index on
`bookings`: it lets the database itself refuse a second active booking for
the same seat, even if the application code has a bug. "Active" now covers
two statuses - see Booking below.
"""
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    venue: Mapped[str] = mapped_column(String(200))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    seats: Mapped[list["Seat"]] = relationship(back_populates="event")


class Seat(Base):
    __tablename__ = "seats"
    __table_args__ = (UniqueConstraint("event_id", "label", name="uq_seat_label_per_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), index=True)
    label: Mapped[str] = mapped_column(String(20))  # e.g. "A1"
    price: Mapped[float] = mapped_column(Numeric(10, 2))

    event: Mapped[Event] = relationship(back_populates="seats")


class Booking(Base):
    """Booking is now two-step: `pending_payment` on creation, `confirmed`
    once POST /payments succeeds, or `cancelled` - either by the user or by
    the background job once `payment_due_at` passes unpaid (see
    app/services/payment_service.py and app/jobs/).

    A seat is "taken" while a booking for it is EITHER pending_payment or
    confirmed - both hold the seat against other bookers, which is why the
    partial unique index below covers both statuses, not just 'confirmed'.
    """
    __tablename__ = "bookings"
    __table_args__ = (
        # Last line of defence: only ONE active (pending or confirmed) booking per seat.
        Index(
            "uq_one_active_booking_per_seat",
            "seat_id",
            unique=True,
            postgresql_where=text("status IN ('pending_payment', 'confirmed')"),
        ),
        # Same user + same idempotency key = same booking (safe retries).
        UniqueConstraint("user_id", "idempotency_key", name="uq_user_idempotency_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id"), index=True)
    # pending_payment | confirmed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="pending_payment")
    idempotency_key: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # When an unpaid booking becomes cancellable by the sweep job. Only meaningful
    # while status == 'pending_payment'.
    payment_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    seat: Mapped[Seat] = relationship()


class Payment(Base):
    """One row per successful payment. `booking_id` is unique: Postgres is
    the backstop against ever recording two payments for the same booking,
    the same role the partial unique index on bookings plays against double
    booking a seat - see payment_service.pay_booking.
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
