"""Try common SMTP login combinations without printing the password."""

from __future__ import annotations

import os
import smtplib
import socket
import ssl

USER = os.environ.get("WELLNAV_SMTP_USER", "").strip()
PASSWORD = os.environ.get("WELLNAV_SMTP_PASSWORD", "")
HOST = os.environ.get("WELLNAV_SMTP_HOST", "mail.simba.services").strip()
USERS = [value for value in (USER, USER.split("@")[0] if "@" in USER else "") if value]


def _ehlo(host: str, port: int) -> None:
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=12)
            smtp.ehlo()
            if smtp.has_extn("starttls"):
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
        print(f"BANNER {host}:{port} {smtp.ehlo_resp!r}")
        print(f"AUTH {host}:{port} {smtp.esmtp_features.get('auth')!r}")
        smtp.quit()
    except Exception as exc:
        print(f"EHLO_FAIL {host}:{port} {type(exc).__name__} {exc}")


def _login(host: str, port: int, user: str) -> None:
    try:
        if port == 465:
            smtp = smtplib.SMTP_SSL(host, port, timeout=12, context=ssl.create_default_context())
        else:
            smtp = smtplib.SMTP(host, port, timeout=12)
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
        smtp.login(user, PASSWORD)
        print(f"LOGIN_OK {host}:{port} user={user}")
        smtp.quit()
    except Exception as exc:
        print(f"LOGIN_FAIL {host}:{port} user={user} {type(exc).__name__} {exc}")


def main() -> None:
    print("password_len", len(PASSWORD))
    print("env_user", USER)
    for port in (587, 465, 25):
        _ehlo(HOST, port)
    for port in (587, 465):
        for user in USERS:
            _login(HOST, port, user)


if __name__ == "__main__":
    main()
