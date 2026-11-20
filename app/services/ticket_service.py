"""E-tickets: a random code per paid booking, a QR code for it, check-in at the door, and a calendar file.

THE CODE IS THE SECRET. 12 characters from a 32-letter alphabet (no 0/O/1/I, which look alike) is 60 bits:
far too many to guess, so knowing a code is proof of holding the ticket, and no signature is needed. It is
random (not made from the booking number), so tickets cannot be guessed from each other. The QR code just
contains the website address of that ticket (<PUBLIC_BASE_URL>/ticket/<code>).

CHECK-IN marks a ticket used exactly once, under a row lock, so two scanners reading the same ticket at the
same moment cannot both admit it. Only staff (admins) can check people in; anyone can SEE a ticket's status
from its code (no personal details), which is what a phone camera scan opens.
"""

import io
import re
import secrets
from datetime import UTC, datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import segno
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import required
from app.models import Booking
from app.schemas import CheckInOut, TicketOut, TicketPublicOut

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ENTRY_OPENS_BEFORE = timedelta(hours=3)  # staff can admit from 3 hours before the show...
ENTRY_CLOSES_AFTER = timedelta(hours=1)  # ...until an hour after it ends


def new_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def normalize(code: str) -> str:
    """'abcd efgh-jklm' -> 'ABCD-EFGH-JKLM'. Returns '' if it cannot be a ticket code."""
    raw = re.sub(r"[^A-Za-z0-9]", "", code or "").upper()
    if len(raw) != 12 or any(ch not in ALPHABET for ch in raw):
        return ""
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def issue_ticket(db: Session, booking: Booking) -> str:
    """Give a PAID booking its ticket code (once; calling again returns the same one)."""
    if booking.ticket_code:
        return booking.ticket_code
    for _ in range(10):
        code = new_code()
        if db.scalar(select(Booking.id).where(Booking.ticket_code == code)) is None:
            booking.ticket_code = code
            return code
    raise RuntimeError("could not find an unused ticket code")


def ticket_url(code: str) -> str:
    return f"{settings.public_base_url.rstrip('/')}/ticket/{code}"


def _qr(code: str):
    return segno.make(ticket_url(code), error="m")


def qr_data_uri(code: str) -> str:
    return _qr(code).svg_data_uri(scale=8, border=2)


def qr_png(code: str) -> bytes:
    out = io.BytesIO()
    _qr(code).save(out, kind="png", scale=8, border=2)
    return out.getvalue()


def _local(dt: datetime) -> datetime:
    try:
        return dt.astimezone(ZoneInfo(settings.display_timezone))
    except Exception:
        return dt


def state_of(booking: Booking) -> str:
    if booking.status != "confirmed":
        return "cancelled"
    return "used" if booking.checked_in_at else "valid"


def ends_at(booking: Booking) -> datetime:
    return booking.showtime.starts_at + timedelta(minutes=booking.showtime.movie.duration_minutes)


def directions_url(theatre: str, city: str) -> str:
    return "https://www.google.com/maps/dir/?api=1&destination=" + quote(f"{theatre}, {city}", safe="")


def public_view(booking: Booking) -> TicketPublicOut:
    st = booking.showtime
    code = required(booking.ticket_code, "ticket code")
    return TicketPublicOut(
        ticket_code=code,
        state=state_of(booking),
        movie_title=st.movie_title,
        poster_url=st.movie.poster_url,
        theatre_name=st.theatre_name,
        city=st.city,
        screen_name=st.screen_name,
        starts_at=st.starts_at,
        seat_labels=booking.seat_labels,
        checked_in_at=booking.checked_in_at,
    )


def owner_view(booking: Booking) -> TicketOut:
    p = public_view(booking)
    return TicketOut(
        booking_id=booking.id,
        ticket_code=p.ticket_code,
        ticket_url=ticket_url(p.ticket_code),
        qr_image=qr_data_uri(p.ticket_code),
        state=p.state,
        movie_title=p.movie_title,
        poster_url=p.poster_url,
        theatre_name=p.theatre_name,
        city=p.city,
        screen_name=p.screen_name,
        starts_at=p.starts_at,
        ends_at=ends_at(booking),
        seat_labels=p.seat_labels,
        total_cents=booking.total_cents,
        checked_in_at=p.checked_in_at,
        directions_url=directions_url(p.theatre_name, p.city),
    )


def check_in(db: Session, code: str, now: datetime | None = None) -> CheckInOut:
    """Admit the ticket holder, once. The row is locked while we decide, so two scans of the same ticket at the
    same instant cannot both succeed."""
    now = now or datetime.now(UTC)
    code = normalize(code)
    booking = (
        db.scalar(
            select(Booking)
            .where(Booking.ticket_code == code)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if code
        else None
    )
    if booking is None:
        return CheckInOut(result="invalid", message="This is not a valid ticket.")
    view = public_view(booking)
    if booking.status != "confirmed":
        db.rollback()
        return CheckInOut(result="cancelled", message="This booking was cancelled. Do not admit.", ticket=view)
    if booking.checked_in_at:
        db.rollback()
        when = _local(booking.checked_in_at).strftime("%d %b, %H:%M")
        return CheckInOut(result="already_used", message=f"Already used at {when}. Do not admit again.", ticket=view)
    starts = booking.showtime.starts_at
    if now < starts - ENTRY_OPENS_BEFORE:
        db.rollback()
        return CheckInOut(result="too_early", message="Entry is not open yet for this show.", ticket=view)
    if now > ends_at(booking) + ENTRY_CLOSES_AFTER:
        db.rollback()
        return CheckInOut(result="expired", message="This show has already finished.", ticket=view)
    booking.checked_in_at = now
    db.commit()
    return CheckInOut(result="admitted", message="Valid ticket. Admit.", ticket=public_view(booking))


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_for(booking: Booking, now: datetime | None = None) -> str:
    """A calendar event (with a reminder an hour before) the person can add to any calendar app."""
    fmt = "%Y%m%dT%H%M%SZ"
    now = now or datetime.now(UTC)
    st = booking.showtime
    code = required(booking.ticket_code, "ticket code")
    seats = ", ".join(booking.seat_labels)
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ticketshow//E-ticket//EN",
        "CALSCALE:GREGORIAN",
        "BEGIN:VEVENT",
        f"UID:{code}@ticketshow",
        f"DTSTAMP:{now.astimezone(UTC).strftime(fmt)}",
        f"DTSTART:{st.starts_at.astimezone(UTC).strftime(fmt)}",
        f"DTEND:{ends_at(booking).astimezone(UTC).strftime(fmt)}",
        f"SUMMARY:{_ics_escape(st.movie_title + ' at ' + st.theatre_name)}",
        f"LOCATION:{_ics_escape(st.theatre_name + ', ' + st.city)}",
        f"DESCRIPTION:{_ics_escape(f'Seats {seats}. Ticket code {code}. {ticket_url(code)}')}",
        "BEGIN:VALARM",
        "TRIGGER:-PT60M",
        "ACTION:DISPLAY",
        "DESCRIPTION:Your movie starts in an hour",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
    ]
    return "\r\n".join(lines) + "\r\n"
