"""The booking confirmation email: the e-ticket with its QR code, seats, time, place and a calendar file.

EXACTLY ONE EMAIL PER BOOKING, even though three things can confirm a payment (the pay call, the bank's
webhook, the sweeper) and the sweeper also retries. Before sending, we CLAIM the booking with one atomic
UPDATE ... WHERE confirmation_emailed_at IS NULL. Only the caller whose UPDATE changed a row sends; everyone
else sees 0 rows and stops. If the send fails, the claim is undone so the next sweep tries again.

NEVER BLOCKS PAYMENT. Sending happens after the payment is committed, in a background thread, and every error
is caught and logged. With no SMTP settings (the demo) nothing is sent and the ticket is still on the website.
Phone-only accounts have no email address; they see the ticket in "My bookings".
"""

import html
import logging
import threading
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

from sqlalchemy import CursorResult, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal, required
from app.models import Booking, User
from app.services import ticket_service
from app.services.otp_delivery import DeliveryFailed, email_configured, smtp_send

log = logging.getLogger("tickets.email")

# Tests set this to False so the email is sent before the call returns.
RUN_IN_THREAD = True
# The sweeper only catches up on recent bookings, so turning email on later doesn't mail every old customer.
CATCH_UP_WINDOW = timedelta(days=1)


def _local(dt: datetime) -> datetime:
    try:
        return dt.astimezone(ZoneInfo(settings.display_timezone))
    except Exception:
        return dt


def build_message(booking: Booking, to: str) -> EmailMessage:
    st = booking.showtime
    code = required(booking.ticket_code, "ticket code")
    start = _local(st.starts_at)
    when = start.strftime("%a, %d %b %Y, %I:%M %p")
    seats = ", ".join(booking.seat_labels)
    total = f"Rs. {booking.total_cents / 100:,.2f}"
    link = ticket_service.ticket_url(code)
    directions = ticket_service.directions_url(st.theatre_name, st.city)
    brand = settings.otp_brand

    msg = EmailMessage()
    msg["Subject"] = f"Your tickets: {st.movie_title} on {start.strftime('%d %b, %I:%M %p')}"
    msg["From"] = settings.smtp_from
    msg["To"] = to
    msg.set_content(
        f"Your booking is confirmed.\n\n"
        f"{st.movie_title}\n{when}\n{st.theatre_name}, {st.city} ({st.screen_name})\n"
        f"Seats: {seats}\nPaid: {total}\n\n"
        f"Ticket code: {code}\nShow this at the entrance, or open: {link}\n\n"
        f"Directions: {directions}\n\n- {brand}\n"
    )
    e = html.escape  # movie and theatre names come from outside data: never trust them in HTML
    msg.add_alternative(
        f"""\
<!doctype html><html><body style="margin:0;background:#f2f2f2;font-family:Arial,Helvetica,sans-serif;color:#222">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" style="max-width:520px;background:#fff;border-radius:12px;overflow:hidden" cellpadding="0" cellspacing="0">
<tr><td style="background:#f84464;color:#fff;padding:16px 20px;font-size:18px;font-weight:bold">{e(brand)} &middot; Booking confirmed</td></tr>
<tr><td style="padding:20px">
  <div style="font-size:22px;font-weight:bold;margin-bottom:4px">{e(st.movie_title)}</div>
  <div style="color:#666">{e(when)}</div>
  <div style="color:#666;margin-bottom:12px">{e(st.theatre_name)}, {e(st.city)} &middot; {e(st.screen_name)}</div>
  <table role="presentation" cellpadding="0" cellspacing="0" style="width:100%;border-top:1px dashed #ccc;border-bottom:1px dashed #ccc;margin:8px 0">
    <tr><td style="padding:10px 0;color:#666">Seats</td><td align="right" style="padding:10px 0;font-weight:bold">{e(seats)}</td></tr>
    <tr><td style="padding:0 0 10px;color:#666">Paid</td><td align="right" style="padding:0 0 10px;font-weight:bold">{e(total)}</td></tr>
  </table>
  <div style="text-align:center;margin:16px 0 4px"><img src="cid:ticket-qr" width="200" height="200" alt="Ticket QR code"></div>
  <div style="text-align:center;font-family:monospace;font-size:18px;letter-spacing:2px">{e(code)}</div>
  <div style="text-align:center;color:#666;font-size:13px;margin-bottom:16px">Show this QR code at the entrance.</div>
  <div style="text-align:center">
    <a href="{e(link)}" style="display:inline-block;background:#f84464;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;margin:4px">Open ticket</a>
    <a href="{e(directions)}" style="display:inline-block;background:#333;color:#fff;text-decoration:none;padding:10px 18px;border-radius:8px;margin:4px">Directions</a>
  </div>
</td></tr></table>
<div style="color:#999;font-size:12px;margin-top:12px">The calendar file attached adds the show to your calendar.</div>
</td></tr></table></body></html>
""",
        subtype="html",
    )
    # The QR code as an inline image (cid:ticket-qr): most mail apps block images from links, not inline ones.
    required(msg.get_body(preferencelist=("html",)), "html part").add_related(
        ticket_service.qr_png(code), maintype="image", subtype="png", cid="<ticket-qr>"
    )
    msg.add_attachment(
        ticket_service.ics_for(booking).encode(),
        maintype="text",
        subtype="calendar",
        filename="ticket.ics",
        params={"method": "PUBLISH"},
    )
    return msg


