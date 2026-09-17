"""Weekly RRC EWA drilling-permit (W-1) refresh."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from wellnav.db import connect, get_cursor, get_meta, init_schema, session, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import upsert_ewa_permits
from wellnav.parsers import format_api
from wellnav.rrc import CLIENT
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, TX_COUNTY_NAME

TX_TZ = ZoneInfo("America/Chicago")
PERMIT_CURSOR = "tx_permits_refreshed_at"
DEFAULT_LOOKBACK_DAYS = 7
STATUS_MAP = {
    "APPROVED": "approved",
    "CANCELLED": "cancelled",
    "CANCELED": "cancelled",
    "EXPIRED": "expired",
}


def _today_tx(now: datetime | None = None) -> date:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(TX_TZ).date()


def _parse_cursor_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date()
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(TX_TZ).date()


def _parse_user_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Dates must be MM/DD/YYYY or YYYY-MM-DD, not {value!r}")


def rrc_date(value: date) -> str:
    return value.strftime("%m/%d/%Y")


def approved_interval(
    last_request_at: str | None,
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[str, str]:
    """Approved-date window: last permit request (or lookback) through today."""
    today = _today_tx(now)
    end = _parse_user_date(to_date) or today
    start = _parse_user_date(from_date)
    if start is None:
        start = _parse_cursor_date(last_request_at) or (today - timedelta(days=lookback_days))
    if start > end:
        start = end
    return rrc_date(start), rrc_date(end)


def _mdy_to_iso(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return raw


def ewa_row_to_permit(row: dict, *, now: str, lifetime_days: int) -> dict | None:
    api8 = (row.get("api") or "").strip()
    if len(api8) != 8:
        return None
    lease_name = (row.get("lease_name") or "").strip()
    well_no = (row.get("well_no") or "").strip()
    well_name = f"{lease_name} #{well_no}".strip(" #") if lease_name or well_no else format_api(api8)
    approved_iso = _mdy_to_iso(row.get("approved_at")) or now[:10]
    try:
        approved_day = datetime.fromisoformat(approved_iso).date()
    except ValueError:
        approved_day = _today_tx()
    expires = (approved_day + timedelta(days=lifetime_days)).isoformat()
    status_raw = (row.get("status") or "APPROVED").strip().upper()
    profile = (row.get("profile") or "").strip().lower()
    county_code = (row.get("county_code") or api8[:3]).zfill(3)
    return {
        "api": f"42{api8}",
        "api8": api8,
        "permit_no": (row.get("permit_no") or "").strip() or f"EWA-{api8}",
        "status": STATUS_MAP.get(status_raw, status_raw.lower() or "approved"),
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease_name,
        "lease_no": "",
        "county": (row.get("county") or "").strip() or TX_COUNTY_NAME.get(county_code, ""),
        "county_code": county_code,
        "district": (row.get("district") or "").strip(),
        "operator": (row.get("operator") or "").strip(),
        "operator_number": (row.get("operator_number") or "").strip(),
        "profile": profile,
        "symbol": None,
        "symnum": None,
        "wellhead_lat": None,
        "wellhead_lon": None,
        "wellhead_crs": None,
        "approved_at": approved_iso,
        "submitted_at": _mdy_to_iso(row.get("submitted_at")),
        "expires_at": expires,
        "lifetime_days": lifetime_days,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "rrc_ewa",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def refresh_permits(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
    state: str = "tx",
) -> dict:
    """Pull newly approved W-1 permits for the window since the last request."""
    if (state or "tx").lower() == "ok":
        from wellnav.ingest.ok_wells import refresh_ok_permits

        return refresh_ok_permits()
    if (state or "tx").lower() == "nm":
        from wellnav.ingest.nm_wells import refresh_nm_permits

        return refresh_nm_permits()
    now = utcnow()
    conn = connect()
    init_schema(conn)
    lifetime = int(get_meta(conn, "permit_lifetime_days", str(DEFAULT_PERMIT_LIFETIME_DAYS)))
    last = get_cursor(conn, PERMIT_CURSOR)
    approved_from, approved_to = approved_interval(last, from_date=from_date, to_date=to_date)
    cur = conn.execute(
        """
        INSERT INTO sync_jobs(kind, state, status, workers, started_at)
        VALUES (?, ?, 'running', ?, ?)
        """,
        ("permit_refresh", state, 1, now),
    )
    job_id = cur.lastrowid
    conn.commit()
    conn.close()

    print(
        f"refresh-permits job {job_id}: approved {approved_from} .. {approved_to}",
        flush=True,
    )
    try:
        raw = CLIENT.search_drilling_permits(
            approved_from=approved_from,
            approved_to=approved_to,
        )
        records = []
        for row in raw.get("permits") or []:
            record = ewa_row_to_permit(row, now=now, lifetime_days=lifetime)
            if record:
                records.append(record)
        with session() as db:
            stored = upsert_ewa_permits(db, state, records)
            db.execute(
                """
                UPDATE sync_jobs SET status = ?, finished_at = ?, message = ?
                WHERE id = ?
                """,
                (
                    "ok",
                    utcnow(),
                    (
                        f"{stored} permits, RRC total {raw.get('total') or 0}, "
                        f"{approved_from}..{approved_to}"
                    ),
                    job_id,
                ),
            )
            set_cursor(db, PERMIT_CURSOR, utcnow(), utcnow())
        stats = {
            "status": "ok",
            "job_id": job_id,
            "permits": stored,
            "total": int(raw.get("total") or 0),
            "approved_from": approved_from,
            "approved_to": approved_to,
        }
        print(f"refresh-permits job {job_id} finished: {stats}", flush=True)
        return stats
    except Exception as exc:
        finished = utcnow()
        with session() as db:
            db.execute(
                """
                UPDATE sync_jobs SET status = ?, finished_at = ?, message = ?
                WHERE id = ?
                """,
                ("failed", finished, str(exc), job_id),
            )
        print(f"refresh-permits job {job_id} failed: {exc}", flush=True)
        return {
            "status": "failed",
            "job_id": job_id,
            "permits": 0,
            "error": str(exc),
            "approved_from": approved_from,
            "approved_to": approved_to,
        }
