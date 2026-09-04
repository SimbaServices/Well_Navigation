"""Users, saved wells, and per-user response cache."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from wellnav.auth import hash_password, validate_password, validate_username, verify_password
from wellnav.parsers import format_api, normalize_api
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


class UserStore:
    def get(self, user_id: int) -> dict | None:
        return _row(
            _conn().execute(
                "SELECT id, username, created_at FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        )

    def by_username(self, username: str) -> dict | None:
        return _row(
            _conn().execute(
                "SELECT id, username, password_hash, created_at FROM users WHERE username = ?",
                (username.strip(),),
            ).fetchone()
        )

    def register(self, username: str, password: str) -> tuple[dict | None, str | None]:
        name_err = validate_username(username)
        if name_err:
            return None, name_err
        pass_err = validate_password(password)
        if pass_err:
            return None, pass_err
        if self.by_username(username):
            return None, "That username is already taken."
        conn = _conn()
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES (?, ?, ?)",
            (username.strip(), hash_password(password), utcnow_iso()),
        )
        conn.commit()
        user = self.get(int(cur.lastrowid))
        return user, None

    def authenticate(self, username: str, password: str) -> dict | None:
        row = self.by_username(username)
        if not row or not verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "created_at": row["created_at"]}


class SavedWells:
    def api_set(self, user_id: int) -> set[str]:
        rows = _conn().execute(
            "SELECT api8 FROM saved_wells WHERE user_id = ?",
            (user_id,),
        ).fetchall()
        return {row["api8"] for row in rows}

    def list(self, user_id: int) -> list[dict]:
        rows = _conn().execute(
            """
            SELECT api8, well_name, well_no, lease_name, county, operator, saved_at
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
            item["api_display"] = format_api(row["api8"])
            out.append(item)
        return out

    def is_saved(self, user_id: int, api8: str) -> bool:
        row = _conn().execute(
            "SELECT 1 FROM saved_wells WHERE user_id = ? AND api8 = ?",
            (user_id, api8),
        ).fetchone()
        return row is not None

    def save(self, user_id: int, well: dict) -> dict:
        _, _, eight = normalize_api(well.get("api") or well.get("api8") or "")
        conn = _conn()
        conn.execute(
            """
            INSERT INTO saved_wells(
                user_id, api8, well_name, well_no, lease_name, county, operator, saved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, api8) DO UPDATE SET
                well_name = excluded.well_name,
                well_no = excluded.well_no,
                lease_name = excluded.lease_name,
                county = excluded.county,
                operator = excluded.operator
            """,
            (
                user_id,
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
        return {"api": eight, "saved": True}

    def remove(self, user_id: int, api: str) -> dict:
        _, _, eight = normalize_api(api)
        conn = _conn()
        conn.execute(
            "DELETE FROM saved_wells WHERE user_id = ? AND api8 = ?",
            (user_id, eight),
        )
        conn.commit()
        return {"api": eight, "saved": False}

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
