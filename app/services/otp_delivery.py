"""Sending one-time codes: SMS (Twilio or MSG91) and email (SMTP).

Nothing here is needed for the demo: with no provider configured and
OTP_DEV_MODE on, the code is simply shown on screen. For real delivery set the
provider's settings in .env (see README). Secrets are never logged and never
put in an error message.

HONEST LIMITS
    - SMS needs a paid account with a provider. Trial accounts usually only
      reach numbers you have verified, and in India commercial SMS needs a
      DLT-approved template (MSG91 and others). Twilio and MSG91 are written to
      their public docs but have NOT been run against a live account here.
    - Email works with any SMTP account (a Gmail "app password" is the easy free
      route). It is tested against a local mail server, not a real inbox.
"""
import logging
import smtplib
from email.message import EmailMessage

import httpx

from app.config import settings

log = logging.getLogger("otp.delivery")


class DeliveryFailed(Exception):
    """The provider refused or could not be reached."""


def sms_configured() -> bool:
    p = settings.otp_sms_provider
    if p == "twilio":
        return bool(settings.twilio_account_sid and settings.twilio_auth_token and settings.twilio_from)
    if p == "msg91":
        return bool(settings.msg91_auth_key and settings.msg91_template_id)
    return False


def email_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def mask(identity: str) -> str:
    """+919876543210 -> +91******3210 ; someone@gmail.com -> so****@gmail.com"""
    if "@" in identity:
        name, _, domain = identity.partition("@")
        return f"{name[:2]}{'*' * max(2, len(name) - 2)}@{domain}"
    return f"{identity[:3]}{'*' * max(2, len(identity) - 7)}{identity[-4:]}"


def _message(code: str) -> str:
    minutes = max(1, settings.otp_ttl_seconds // 60)
    return f"{settings.otp_brand}: your verification code is {code}. It expires in {minutes} minutes. Do not share it with anyone."


def send_sms(phone: str, code: str) -> None:
    try:
        if settings.otp_sms_provider == "twilio":
            data = {"To": phone, "Body": _message(code)}
            data["MessagingServiceSid" if settings.twilio_from.startswith("MG") else "From"] = settings.twilio_from
            r = httpx.post(
                f"https://api.twilio.com/2010-04-01/Accounts/{settings.twilio_account_sid}/Messages.json",
                auth=(settings.twilio_account_sid, settings.twilio_auth_token), data=data, timeout=15,
            )
            ok = r.status_code in (200, 201)
        elif settings.otp_sms_provider == "msg91":
            r = httpx.post(
                "https://control.msg91.com/api/v5/otp",
                params={"template_id": settings.msg91_template_id, "mobile": phone.lstrip("+"),
                        "authkey": settings.msg91_auth_key, "otp": code,
                        "otp_expiry": max(1, settings.otp_ttl_seconds // 60)},
                timeout=15,
            )
            ok = r.status_code == 200 and r.json().get("type") == "success"
        else:
            raise DeliveryFailed("no SMS provider configured")
    except httpx.HTTPError as exc:
        raise DeliveryFailed("could not reach the SMS provider") from exc
    if not ok:
        log.warning("SMS provider %s rejected the message (HTTP %s)", settings.otp_sms_provider, r.status_code)
        raise DeliveryFailed("the SMS provider rejected the message")


def send_email(address: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"Your {settings.otp_brand} verification code"
    msg["From"] = settings.smtp_from
    msg["To"] = address
    msg.set_content(_message(code))
    try:
        smtp_cls = smtplib.SMTP_SSL if settings.smtp_port == 465 else smtplib.SMTP
        with smtp_cls(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_port != 465 and settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)
    except (OSError, smtplib.SMTPException) as exc:
        log.warning("email delivery failed: %s", exc.__class__.__name__)   # class only: no addresses or passwords
        raise DeliveryFailed("could not send the email") from exc
