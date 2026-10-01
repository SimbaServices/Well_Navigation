"""Approved-date windows for the weekly permitted-location refresh.

A normal run requests ``[cursor date, today]`` in America/Chicago for each
state, or the last ``DEFAULT_LOOKBACK_DAYS`` when ``{state}_permits_refreshed_at``
is missing. A multi-year backfill is explicit: the caller passes ``from_date``,
``to_date``, and ``replace=True``. This module does not choose a two-year window.
"""

from __future__ import annotations

import importlib
import sqlite3
from datetime import date, datetime, timedelta

from wellnav.db import get_cursor, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.ingest.permits import DEFAULT_LOOKBACK_DAYS, _parse_cursor_date, _today_tx
from wellnav.ingest.persist import upsert_permits
from wellnav.states import APP_STATES, normalize_state, permits_table, wells_table

_SAVEPOINT = "wellnav_permit_store"

# {table} is permits_{state} or wells_{state} from wellnav.states.
DELETE_PERMITS_SQL = "DELETE FROM {table}"
DELETE_PERMITTED_WELLS_SQL = (
    "DELETE FROM {table} WHERE symbol = 'Permitted' OR symnum IN (2, 9)"
)

_DEFAULT_FETCHERS = {
    "tx": ("wellnav.ingest.tx_permit_pull", "fetch_texas_permits"),
    "nm": ("wellnav.ingest.nm_permit_pull", "fetch_new_mexico_permits"),
    "ok": ("wellnav.ingest.ok_permit_pull", "fetch_oklahoma_permits"),
    "la": ("wellnav.ingest.la_permit_pull", "fetch_louisiana_permits"),
}


def _require_state(state: str) -> str:
    code = normalize_state(state)
    if code not in APP_STATES:
        raise ValueError(f"No permit schedule for state: {state}")
    return code


def _cursor_name(state: str) -> str:
    return f"{state}_permits_refreshed_at"


def _cursor_day(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    # A bare YYYY-MM-DD is already a calendar date. Parsing it as an instant
    # would treat midnight UTC as the previous evening in America/Chicago.
    if len(raw) == 10:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date()
        except ValueError:
            return None
    return _parse_cursor_date(raw)


def _is_iso_day(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def window_for_state(
    last_updated: str | None,
    *,
    now: datetime | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
) -> tuple[date, date]:
    """Return the inclusive approved-date window for one state.

    End is today in America/Chicago unless ``to_date`` is set. Start is
    ``from_date``, else the cursor calendar date in America/Chicago, else
    today minus ``lookback_days``. When start is after end, start becomes end.
    """
    today = _today_tx(now)
    end = today if to_date is None else to_date
    if from_date is not None:
        start = from_date
    else:
        start = _cursor_day(last_updated)
        if start is None:
            start = today - timedelta(days=lookback_days)
    if start > end:
        start = end
    return start, end


def _begin(conn: sqlite3.Connection) -> None:
    if not conn.in_transaction:
        conn.execute("BEGIN")


def _rollback_store(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
        conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
    except sqlite3.Error:
        conn.rollback()


def store_permits(
    conn: sqlite3.Connection,
    state: str,
    rows: list[dict],
    *,
    replace: bool,
) -> dict:
    """Upsert permits and advance the cursor only after the upsert succeeds.

    ``replace=False`` upserts and leaves existing permits and wells in place.
    ``replace=True`` deletes every row in ``permits_{state}`` and permitted
    wells (``symbol = 'Permitted'`` or ``symnum`` 2 or 9), then upserts.
    Drilled symbols such as Oil Well stay. Rows are not inserted into ``wells_*``.
    ``approved_at`` must be YYYY-MM-DD; a null wellhead latitude does not drop a row.
    """
    code = _require_state(state)
    kept = [row for row in rows if _is_iso_day(row.get("approved_at"))]
    deleted_permits = 0
    deleted_permitted_wells = 0
    _begin(conn)
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")
    try:
        if replace:
            deleted_permits = conn.execute(
                DELETE_PERMITS_SQL.format(table=permits_table(code))
            ).rowcount
            deleted_permitted_wells = conn.execute(
                DELETE_PERMITTED_WELLS_SQL.format(table=wells_table(code))
            ).rowcount
        stored = upsert_permits(conn, code, kept)
        finished = utcnow()
        set_cursor(conn, _cursor_name(code), finished, finished)
        conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")
        conn.commit()
    except Exception:
        _rollback_store(conn)
        raise
    return {
        "state": code,
        "replace": bool(replace),
        "deleted_permits": deleted_permits,
        "deleted_permitted_wells": deleted_permitted_wells,
        "stored": stored,
        "cursor": finished,
    }


def refresh_permitted_locations(
    conn: sqlite3.Connection,
    states: tuple[str, ...] = ("tx", "nm", "ok", "la"),
    *,
    now: datetime | None = None,
    from_date: date | None = None,
    to_date: date | None = None,
    replace: bool = False,
    fetchers: dict | None = None,
) -> dict:
    """Fetch each state's approved-date window and store the rows.

    ``fetchers`` maps a state code to ``fetcher(start, end) -> list[dict]``.
    When a state is omitted, its fetcher is imported on first use. A fetcher
    that raises is recorded as ``status='failed'`` and does not delete permits
    or move that state's cursor. Other states still run.
    """
    supplied: dict = {}
    if fetchers:
        for key, func in fetchers.items():
            supplied[_require_state(key)] = func

    results: dict = {}
    for raw in states:
        code = _require_state(raw)
        name = _cursor_name(code)
        start, end = window_for_state(
            get_cursor(conn, name),
            now=now,
            from_date=from_date,
            to_date=to_date,
        )
        fetcher = supplied.get(code)
        if fetcher is None:
            module_name, func_name = _DEFAULT_FETCHERS[code]
            module = importlib.import_module(module_name)
            fetcher = getattr(module, func_name)
        try:
            rows = fetcher(start, end)
        except Exception as exc:
            results[code] = {
                "status": "failed",
                "state": code,
                "start": start,
                "end": end,
                "replace": bool(replace),
                "deleted_permits": 0,
                "deleted_permitted_wells": 0,
                "stored": 0,
                "cursor": get_cursor(conn, name),
                "error": str(exc),
            }
            continue
        outcome = store_permits(conn, code, list(rows or []), replace=replace)
        outcome["status"] = "ok"
        outcome["start"] = start
        outcome["end"] = end
        results[code] = outcome
    return results
