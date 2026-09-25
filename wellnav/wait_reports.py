"""Disposal facility wait-time and open-lanes community reports."""

from __future__ import annotations

import statistics
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from wellnav.db import ROOT

WAIT_REPORTS_DB_PATH = ROOT / "data" / "wait_reports.db"

REPORT_KINDS = frozenset({"actual", "partial", "estimated"})
REPORT_STATUSES = frozenset({"active", "hidden", "reviewed"})

DEFAULT_WINDOW_HOURS = 24
MIN_WINDOW_HOURS = 1
MAX_WINDOW_HOURS = 168  # 7 days
MAX_INTERVAL = timedelta(hours=24)
OPEN_LANES_MIN = 0
OPEN_LANES_MAX = 20
MAX_NOTES_LEN = 280
MAX_FLAG_REASON_LEN = 280

# Clock skew grace when comparing client "now" to arrival/departure.
FUTURE_SKEW = timedelta(seconds=120)

AUTO_FLAG_MEDIAN_FACTOR = 3.0
AUTO_FLAG_ABS_DELTA_MINUTES = 60
LANES_OUTLIER_DELTA = 8
RECENT_SAMPLE_LIMIT = 50
SUMMARY_RECENT_LIMIT = 12


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else WAIT_REPORTS_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            disposal_site_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            org_id INTEGER,
            report_kind TEXT NOT NULL,
            arrival_at TEXT NOT NULL,
            departure_at TEXT,
            open_lanes INTEGER,
            wait_minutes INTEGER,
            notes TEXT,
            created_at TEXT NOT NULL,
            is_flagged INTEGER NOT NULL DEFAULT 0,
            flag_reason TEXT,
            auto_flagged INTEGER NOT NULL DEFAULT 0,
            flagged_by_user_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active'
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reports_site_created
        ON reports(disposal_site_id, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reports_site_kind_status
        ON reports(disposal_site_id, report_kind, status)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_reports_flagged
        ON reports(is_flagged, auto_flagged, created_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_prefs (
            user_id INTEGER PRIMARY KEY,
            avg_window_hours INTEGER NOT NULL DEFAULT 24,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS report_flags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (report_id) REFERENCES reports(id)
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_report_flags_report
        ON report_flags(report_id, created_at DESC)
        """
    )
    conn.commit()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _to_iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(raw: str | None, *, field: str) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO timestamp.") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def _clamp_window(hours: int | float | str | None) -> int:
    try:
        value = int(hours)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        value = DEFAULT_WINDOW_HOURS
    return max(MIN_WINDOW_HOURS, min(MAX_WINDOW_HOURS, value))


def _normalize_notes(raw: str | None) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    if len(text) > MAX_NOTES_LEN:
        raise ValueError(f"notes must be at most {MAX_NOTES_LEN} characters.")
    return text


def _normalize_lanes(raw: int | str | None) -> int | None:
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("open_lanes must be an integer between 0 and 20.") from exc
    if value < OPEN_LANES_MIN or value > OPEN_LANES_MAX:
        raise ValueError("open_lanes must be an integer between 0 and 20.")
    return value


def _validate_times(
    kind: str,
    arrival_at: str | None,
    departure_at: str | None,
    *,
    now: datetime,
) -> tuple[datetime, datetime | None, int | None]:
    arrival = _parse_iso(arrival_at, field="arrival_at")
    departure = _parse_iso(departure_at, field="departure_at")

    if kind == "partial":
        if arrival is None:
            raise ValueError("Partial reports require arrival_at.")
        if departure is not None:
            raise ValueError("Partial reports cannot include departure_at.")
        if arrival > now + FUTURE_SKEW:
            raise ValueError("arrival_at cannot be in the future for partial reports.")
        return arrival, None, None

    if kind == "actual":
        if arrival is None:
            raise ValueError("Actual reports require arrival_at.")
        if departure is None:
            raise ValueError("Actual reports require departure_at. Use arrival-only for partial reports.")
        if arrival > now + FUTURE_SKEW:
            raise ValueError("arrival_at cannot be in the future for actual reports.")
        if departure > now + FUTURE_SKEW:
            raise ValueError("departure_at cannot be in the future for actual reports.")
        if departure < arrival:
            raise ValueError("departure_at must be on or after arrival_at.")
        if departure - arrival > MAX_INTERVAL:
            raise ValueError("Wait interval cannot exceed 24 hours.")
        wait_minutes = int(round((departure - arrival).total_seconds() / 60.0))
        return arrival, departure, wait_minutes

    # estimated — both ends required and must be in the future (org-shareable forecast).
    if arrival is None:
        raise ValueError("Estimated reports require a future arrival_at.")
    if departure is None:
        raise ValueError("Estimated reports require a future departure_at.")
    if arrival <= now - FUTURE_SKEW:
        raise ValueError("Estimated arrival_at must be in the future.")
    if departure <= now - FUTURE_SKEW:
        raise ValueError("Estimated departure_at must be in the future.")
    if departure < arrival:
        raise ValueError("departure_at must be on or after arrival_at.")
    if departure - arrival > MAX_INTERVAL:
        raise ValueError("Wait interval cannot exceed 24 hours.")
    wait_minutes = int(round((departure - arrival).total_seconds() / 60.0))
    return arrival, departure, wait_minutes


def _report_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "disposal_site_id": int(row["disposal_site_id"]),
        "user_id": int(row["user_id"]),
        "org_id": int(row["org_id"]) if row["org_id"] is not None else None,
        "report_kind": row["report_kind"],
        "arrival_at": row["arrival_at"],
        "departure_at": row["departure_at"],
        "open_lanes": int(row["open_lanes"]) if row["open_lanes"] is not None else None,
        "wait_minutes": int(row["wait_minutes"]) if row["wait_minutes"] is not None else None,
        "notes": row["notes"] or "",
        "created_at": row["created_at"],
        "is_flagged": bool(row["is_flagged"]),
        "flag_reason": row["flag_reason"] or "",
        "auto_flagged": bool(row["auto_flagged"]),
        "flagged_by_user_id": (
            int(row["flagged_by_user_id"]) if row["flagged_by_user_id"] is not None else None
        ),
        "status": row["status"],
    }


def _site_wait_samples(
    conn: sqlite3.Connection,
    site_id: int,
    *,
    window_hours: int,
    exclude_id: int | None = None,
) -> list[int]:
    cutoff = _to_iso(_utc_now() - timedelta(hours=window_hours))
    params: list[Any] = [int(site_id), cutoff]
    exclude_sql = ""
    if exclude_id is not None:
        exclude_sql = " AND id != ?"
        params.append(int(exclude_id))
    rows = conn.execute(
        f"""
        SELECT wait_minutes FROM reports
        WHERE disposal_site_id = ?
          AND status = 'active'
          AND report_kind IN ('actual', 'partial')
          AND wait_minutes IS NOT NULL
          AND created_at >= ?
          {exclude_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, RECENT_SAMPLE_LIMIT),
    ).fetchall()
    return [int(row["wait_minutes"]) for row in rows]


def _site_lane_samples(
    conn: sqlite3.Connection,
    site_id: int,
    *,
    window_hours: int,
    exclude_id: int | None = None,
) -> list[int]:
    cutoff = _to_iso(_utc_now() - timedelta(hours=window_hours))
    params: list[Any] = [int(site_id), cutoff]
    exclude_sql = ""
    if exclude_id is not None:
        exclude_sql = " AND id != ?"
        params.append(int(exclude_id))
    rows = conn.execute(
        f"""
        SELECT open_lanes FROM reports
        WHERE disposal_site_id = ?
          AND status = 'active'
          AND open_lanes IS NOT NULL
          AND created_at >= ?
          {exclude_sql}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (*params, RECENT_SAMPLE_LIMIT),
    ).fetchall()
    return [int(row["open_lanes"]) for row in rows]


def _auto_flag_reason(
    conn: sqlite3.Connection,
    *,
    site_id: int,
    wait_minutes: int | None,
    open_lanes: int | None,
    window_hours: int = DEFAULT_WINDOW_HOURS,
    exclude_id: int | None = None,
) -> str | None:
    reasons: list[str] = []

    if wait_minutes is not None and wait_minutes < 0:
        reasons.append("negative wait_minutes")

    if wait_minutes is not None and wait_minutes > int(MAX_INTERVAL.total_seconds() // 60):
        reasons.append("wait_minutes exceeds 24 hours")

    waits = _site_wait_samples(
        conn, site_id, window_hours=window_hours, exclude_id=exclude_id
    )
    if wait_minutes is not None and len(waits) >= 3:
        median = float(statistics.median(waits))
        delta = abs(wait_minutes - median)
        if median > 0 and wait_minutes > AUTO_FLAG_MEDIAN_FACTOR * median and delta > AUTO_FLAG_ABS_DELTA_MINUTES:
            reasons.append(
                f"wait_minutes {wait_minutes} is an outlier vs median {median:.0f}"
            )
        elif median == 0 and wait_minutes > AUTO_FLAG_ABS_DELTA_MINUTES:
            reasons.append(
                f"wait_minutes {wait_minutes} is an outlier vs near-zero recent waits"
            )

    lanes = _site_lane_samples(
        conn, site_id, window_hours=window_hours, exclude_id=exclude_id
    )
    if open_lanes is not None and len(lanes) >= 3:
        typical = float(statistics.median(lanes))
        if abs(open_lanes - typical) >= LANES_OUTLIER_DELTA:
            reasons.append(
                f"open_lanes {open_lanes} far from typical {typical:.0f}"
            )

    if open_lanes is not None and (open_lanes < OPEN_LANES_MIN or open_lanes > OPEN_LANES_MAX):
        reasons.append("open_lanes out of valid range")

    return "; ".join(reasons) if reasons else None


def create_report(
    *,
    disposal_site_id: int,
    user_id: int,
    report_kind: str,
    arrival_at: str | None = None,
    departure_at: str | None = None,
    open_lanes: int | str | None = None,
    notes: str | None = None,
    org_id: int | None = None,
    now: str | None = None,
    path: Path | None = None,
) -> dict[str, Any]:
    """Create a wait/open-lanes report. Raises ValueError on invalid input."""
    kind = (report_kind or "").strip().lower()
    if kind not in REPORT_KINDS:
        raise ValueError("report_kind must be actual, partial, or estimated.")

    try:
        site_id = int(disposal_site_id)
        uid = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("disposal_site_id and user_id must be integers.") from exc
    if site_id <= 0 or uid <= 0:
        raise ValueError("disposal_site_id and user_id must be positive integers.")

    org: int | None
    if org_id is None or org_id == "":
        org = None
    else:
        try:
            org = int(org_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("org_id must be an integer.") from exc

    if kind == "estimated":
        if org is None:
            raise ValueError(
                "Estimated reports require a team membership so they can be shared with your organization."
            )
        if org <= 0:
            raise ValueError("org_id must be a positive integer for estimated reports.")

    reference = _parse_iso(now, field="now") if now else _utc_now()
    assert reference is not None

    arrival_dt, departure_dt, wait_minutes = _validate_times(
        kind, arrival_at, departure_at, now=reference
    )
    lanes = _normalize_lanes(open_lanes)
    note_text = _normalize_notes(notes)
    created = _to_iso(_utc_now())

    conn = connect(path)
    try:
        init_schema(conn)
        flag_reason = _auto_flag_reason(
            conn,
            site_id=site_id,
            wait_minutes=wait_minutes,
            open_lanes=lanes,
        )
        auto_flagged = 1 if flag_reason else 0
        is_flagged = auto_flagged
        cur = conn.execute(
            """
            INSERT INTO reports (
                disposal_site_id, user_id, org_id, report_kind,
                arrival_at, departure_at, open_lanes, wait_minutes, notes,
                created_at, is_flagged, flag_reason, auto_flagged,
                flagged_by_user_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'active')
            """,
            (
                site_id,
                uid,
                org,
                kind,
                _to_iso(arrival_dt),
                _to_iso(departure_dt) if departure_dt else None,
                lanes,
                wait_minutes,
                note_text,
                created,
                is_flagged,
                flag_reason,
                auto_flagged,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (cur.lastrowid,)).fetchone()
        assert row is not None
        return _report_row(row)
    finally:
        conn.close()


def flag_report(
    report_id: int,
    user_id: int,
    reason: str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    """Record a user flag that a report looks errant."""
    try:
        rid = int(report_id)
        uid = int(user_id)
    except (TypeError, ValueError) as exc:
        raise ValueError("report_id and user_id must be integers.") from exc
    text = (reason or "").strip()
    if not text:
        raise ValueError("A flag reason is required.")
    if len(text) > MAX_FLAG_REASON_LEN:
        raise ValueError(f"reason must be at most {MAX_FLAG_REASON_LEN} characters.")

    conn = connect(path)
    try:
        init_schema(conn)
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
        if not row:
            raise ValueError("That wait report was not found.")
        created = _to_iso(_utc_now())
        conn.execute(
            """
            INSERT INTO report_flags (report_id, user_id, reason, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (rid, uid, text, created),
        )
        existing_reason = (row["flag_reason"] or "").strip()
        merged = text if not existing_reason else f"{existing_reason}; {text}"
        if len(merged) > MAX_FLAG_REASON_LEN * 2:
            merged = merged[: MAX_FLAG_REASON_LEN * 2]
        conn.execute(
            """
            UPDATE reports
            SET is_flagged = 1,
                flag_reason = ?,
                flagged_by_user_id = ?
            WHERE id = ?
            """,
            (merged, uid, rid),
        )
        conn.commit()
        updated = conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()
        assert updated is not None
        return _report_row(updated)
    finally:
        conn.close()


def get_user_pref(user_id: int, *, path: Path | None = None) -> dict[str, Any]:
    uid = int(user_id)
    conn = connect(path)
    try:
        init_schema(conn)
        row = conn.execute(
            "SELECT user_id, avg_window_hours, updated_at FROM user_prefs WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if not row:
            return {
                "user_id": uid,
                "avg_window_hours": DEFAULT_WINDOW_HOURS,
                "updated_at": None,
            }
        return {
            "user_id": int(row["user_id"]),
            "avg_window_hours": _clamp_window(row["avg_window_hours"]),
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def set_user_pref(
    user_id: int,
    avg_window_hours: int | float | str,
    *,
    path: Path | None = None,
) -> dict[str, Any]:
    uid = int(user_id)
    hours = _clamp_window(avg_window_hours)
    stamp = _to_iso(_utc_now())
    conn = connect(path)
    try:
        init_schema(conn)
        conn.execute(
            """
            INSERT INTO user_prefs (user_id, avg_window_hours, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                avg_window_hours = excluded.avg_window_hours,
                updated_at = excluded.updated_at
            """,
            (uid, hours, stamp),
        )
        conn.commit()
        return {"user_id": uid, "avg_window_hours": hours, "updated_at": stamp}
    finally:
        conn.close()


def get_site_wait_summary(
    site_id: int,
    *,
    window_hours: int | float | str | None = None,
    org_id: int | None = None,
    path: Path | None = None,
    now: datetime | str | None = None,
) -> dict[str, Any]:
    sid = int(site_id)
    hours = _clamp_window(window_hours if window_hours is not None else DEFAULT_WINDOW_HOURS)
    if now is None:
        clock = _utc_now()
    elif isinstance(now, datetime):
        clock = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        clock = clock.astimezone(timezone.utc).replace(microsecond=0)
    else:
        clock = _parse_iso(str(now), field="now")
        assert clock is not None
    cutoff = _to_iso(clock - timedelta(hours=hours))
    now_iso = _to_iso(clock)

    conn = connect(path)
    try:
        init_schema(conn)
        wait_rows = conn.execute(
            """
            SELECT wait_minutes FROM reports
            WHERE disposal_site_id = ?
              AND status = 'active'
              AND report_kind IN ('actual', 'partial')
              AND wait_minutes IS NOT NULL
              AND is_flagged = 0
              AND created_at >= ?
            """,
            (sid, cutoff),
        ).fetchall()
        waits = [int(row["wait_minutes"]) for row in wait_rows]
        avg_wait = round(sum(waits) / len(waits), 1) if waits else None

        count_row = conn.execute(
            """
            SELECT COUNT(*) AS n FROM reports
            WHERE disposal_site_id = ?
              AND status = 'active'
              AND report_kind IN ('actual', 'partial')
              AND created_at >= ?
            """,
            (sid, cutoff),
        ).fetchone()
        report_count = int(count_row["n"]) if count_row else 0

        lanes_row = conn.execute(
            """
            SELECT open_lanes FROM reports
            WHERE disposal_site_id = ?
              AND status = 'active'
              AND open_lanes IS NOT NULL
              AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """,
            (sid, cutoff),
        ).fetchone()
        open_lanes_latest = int(lanes_row["open_lanes"]) if lanes_row else None

        recent = conn.execute(
            """
            SELECT * FROM reports
            WHERE disposal_site_id = ?
              AND status = 'active'
              AND report_kind IN ('actual', 'partial')
              AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (sid, cutoff, SUMMARY_RECENT_LIMIT),
        ).fetchall()

        estimated: list[dict[str, Any]] = []
        if org_id is not None:
            est_rows = conn.execute(
                """
                SELECT * FROM reports
                WHERE disposal_site_id = ?
                  AND status = 'active'
                  AND report_kind = 'estimated'
                  AND org_id = ?
                  AND (
                    arrival_at >= ?
                    OR (departure_at IS NOT NULL AND departure_at >= ?)
                  )
                ORDER BY arrival_at ASC
                LIMIT ?
                """,
                (sid, int(org_id), now_iso, now_iso, SUMMARY_RECENT_LIMIT),
            ).fetchall()
            estimated = [_report_row(row) for row in est_rows]

        return {
            "disposal_site_id": sid,
            "avg_wait_minutes": avg_wait,
            "report_count": report_count,
            "open_lanes_latest": open_lanes_latest,
            "recent_reports": [_report_row(row) for row in recent],
            "window_hours": hours,
            "estimated_for_org": estimated,
        }
    finally:
        conn.close()


def list_flagged_reports(
    limit: int = 50,
    *,
    path: Path | None = None,
) -> list[dict[str, Any]]:
    try:
        cap = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        cap = 50
    conn = connect(path)
    try:
        init_schema(conn)
        rows = conn.execute(
            """
            SELECT * FROM reports
            WHERE is_flagged = 1 OR auto_flagged = 1
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (cap,),
        ).fetchall()
        return [_report_row(row) for row in rows]
    finally:
        conn.close()