def _address(db: Session, booking: Booking) -> str | None:
    return db.scalar(select(User.email).where(User.id == booking.user_id))


def send_confirmation(db: Session, booking_id: int, *, force: bool = False) -> bool:
    """Send the ticket email for one booking. Returns True if an email went out.
    force=True is the "email me my ticket again" button: it skips the once-only claim."""
    if not email_configured():
        return False
    booking = db.get(Booking, booking_id)
    if booking is None or booking.status != "confirmed" or not booking.ticket_code:
        return False
    to = _address(db, booking)
    if not to:
        return False  # phone-only account: nothing to send to
    now = datetime.now(UTC)
    if not force:
        result: CursorResult = db.execute(  # type: ignore[assignment]
            update(Booking)
            .where(Booking.id == booking_id, Booking.confirmation_emailed_at.is_(None))
            .values(confirmation_emailed_at=now)
        )
        claimed = result.rowcount
        db.commit()
        if not claimed:
            return False  # someone else already sent (or is sending) it
    try:
        smtp_send(build_message(booking, to))
    except DeliveryFailed:
        if not force:  # give the claim back so the sweeper tries again
            db.execute(update(Booking).where(Booking.id == booking_id).values(confirmation_emailed_at=None))
            db.commit()
        raise
    if force:
        booking.confirmation_emailed_at = now
        db.commit()
    return True


def _send_safely(booking_id: int) -> None:
    try:
        with SessionLocal() as db:
            send_confirmation(db, booking_id)
    except Exception:
        log.warning("confirmation email for booking %s not sent now; the sweeper will retry", booking_id)


def notify(booking_id: int) -> None:
    """Called right after a booking is confirmed. Never raises, never slows down the payment."""
    if not email_configured():
        return
    if RUN_IN_THREAD:
        threading.Thread(target=_send_safely, args=(booking_id,), name="ticket-email", daemon=True).start()
    else:
        _send_safely(booking_id)


def send_pending_confirmations(db: Session, now: datetime | None = None, limit: int = 50) -> int:
    """Sweeper: send emails for recently paid bookings that do not have one yet (the instant send failed,
    or the server restarted before it ran)."""
    if not email_configured():
        return 0
    now = now or datetime.now(UTC)
    ids = db.scalars(
        select(Booking.id)
        .join(User, User.id == Booking.user_id)
        .where(
            Booking.status == "confirmed",
            Booking.ticket_code.is_not(None),
            Booking.confirmation_emailed_at.is_(None),
            Booking.paid_at >= now - CATCH_UP_WINDOW,
            User.email.is_not(None),
        )
        .order_by(Booking.id)
        .limit(limit)
    ).all()
    sent = 0
    for booking_id in ids:
        try:
            sent += send_confirmation(db, booking_id)
        except DeliveryFailed:
            break  # the mail server is down: stop this round, try again next time
    return sent
