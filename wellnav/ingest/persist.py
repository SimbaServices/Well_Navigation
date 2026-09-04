"""Upsert partition results into per-state SQLite tables."""

from __future__ import annotations

import sqlite3

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
