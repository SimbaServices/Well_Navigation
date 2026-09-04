"""Upsert partition results into per-state SQLite tables."""

from __future__ import annotations

import sqlite3

from wellnav.ingest.classify import utcnow
from wellnav.states import permits_table, wells_table

IDENTITY_UPDATE_FIELDS = [
    "well_name", "well_no", "lease_name", "lease_no",
    "district", "operator", "operator_number", "field",
]

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


def _values(record: dict, fields: list[str]) -> list:
    return [record.get(name) for name in fields]


def upsert_wells(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    table = wells_table(state)
    placeholders = ",".join("?" * len(WELL_FIELDS))
    cols = ",".join(WELL_FIELDS)
    updates = ",".join(
        f"{name}=excluded.{name}"
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
        f"{name}=excluded.{name}"
        for name in PERMIT_FIELDS
        if name not in {"api8", "permit_no", "first_seen_at", "migrated_at"}
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


def update_identity(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    """COALESCE identity onto existing wells/permits. Blanks do not wipe data."""
    if not rows:
        return 0
    now = utcnow()
    wtable = wells_table(state)
    ptable = permits_table(state)
    well_sql = f"""
        UPDATE {wtable}
        SET well_name=COALESCE(NULLIF(?, ''), well_name),
            well_no=COALESCE(NULLIF(?, ''), well_no),
            lease_name=COALESCE(NULLIF(?, ''), lease_name),
            lease_no=COALESCE(NULLIF(?, ''), lease_no),
            district=COALESCE(NULLIF(?, ''), district),
            operator=COALESCE(NULLIF(?, ''), operator),
            operator_number=COALESCE(NULLIF(?, ''), operator_number),
            field=COALESCE(NULLIF(?, ''), field),
            updated_at=?
        WHERE api8=?
    """
    permit_sql = f"""
        UPDATE {ptable}
        SET well_name=COALESCE(NULLIF(?, ''), well_name),
            well_no=COALESCE(NULLIF(?, ''), well_no),
            lease_name=COALESCE(NULLIF(?, ''), lease_name),
            lease_no=COALESCE(NULLIF(?, ''), lease_no),
            district=COALESCE(NULLIF(?, ''), district),
            operator=COALESCE(NULLIF(?, ''), operator),
            operator_number=COALESCE(NULLIF(?, ''), operator_number),
            updated_at=?
        WHERE api8=? AND status NOT IN ('migrated')
    """
    updated = 0
    for row in rows:
        api8 = (row.get("api8") or row.get("api") or "")
        if len(api8) > 8:
            api8 = api8[-8:]
        if len(api8) != 8:
            continue
        values = [row.get(name) or "" for name in IDENTITY_UPDATE_FIELDS]
        wcur = conn.execute(well_sql, (*values, now, api8))
        pcur = conn.execute(permit_sql, (*values[:-1], now, api8))
        if wcur.rowcount or pcur.rowcount:
            updated += 1
    return updated
