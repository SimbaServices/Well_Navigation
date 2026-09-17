"""Send one-time passcodes as texts via SMTP email-to-SMS, or Twilio."""

from __future__ import annotations

import os
import re
import smtplib
import ssl
from collections.abc import Callable
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import urlencode

import requests

from wellnav.db import ROOT

SmsSender = Callable[[str, str], None]
_sender: SmsSender | None = None

US_SMS_GATEWAYS = (
    "vtext.com",
    "txt.att.net",
    "tmomail.net",
    "messaging.sprintpcs.com",
    "vmobl.com",
    "msg.fi.google.com",
    "sms.myboostmobile.com",
    "mms.cricketwireless.net",
)


def set_sender(fn: SmsSender | None) -> None:
    global _sender
    _sender = fn


def smtp_configured() -> bool:
    return bool(
        (os.environ.get("WELLNAV_SMTP_USER") or "").strip()
        and (os.environ.get("WELLNAV_SMTP_PASSWORD") or "").strip()
    )


def sms_configured() -> bool:
    return smtp_configured() or bool(
        (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
        and (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
        and (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    )


def otp_message(code: str) -> str:
    return f"Well Navigation code: {code}. Expires in 10 minutes."


def sms_gateway_addresses(phone: str) -> list[str]:
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) != 10:
        raise ValueError("A 10-digit US mobile number is required to send a text.")
    return [f"{digits}@{domain}" for domain in US_SMS_GATEWAYS]


def send_sms(destination: str, body: str) -> None:
    if _sender is not None:
        _sender(destination, body)
        return
    if smtp_configured() and "@" in (destination or ""):
        _send_direct_email(destination, body)
        return
    if smtp_configured():
        _send_email_sms(destination, body)
        return
    if _twilio_configured():
        _send_twilio(destination, body)
        return
    _log_sms(destination, body)


def _send_direct_email(to: str, body: str) -> None:
    sender = (os.environ.get("WELLNAV_SMTP_FROM") or os.environ.get("WELLNAV_SMTP_USER") or "").strip()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = "Well Navigation verification code"
    message.set_content(body)
    _send_smtp(message, [to])
    _log_sms(to, body)


def _twilio_configured() -> bool:
    return bool(
        (os.environ.get("TWILIO_ACCOUNT_SID") or "").strip()
        and (os.environ.get("TWILIO_AUTH_TOKEN") or "").strip()
        and (os.environ.get("TWILIO_FROM_NUMBER") or "").strip()
    )


def _send_email_sms(phone: str, body: str) -> None:
    recipients = sms_gateway_addresses(phone)
    sender = (os.environ.get("WELLNAV_SMTP_FROM") or os.environ.get("WELLNAV_SMTP_USER") or "").strip()
    message = EmailMessage()
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Subject"] = "Well Navigation"
    message.set_content(body)
    _send_smtp(message, recipients)
    _log_sms(phone, body)


def _smtp_usernames(user: str) -> list[str]:
    names = [user]
    if "@" in user:
        local = user.split("@", 1)[0].strip()
        if local and local not in names:
            names.append(local)
    return [name for name in names if name]


def _login_smtp(smtp: smtplib.SMTP, user: str, password: str) -> str:
    last: Exception | None = None
    for name in _smtp_usernames(user):
        try:
            smtp.login(name, password)
            return name
        except smtplib.SMTPAuthenticationError as exc:
            last = exc
    if last:
        raise last
    raise smtplib.SMTPAuthenticationError(535, b"Authentication credentials invalid.")


def _send_smtp(message: EmailMessage, recipients: list[str]) -> None:
    host = (os.environ.get("WELLNAV_SMTP_HOST") or "mail.simba.services").strip()
    port = int((os.environ.get("WELLNAV_SMTP_PORT") or "587").strip() or "587")
    user = (os.environ.get("WELLNAV_SMTP_USER") or "").strip()
    password = os.environ.get("WELLNAV_SMTP_PASSWORD") or ""
    sender = (os.environ.get("WELLNAV_SMTP_FROM") or user).strip()
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as smtp:
                _login_smtp(smtp, user, password)
                smtp.send_message(message, from_addr=sender, to_addrs=recipients)
            return
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            _login_smtp(smtp, user, password)
            smtp.send_message(message, from_addr=sender, to_addrs=recipients)
    except (OSError, smtplib.SMTPException) as exc:
        raise RuntimeError("Could not send the verification text.") from exc


def _send_twilio(phone: str, body: str) -> None:
    sid = os.environ["TWILIO_ACCOUNT_SID"].strip()
    token = os.environ["TWILIO_AUTH_TOKEN"].strip()
    source = os.environ["TWILIO_FROM_NUMBER"].strip()
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    response = requests.post(
        url,
        auth=(sid, token),
        data=urlencode({"From": source, "To": phone, "Body": body}),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=20,
    )
    if response.status_code >= 400:
        raise RuntimeError("Could not send the verification text.")


def _log_sms(phone: str, body: str) -> None:
    path = Path(os.environ.get("WELLNAV_SMS_LOG") or ROOT / "data" / "sms.log")
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{stamp} {phone} {body}\n")
