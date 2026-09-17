"""Users, saved wells, and per-user response cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from wellnav.billing import complimentary_domain
from wellnav.auth import (
    DUMMY_PASSWORD_HASH,
    email_domain,
    hash_password,
    mask_email,
    org_key,
    validate_email,
    validate_password,
    verify_password,
)
from wellnav.parsers import format_api, normalize_api
from wellnav.states import state_from_api
from wellnav.repository import REPO

SEARCH_TTL = 30 * 60
OPERATOR_TTL = 30 * 60
LOCATION_TTL = 24 * 60 * 60
RECENT_TTL = 7 * 24 * 60 * 60
RECENT_LIMIT = 20


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def utcnow_iso() -> str:
    return utcnow().isoformat()


def _conn() -> sqlite3.Connection:
    return REPO.conn


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


def _public_stamp(raw: object) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _public_user(row: dict | None) -> dict | None:
    if not row:
        return None
    email = (row.get("email") or row.get("username") or "").strip().lower()
    role = (row.get("role") or "member").strip() or "member"
    return {
        "id": row["id"],
        "username": email or row["username"],
        "email": email,
        "domain": email_domain(email),
        "org_key": org_key(email) if email else "",
        "org_id": row.get("org_id"),
        "role": role,
        "is_admin": role == "admin",
        "phone": row.get("phone") or "",
        "email_hint": mask_email(email),
        "created_at": row["created_at"],
        "last_login_at": _public_stamp(row.get("last_login_at")),
        "last_activity_at": _public_stamp(row.get("last_activity_at")),
        "session_version": int(row.get("session_version") or 0),
    }


_USER_COLS = (
    "id, username, email, password_hash, phone, org_id, role, "
    "email_verified_at, phone_verified_at, created_at, last_login_at, last_activity_at, "
    "session_version"
)
ACTIVITY_TOUCH_SECONDS = 60


class UserStore:
    def get(self, user_id: int) -> dict | None:
        return _public_user(
            _row(
                _conn().execute(
                    f"SELECT {_USER_COLS} FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            )
        )

    def by_username(self, username: str) -> dict | None:
        email, _ = validate_email(username)
        needle = email or (username or "").strip()
        if not needle:
            return None
        return _row(
            _conn().execute(
                f"SELECT {_USER_COLS} FROM users WHERE username = ? OR email = ?",
                (needle, needle),
            ).fetchone()
        )

    def by_phone(self, phone: str) -> dict | None:
        return _public_user(
            _row(
                _conn().execute(
                    f"SELECT {_USER_COLS} FROM users WHERE phone = ? OR email = ?",
                    (phone, phone),
                ).fetchone()
            )
        )

    def create_from_pending(
        self, *, username: str, password_hash: str, phone: str
    ) -> tuple[dict | None, str | None]:
        email, error = validate_email(username)
        if error or not email:
            return None, error or "Enter a valid email address."
        org_id, role = self.ensure_org(email)
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO users(
                username, password_hash, email, phone, email_verified_at,
                org_id, role, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, password_hash, email, phone or None, utcnow_iso(), org_id, role, utcnow_iso()),
        )
        conn.commit()
        return self.get(int(cur.lastrowid)), None

    def ensure_org(self, email: str) -> tuple[int, str]:
        key = org_key(email)
        conn = _conn()
        row = conn.execute(
            "SELECT id FROM organizations WHERE domain = ?",
            (key,),
        ).fetchone()
        if row:
            if complimentary_domain(key):
                conn.execute(
                    "UPDATE organizations SET billing_status = 'complimentary', seat_count = 10000 "
                    "WHERE id = ? AND billing_status != 'complimentary'",
                    (int(row["id"]),),
                )
                conn.commit()
            return int(row["id"]), "member"
        complimentary = complimentary_domain(key)
        try:
            cur = conn.execute(
                """
                INSERT INTO organizations(
                    domain, name, created_at, billing_status, seat_count
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    key,
                    "Simba Services" if complimentary else key,
                    utcnow_iso(),
                    "complimentary" if complimentary else "none",
                    10000 if complimentary else 0,
                ),
            )
            conn.commit()
            return int(cur.lastrowid), "admin"
        except sqlite3.IntegrityError:
            conn.rollback()
            row = conn.execute(
                "SELECT id FROM organizations WHERE domain = ?",
                (key,),
            ).fetchone()
            return int(row["id"]), "member"

    def org(self, org_id: int | None) -> dict | None:
        if not org_id:
            return None
        return _row(
            _conn().execute(
                "SELECT * FROM organizations WHERE id = ?",
                (org_id,),
            ).fetchone()
        )

    def org_members(self, org_id: int) -> list[dict]:
        rows = _conn().execute(
            f"SELECT {_USER_COLS} FROM users WHERE org_id = ? ORDER BY role DESC, username",
            (org_id,),
        ).fetchall()
        return [_public_user(dict(row)) for row in rows]

    def admin_count(self, org_id: int) -> int:
        return int(
            _conn().execute(
                "SELECT COUNT(*) AS n FROM users WHERE org_id = ? AND role = 'admin'",
                (org_id,),
            ).fetchone()["n"]
        )

    def set_member_role(self, admin: dict, member_id: int, role: str) -> tuple[dict | None, str | None]:
        if not admin.get("is_admin") or not admin.get("org_id"):
            return None, "Only an organization admin can change roles."
        if role not in {"admin", "member"}:
            return None, "That role is not allowed."
        member = self.get(member_id)
        if not member or member.get("org_id") != admin["org_id"]:
            return None, "That person is not in your organization."
        if role == "member" and member.get("is_admin") and self.admin_count(admin["org_id"]) <= 1:
            return None, "Keep at least one admin on the organization."
        conn = _conn()
        conn.execute("UPDATE users SET role = ? WHERE id = ?", (role, member_id))
        conn.commit()
        return self.get(member_id), None

    def delete_account(self, user: dict, password: str) -> tuple[bool, str | None]:
        if not user:
            return False, "Sign in again to delete this account."
        row = self.by_username(user.get("email") or user.get("username") or "")
        if not row:
            return False, "That account was not found."
        if not verify_password(password or "", row["password_hash"]):
            return False, "Password is incorrect."
        uid = int(row["id"])
        email = (row.get("email") or row.get("username") or "").strip().lower()
        conn = _conn()
        conn.execute("DELETE FROM otp_challenges WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM saved_wells WHERE user_id = ?", (uid,))
        conn.execute("DELETE FROM user_cache WHERE user_id = ?", (uid,))
        if email:
            conn.execute("DELETE FROM pending_signups WHERE username = ?", (email,))
        conn.execute("DELETE FROM users WHERE id = ?", (uid,))
        conn.commit()
        return True, None

    def remove_member(self, admin: dict, member_id: int) -> tuple[bool, str | None]:
        if not admin.get("is_admin") or not admin.get("org_id"):
            return False, "Only an organization admin can remove users."
        if int(admin["id"]) == int(member_id):
            return False, "You cannot remove your own account here."
        member = self.get(member_id)
        if not member or member.get("org_id") != admin["org_id"]:
            return False, "That person is not in your organization."
        if member.get("is_admin") and self.admin_count(admin["org_id"]) <= 1:
            return False, "Keep at least one admin on the organization."
        conn = _conn()
        conn.execute("DELETE FROM users WHERE id = ?", (member_id,))
        conn.commit()
        return True, None

    def set_password(self, user_id: int, password: str) -> tuple[dict | None, str | None]:
        pass_err = validate_password(password)
        if pass_err:
            return None, pass_err
        conn = _conn()
        conn.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (hash_password(password), user_id),
        )
        conn.commit()
        user = self.get(user_id)
        if not user:
            return None, "Sign in again and try that one more time."
        return user, None

    def set_phone(self, user_id: int, phone: str) -> tuple[dict | None, str | None]:
        conn = _conn()
        conn.execute(
            "UPDATE users SET phone = ?, phone_verified_at = ? WHERE id = ?",
            (phone, utcnow_iso(), user_id),
        )
        conn.commit()
        user = self.get(user_id)
        if not user:
            return None, "Sign in again and try that one more time."
        return user, None

    def register(
        self, username: str, password: str, phone: str = ""
    ) -> tuple[dict | None, str | None]:
        email, email_err = validate_email(username)
        if email_err or not email:
            return None, email_err or "Enter a valid email address."
        pass_err = validate_password(password)
        if pass_err:
            return None, pass_err
        if self.by_username(email):
            return None, "That email is already registered."
        org_id, role = self.ensure_org(email)
        conn = _conn()
        cur = conn.execute(
            """
            INSERT INTO users(
                username, password_hash, email, phone, email_verified_at,
                org_id, role, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (email, hash_password(password), email, phone or None, utcnow_iso(), org_id, role, utcnow_iso()),
        )
        conn.commit()
        return self.get(int(cur.lastrowid)), None

    def authenticate(self, username: str, password: str) -> dict | None:
        row = self.by_username(username)
        if not row:
            verify_password(password or " ", DUMMY_PASSWORD_HASH)
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        return _public_user(row)

    def rotate_session_version(self, user_id: int) -> int:
        conn = _conn()
        conn.execute(
            "UPDATE users SET session_version = COALESCE(session_version, 0) + 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT session_version FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("Account was not found.")
        return int(row["session_version"] or 1)

    def record_login(self, user_id: int) -> None:
        now = utcnow_iso()
        conn = _conn()
        try:
            conn.execute(
                "UPDATE users SET last_login_at = ?, last_activity_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()

    def touch_activity(self, user_id: int, *, min_interval: int = ACTIVITY_TOUCH_SECONDS) -> None:
        now = utcnow()
        cutoff = (now - timedelta(seconds=max(0, int(min_interval)))).isoformat()
        conn = _conn()
        try:
            conn.execute(
                """
                UPDATE users
                SET last_activity_at = ?
                WHERE id = ?
                  AND (last_activity_at IS NULL OR last_activity_at <= ?)
                """,
                (now.isoformat(), user_id, cutoff),
            )
            conn.commit()
        except sqlite3.OperationalError:
            conn.rollback()


def _saved_state(well: dict | None, api: str = "") -> str:
    raw = ((well or {}).get("state") or "").strip().lower()
    if raw in {"tx", "nm", "ok", "la"}:
        return raw
    return state_from_api(api or (well or {}).get("api") or (well or {}).get("api_full") or "") or "tx"


class SavedWells:
    def api_set(self, user_id: int) -> set[str]:
        rows = _conn().execute(
            "SELECT state, api8 FROM saved_wells WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        keys = {f"{row['state']}:{row['api8']}" for row in rows}
        keys.update(row["api8"] for row in rows if row["state"] == "tx")
        return keys

    def list(self, user_id: int) -> list[dict]:
        rows = _conn().execute(
            """
            SELECT state, api8, well_name, well_no, lease_name, county, operator, saved_at
            FROM saved_wells
            WHERE user_id = ?
            ORDER BY saved_at DESC
            """,
            (user_id,),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["api"] = row["api8"]
            item["state"] = row["state"] or "tx"
            item["api_display"] = format_api(row["api8"], row["state"] or "tx")
            out.append(item)
        return out

    def is_saved(self, user_id: int, api8: str, state: str = "tx") -> bool:
        row = _conn().execute(
            "SELECT 1 FROM saved_wells WHERE user_id = ? AND state = ? AND api8 = ?",
            (user_id, state or "tx", api8),
        ).fetchone()
        return row is not None

    def save(self, user_id: int, well: dict) -> dict:
        raw_api = well.get("api") or well.get("api8") or well.get("api_full") or ""
        _, _, eight = normalize_api(raw_api)
        state = _saved_state(well, raw_api)
        conn = _conn()
        conn.execute(
            """
            INSERT INTO saved_wells(
                user_id, state, api8, well_name, well_no, lease_name, county, operator, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, state, api8) DO UPDATE SET
                well_name = excluded.well_name,
                well_no = excluded.well_no,
                lease_name = excluded.lease_name,
                county = excluded.county,
                operator = excluded.operator
            """,
            (
                user_id,
                state,
                eight,
                well.get("well_name") or "",
                well.get("well_no") or "",
                well.get("lease_name") or "",
                well.get("county") or "",
                well.get("operator") or "",
                utcnow_iso(),
            ),
        )
        conn.commit()
        return {"api": eight, "state": state, "saved": True}

    def save_many(self, user_id: int, wells: list[dict]) -> dict:
        existing = self.api_set(user_id)
        added = 0
        kept = 0
        skipped = 0
        apis: list[str] = []
        seen: set[str] = set()
        for well in wells[:200]:
            try:
                _, _, eight = normalize_api(well.get("api") or well.get("api8") or "")
            except (TypeError, ValueError):
                skipped += 1
                continue
            if eight in seen:
                continue
            seen.add(eight)
            self.save(
                user_id,
                {
                    "api": eight,
                    "state": _saved_state(well, well.get("api") or eight),
                    "well_name": well.get("well_name") or well.get("name") or format_api(eight),
                    "well_no": well.get("well_no") or "",
                    "lease_name": well.get("lease_name") or well.get("lease") or "",
                    "county": well.get("county") or "",
                    "operator": well.get("operator") or "",
                },
            )
            apis.append(eight)
            if eight in existing:
                kept += 1
            else:
                added += 1
                existing.add(eight)
        return {"added": added, "kept": kept, "skipped": skipped, "apis": apis}

    def remove(self, user_id: int, api: str, state: str = "tx") -> dict:
        _, _, eight = normalize_api(api)
        st = (state or state_from_api(api) or "tx").strip().lower()
        conn = _conn()
        conn.execute(
            "DELETE FROM saved_wells WHERE user_id = ? AND state = ? AND api8 = ?",
            (user_id, st, eight),
        )
        conn.commit()
        return {"api": eight, "state": st, "saved": False}

    def count(self, user_id: int) -> int:
        return int(
            _conn().execute(
                "SELECT COUNT(*) AS n FROM saved_wells WHERE user_id = ?",
                (user_id,),
            ).fetchone()["n"]
        )


class UserCache:
    def peek(self, user_id: int, kind: str, cache_key: str) -> Any | None:
        self.purge_expired(user_id)
        row = _conn().execute(
            """
            SELECT payload FROM user_cache
            WHERE user_id = ? AND kind = ? AND cache_key = ? AND expires_at > ?
            """,
            (user_id, kind, cache_key, utcnow_iso()),
        ).fetchone()
        return json.loads(row["payload"]) if row else None

    def get(self, user_id: int, kind: str, cache_key: str) -> Any | None:
        self.purge_expired(user_id)
        conn = _conn()
        row = conn.execute(
            """
            SELECT id, payload FROM user_cache
            WHERE user_id = ? AND kind = ? AND cache_key = ? AND expires_at > ?
            """,
            (user_id, kind, cache_key, utcnow_iso()),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "UPDATE user_cache SET hits = hits + 1, last_hit_at = ? WHERE id = ?",
            (utcnow_iso(), row["id"]),
        )
        conn.commit()
        return json.loads(row["payload"])

    def set(self, user_id: int, kind: str, cache_key: str, payload: Any, ttl: int) -> None:
        now = utcnow()
        body = json.dumps(payload, separators=(",", ":"), default=str)
        expires = (now + timedelta(seconds=ttl)).isoformat()
        conn = _conn()
        conn.execute(
            """
            INSERT INTO user_cache(
                user_id, kind, cache_key, payload, hits, bytes, created_at, last_hit_at, expires_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?, ?, ?)
            ON CONFLICT(user_id, kind, cache_key) DO UPDATE SET
                payload = excluded.payload,
                bytes = excluded.bytes,
                created_at = excluded.created_at,
                last_hit_at = excluded.last_hit_at,
                expires_at = excluded.expires_at
            """,
            (user_id, kind, cache_key, body, len(body.encode("utf-8")), now.isoformat(), now.isoformat(), expires),
        )
        conn.commit()

    def remember_recent(self, user_id: int, entry: dict) -> None:
        recent = self.peek(user_id, "recent", "searches") or []
        key = (entry.get("mode"), entry.get("q"), entry.get("operator_number"))
        recent = [item for item in recent if (item.get("mode"), item.get("q"), item.get("operator_number")) != key]
        recent.insert(0, {**entry, "at": utcnow_iso()})
        self.set(user_id, "recent", "searches", recent[:RECENT_LIMIT], RECENT_TTL)

    def recent_searches(self, user_id: int) -> list[dict]:
        return list(self.peek(user_id, "recent", "searches") or [])

    def list_entries(self, user_id: int) -> list[dict]:
        self.purge_expired(user_id)
        rows = _conn().execute(
            """
            SELECT id, kind, cache_key, hits, bytes, created_at, last_hit_at, expires_at
            FROM user_cache
            WHERE user_id = ?
            ORDER BY last_hit_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def stats(self, user_id: int) -> dict:
        self.purge_expired(user_id)
        row = _conn().execute(
            """
            SELECT COUNT(*) AS entries,
                   COALESCE(SUM(hits), 0) AS hits,
                   COALESCE(SUM(bytes), 0) AS bytes
            FROM user_cache
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()
        return {
            "entries": int(row["entries"]),
            "hits": int(row["hits"]),
            "bytes": int(row["bytes"]),
        }

    def delete(self, user_id: int, entry_id: int) -> None:
        conn = _conn()
        conn.execute(
            "DELETE FROM user_cache WHERE user_id = ? AND id = ?",
            (user_id, entry_id),
        )
        conn.commit()

    def clear(self, user_id: int, kind: str | None = None) -> int:
        conn = _conn()
        if kind:
            cur = conn.execute(
                "DELETE FROM user_cache WHERE user_id = ? AND kind = ?",
                (user_id, kind),
            )
        else:
            cur = conn.execute("DELETE FROM user_cache WHERE user_id = ?", (user_id,))
        conn.commit()
        return int(cur.rowcount or 0)

    def purge_expired(self, user_id: int | None = None) -> int:
        conn = _conn()
        if user_id is None:
            cur = conn.execute("DELETE FROM user_cache WHERE expires_at <= ?", (utcnow_iso(),))
        else:
            cur = conn.execute(
                "DELETE FROM user_cache WHERE user_id = ? AND expires_at <= ?",
                (user_id, utcnow_iso()),
            )
        conn.commit()
        return int(cur.rowcount or 0)


USERS = UserStore()
SAVED = SavedWells()
CACHE = UserCache()
