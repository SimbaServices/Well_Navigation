"""Upsert partition results into per-state SQLite tables."""

from __future__ import annotations

import sqlite3

from wellnav.ingest.classify import utcnow
from wellnav.states import permits_table, wells_table

WELL_FIELDS = [
    "api", "api8", "well_name", "well_no", "lease_name", "lease_no", "county",
    "county_code", "district", "operator", "operator_number", "field", "well_type",
    "symbol", "symnum", "profile", "wellhead_lat", "wellhead_lon", "wellhead_crs",
    "toe_lat", "toe_lon", "toe_crs", "location_kind", "location_source",
    "gis_lat83", "gis_long83", "gis_lat27", "gis_long27", "source",
    "migrated_from_permit", "first_seen_at", "last_seen_at", "updated_at",
]

PERMIT_FIELDS = [
    "api", "api8", "permit_no", "status", "well_name", "well_no", "lease_name",
    "lease_no", "county", "county_code", "district", "operator", "operator_number",
    "profile", "symbol", "symnum", "wellhead_lat", "wellhead_lon", "wellhead_crs",
    "approved_at", "submitted_at", "expires_at", "lifetime_days", "as_drilled_ready",
    "migrated_at", "source", "first_seen_at", "last_seen_at", "updated_at",
]

# GIS records ship these as empty strings. Never let a blank excluded value
# clobber lease/operator identity that EWA already wrote.
KEEP_IF_BLANK = (
    "lease_name",
    "lease_no",
    "district",
    "operator",
    "operator_number",
    "field",
)

IDENTITY_FIELDS = (
    "well_name",
    "well_no",
    "lease_name",
    "lease_no",
    "district",
    "operator",
    "operator_number",
    "field",
)

# permits_tx has no field column
PERMIT_IDENTITY_FIELDS = tuple(name for name in IDENTITY_FIELDS if name != "field")


def _values(record: dict, fields: list[str]) -> list:
    return [record.get(name) for name in fields]


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _api8(row: dict) -> str:
    raw = row.get("api8") or row.get("api") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("42") and len(digits) >= 10:
        digits = digits[2:]
    return digits[:8]


def _keep_assign(table: str, name: str) -> str:
    if name in KEEP_IF_BLANK:
        return (
            f"{name}=CASE WHEN excluded.{name} IS NULL OR excluded.{name}='' "
            f"THEN {table}.{name} ELSE excluded.{name} END"
        )
    return f"{name}=excluded.{name}"


def upsert_wells(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    table = wells_table(state)
    placeholders = ",".join("?" * len(WELL_FIELDS))
    cols = ",".join(WELL_FIELDS)
    updates = ",".join(
        _keep_assign(table, name)
        for name in WELL_FIELDS
        if name not in {"api", "first_seen_at", "migrated_from_permit"}
    )
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(api) DO UPDATE SET {updates}"
    )
    payload = []
    for row in rows:
        item = dict(row)
        item.setdefault("migrated_from_permit", 0)
        payload.append(_values(item, WELL_FIELDS))
    conn.executemany(sql, payload)
    return len(rows)


def upsert_permits(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    table = permits_table(state)
    placeholders = ",".join("?" * len(PERMIT_FIELDS))
    cols = ",".join(PERMIT_FIELDS)
    updates = ",".join(
        _keep_assign(table, name)
        for name in PERMIT_FIELDS
        if name not in {"api8", "permit_no", "first_seen_at", "migrated_at", "status"}
    )
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(api8, permit_no) DO UPDATE SET {updates}, "
        f"status=CASE WHEN {table}.status='migrated' THEN {table}.status ELSE excluded.status END"
    )
    payload = []
    for row in rows:
        item = dict(row)
        item.setdefault("permit_no", f"GIS-{item.get('api8')}")
        item.setdefault("as_drilled_ready", 0)
        item.setdefault("migrated_at", None)
        payload.append(_values(item, PERMIT_FIELDS))
    conn.executemany(sql, payload)
    return len(rows)


def _identity_set_sql(table: str, fields: tuple[str, ...]) -> str:
    assigns = [
        f"{name}=CASE WHEN ? != '' THEN ? ELSE {table}.{name} END"
        for name in fields
    ]
    assigns.append("updated_at=?")
    return ", ".join(assigns)


def _identity_params(incoming: dict, fields: tuple[str, ...], now: str, api8: str) -> tuple:
    params: list = []
    for name in fields:
        value = _text(incoming.get(name))
        params.extend((value, value))
    params.extend((now, api8))
    return tuple(params)


def update_identity(
    conn: sqlite3.Connection,
    state: str,
    rows: list[dict],
    *,
    county_code: str | None = None,
) -> tuple[int, int]:
    """Fill blank identity columns, or replace them when incoming is non-empty.

    UPDATE is keyed by api8 and never writes wellhead/toe coordinates.
    Optional county_code restricts the match to that partition.
    """
    if not rows:
        return 0, 0
    now = utcnow()
    wells = wells_table(state)
    permits = permits_table(state)
    well_where = "api8=?"
    permit_where = "api8=? AND status NOT IN ('migrated')"
    extra: list = []
    if county_code:
        well_where += " AND county_code=?"
        permit_where += " AND county_code=?"
        extra = [county_code]
    well_sql = f"UPDATE {wells} SET {_identity_set_sql(wells, IDENTITY_FIELDS)} WHERE {well_where}"
    permit_sql = (
        f"UPDATE {permits} SET {_identity_set_sql(permits, PERMIT_IDENTITY_FIELDS)} "
        f"WHERE {permit_where}"
    )
    well_hits = 0
    permit_hits = 0
    seen: set[str] = set()
    for row in rows:
        api8 = _api8(row)
        if len(api8) != 8 or api8 in seen:
            continue
        seen.add(api8)
        incoming = {name: _text(row.get(name)) for name in IDENTITY_FIELDS}
        if not any(incoming.values()):
            continue
        well_params = _identity_params(incoming, IDENTITY_FIELDS, now, api8)
        permit_params = _identity_params(incoming, PERMIT_IDENTITY_FIELDS, now, api8)
        well_hits += conn.execute(well_sql, well_params + tuple(extra)).rowcount
        permit_hits += conn.execute(permit_sql, permit_params + tuple(extra)).rowcount
    return well_hits, permit_hits
