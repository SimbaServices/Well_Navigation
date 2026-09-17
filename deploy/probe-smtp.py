"""Confirm the Well Navigation SMTP mailbox can send mail."""

from __future__ import annotations

import os
from email.message import EmailMessage

from wellnav.sms import _send_smtp, smtp_configured


def main() -> None:
    if not smtp_configured():
        raise SystemExit("FAIL smtp env is missing")
    user = os.environ["WELLNAV_SMTP_USER"].strip()
    message = EmailMessage()
    message["From"] = os.environ.get("WELLNAV_SMTP_FROM") or user
    message["To"] = user
    message["Subject"] = "Well Navigation SMTP check"
    message.set_content("SMTP login and send from the Well Navigation host succeeded.")
    _send_smtp(message, [user])
    print("OK smtp_send", user)


if __name__ == "__main__":
    main()
