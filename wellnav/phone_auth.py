"""Email OTP for signup and password recovery; password-only sign-in."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from wellnav import accounts as account_store
from wellnav.accounts import UserStore, utcnow, utcnow_iso
from wellnav.auth import (
    OTP_MAX_ATTEMPTS,
    OTP_MAX_SENDS_PER_HOUR,
    OTP_RESEND_SECONDS,
    OTP_TTL_SECONDS,
    DUMMY_PASSWORD_HASH,
    hash_otp,
    hash_password,
    mask_email,
    new_otp_code,
    validate_email,
    validate_password,
    verify_otp,
    verify_password,
)
from wellnav.sms import otp_message, send_sms

PENDING_TTL_SECONDS = 24 * 60 * 60


def _secret(length: int = 24) -> str:
    import secrets

    return secrets.token_urlsafe(length)


class PhoneAuth:
    def __init__(self, users: UserStore | None = None) -> None:
        self.users = users or UserStore()

    def start_signup(self, username: str, password: str, phone_raw: str = "") -> tuple[dict | None, str | None]:
        email, email_err = validate_email(username)
        if email_err or not email:
            return None, email_err or "Enter a valid email address."
        pass_err = validate_password(password)
        if pass_err:
            return None, pass_err
        if self.users.by_username(email):
            return None, "That email is already registered."
        conn = account_store._conn()
        self._purge(conn)
        conn.execute("DELETE FROM pending_signups WHERE username = ? OR phone = ?", (email, email))
        pending_id = _secret()
        conn.execute(
            """
            INSERT INTO pending_signups(id, username, password_hash, phone, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (pending_id, email, hash_password(password), email, utcnow_iso()),
        )
        challenge, error = self._issue(
            conn,
            purpose="register",
            phone=email,
            pending_id=pending_id,
        )
        conn.commit()
        if error or not challenge:
            return None, error or "Could not send the verification code."
        return challenge, None

    def start_login(self, username: str, password: str) -> tuple[dict | None, str | None]:
        user = self.users.authenticate(username, password)
        if not user:
            return None, "Email or password is incorrect."
        return user, None

    def start_add_phone(self, user_id: int, phone_raw: str) -> tuple[dict | None, str | None]:
        return None, "Use your email address to verify this account."

    def start_recovery(self, username: str, phone_raw: str = "") -> tuple[dict | None, str | None]:
        email, email_err = validate_email(username)
        if email_err or not email:
            return None, email_err or "Enter a valid email address."
        row = self.users.by_username(email)
        if not row:
            verify_password(" ", DUMMY_PASSWORD_HASH)
            return None, "No account matches that email address."
        destination = (row.get("email") or row.get("username") or "").strip().lower()
        if destination != email:
            return None, "No account matches that email address."
        conn = account_store._conn()
        self._purge(conn)
        challenge, error = self._issue(conn, purpose="recovery", phone=email, user_id=row["id"])
        conn.commit()
        if error or not challenge:
            return None, error or "Could not send the verification code."
        challenge["pending_uid"] = row["id"]
        return challenge, None

    def resend(self, challenge_id: str) -> tuple[dict | None, str | None]:
        conn = account_store._conn()
        self._purge(conn)
        row = conn.execute("SELECT * FROM otp_challenges WHERE id = ?", (challenge_id,)).fetchone()
        if not row:
            return None, "Request a new code to continue."
        challenge, error = self._issue(
            conn,
            purpose=row["purpose"],
            phone=row["phone"],
            pending_id=row["pending_id"],
            user_id=row["user_id"],
            replace_id=row["id"],
        )
        conn.commit()
        if error or not challenge:
            return None, error or "Could not send the verification code."
        if row["user_id"]:
            challenge["pending_uid"] = row["user_id"]
        return challenge, None

    def verify(self, challenge_id: str, code: str) -> tuple[dict | None, str | None]:
        conn = account_store._conn()
        row = conn.execute("SELECT * FROM otp_challenges WHERE id = ?", (challenge_id,)).fetchone()
        self._purge(conn)
        if not row:
            conn.commit()
            return None, "Request a new code to continue."
        if row["expires_at"] <= utcnow_iso():
            conn.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))
            conn.commit()
            return None, "That code has expired. Request a new one."
        if int(row["attempts"]) >= OTP_MAX_ATTEMPTS:
            conn.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))
            conn.commit()
            return None, "Too many incorrect tries. Request a new code."
        if not verify_otp(code, row["code_hash"]):
            conn.execute(
                "UPDATE otp_challenges SET attempts = attempts + 1 WHERE id = ?",
                (challenge_id,),
            )
            conn.commit()
            left = OTP_MAX_ATTEMPTS - int(row["attempts"]) - 1
            if left <= 0:
                conn.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))
                conn.commit()
                return None, "Too many incorrect tries. Request a new code."
            return None, "That code is incorrect."
        purpose = row["purpose"]
        try:
            if purpose == "register":
                user, error = self._finish_register(conn, row)
            elif purpose in {"login", "recovery"}:
                user, error = self._finish_login(conn, row)
            elif purpose == "add_phone":
                user, error = self._finish_add_phone(conn, row)
            else:
                user, error = None, "Request a new code to continue."
        except sqlite3.IntegrityError:
            conn.rollback()
            return None, "That email is already registered."
        if error or not user:
            conn.commit()
            return None, error or "Could not finish verification."
        conn.execute("DELETE FROM otp_challenges WHERE id = ?", (challenge_id,))
        conn.commit()
        return user, None

    def get_challenge(self, challenge_id: str) -> dict | None:
        if not challenge_id:
            return None
        row = account_store._conn().execute(
            "SELECT * FROM otp_challenges WHERE id = ?",
            (challenge_id,),
        ).fetchone()
        if not row or row["expires_at"] <= utcnow_iso():
            return None
        return dict(row)

    def challenge_view(self, challenge: dict) -> dict:
        return {
            "challenge_id": challenge["id"],
            "purpose": challenge["purpose"],
            "phone_hint": mask_email(challenge["phone"]),
            "expires_minutes": OTP_TTL_SECONDS // 60,
        }

    def _finish_register(self, conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict | None, str | None]:
        pending = conn.execute(
            "SELECT * FROM pending_signups WHERE id = ?",
            (row["pending_id"],),
        ).fetchone()
        if not pending:
            return None, "That sign-up expired. Start again."
        if self.users.by_username(pending["username"]):
            return None, "That email is already registered."
        user, error = self.users.create_from_pending(
            username=pending["username"],
            password_hash=pending["password_hash"],
            phone=pending["phone"],
        )
        conn.execute("DELETE FROM pending_signups WHERE id = ?", (pending["id"],))
        return user, error

    def _finish_login(self, conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict | None, str | None]:
        user = self.users.get(int(row["user_id"]))
        if not user:
            return None, "Request a new code to continue."
        destination = (user.get("email") or user.get("username") or "").strip().lower()
        if destination != (row["phone"] or "").strip().lower():
            return None, "Request a new code to continue."
        return user, None

    def _finish_add_phone(self, conn: sqlite3.Connection, row: sqlite3.Row) -> tuple[dict | None, str | None]:
        taken = self.users.by_phone(row["phone"])
        if taken and taken["id"] != int(row["user_id"]):
            return None, "That phone number is already registered."
        return self.users.set_phone(int(row["user_id"]), row["phone"])

    def _issue(
        self,
        conn: sqlite3.Connection,
        *,
        purpose: str,
        phone: str,
        pending_id: str | None = None,
        user_id: int | None = None,
        replace_id: str | None = None,
    ) -> tuple[dict | None, str | None]:
        hour_ago = (utcnow() - timedelta(hours=1)).isoformat()
        sent = conn.execute(
            "SELECT COUNT(*) AS n FROM otp_challenges WHERE phone = ? AND sent_at > ?",
            (phone, hour_ago),
        ).fetchone()["n"]
        if int(sent) >= OTP_MAX_SENDS_PER_HOUR:
            return None, "Too many codes sent to this number. Try again in an hour."
        if replace_id:
            latest = conn.execute(
                "SELECT sent_at FROM otp_challenges WHERE id = ?",
                (replace_id,),
            ).fetchone()
            if latest:
                earliest = (utcnow() - timedelta(seconds=OTP_RESEND_SECONDS)).isoformat()
                if latest["sent_at"] > earliest:
                    wait = OTP_RESEND_SECONDS
                    return None, f"Wait {wait} seconds before requesting another code."
            conn.execute("DELETE FROM otp_challenges WHERE id = ?", (replace_id,))
        code = new_otp_code()
        try:
            send_sms(phone, otp_message(code))
        except Exception:
            return None, "Could not send the verification email. Try again in a minute."
        now = utcnow()
        challenge_id = _secret()
        payload = {
            "id": challenge_id,
            "purpose": purpose,
            "pending_id": pending_id,
            "user_id": user_id,
            "phone": phone,
            "code_hash": hash_otp(code),
            "expires_at": (now + timedelta(seconds=OTP_TTL_SECONDS)).isoformat(),
            "sent_at": now.isoformat(),
            "created_at": now.isoformat(),
        }
        conn.execute(
            """
            INSERT INTO otp_challenges(
                id, purpose, pending_id, user_id, phone, code_hash,
                expires_at, attempts, sent_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
            """,
            (
                payload["id"],
                payload["purpose"],
                payload["pending_id"],
                payload["user_id"],
                payload["phone"],
                payload["code_hash"],
                payload["expires_at"],
                payload["sent_at"],
                payload["created_at"],
            ),
        )
        return payload, None

    def _purge(self, conn: sqlite3.Connection) -> None:
        now = utcnow_iso()
        stale_pending = (utcnow() - timedelta(seconds=PENDING_TTL_SECONDS)).isoformat()
        conn.execute("DELETE FROM otp_challenges WHERE expires_at <= ?", (now,))
        conn.execute("DELETE FROM pending_signups WHERE created_at <= ?", (stale_pending,))


PHONE_AUTH = PhoneAuth()
