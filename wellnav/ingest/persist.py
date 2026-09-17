"""Upsert partition results into per-state SQLite tables."""

from __future__ import annotations

import sqlite3

from wellnav.operators import normalize_operator_name, standardize_operator_name
from wellnav.states import operators_table, permits_table, wells_table

WELL_FIELDS = [
    "api", "api8", "well_name", "well_no", "lease_name", "lease_no", "county",
    "county_code", "district", "operator", "operator_number", "field", "well_type",
    "symbol", "symnum", "profile", "wellhead_lat", "wellhead_lon", "wellhead_crs",
    "toe_lat", "toe_lon", "toe_crs", "location_kind", "location_source",
    "gis_lat83", "gis_long83", "gis_lat27", "gis_long27", "source",
    "migrated_from_permit", "first_seen_at", "last_seen_at", "updated_at",
]

OPERATOR_FIELDS = [
    "operator_number", "operator_name", "oil", "gas", "org_status", "org_type",
    "status", "wells", "error", "updated_at",
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


def _with_normalized_operator(record: dict) -> dict:
    item = dict(record)
    if item.get("operator"):
        item["operator"] = standardize_operator_name(item["operator"])
    return item


KEEP_IF_BLANK = {
    "lease_name",
    "lease_no",
    "district",
    "operator",
    "operator_number",
    "field",
}


def _conflict_set(fields: list[str], protected: set[str], table: str) -> str:
    parts = []
    for name in fields:
        if name in protected:
            continue
        if name == "well_name":
            parts.append(
                f"{name}=CASE WHEN TRIM(COALESCE(excluded.lease_name,''))!='' "
                f"THEN excluded.{name} ELSE {table}.{name} END"
            )
        elif name in KEEP_IF_BLANK:
            parts.append(
                f"{name}=CASE WHEN TRIM(COALESCE(excluded.{name},''))!='' "
                f"THEN excluded.{name} ELSE {table}.{name} END"
            )
        else:
            parts.append(f"{name}=excluded.{name}")
    return ",".join(parts)


def upsert_operators(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    """Insert operator catalog rows with the same columns as operators_tx."""
    if not rows:
        return 0
    table = operators_table(state)
    placeholders = ",".join("?" * len(OPERATOR_FIELDS))
    cols = ",".join(OPERATOR_FIELDS)
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(operator_number) DO UPDATE SET "
        f"operator_name=COALESCE(NULLIF(excluded.operator_name,''), {table}.operator_name), "
        f"oil=MAX({table}.oil, excluded.oil), "
        f"gas=MAX({table}.gas, excluded.gas), "
        f"org_status=COALESCE(NULLIF(excluded.org_status,''), {table}.org_status), "
        f"org_type=COALESCE(NULLIF(excluded.org_type,''), {table}.org_type), "
        f"status=COALESCE(NULLIF(excluded.status,''), {table}.status), "
        f"wells=MAX({table}.wells, excluded.wells), "
        f"error=excluded.error, "
        f"updated_at=excluded.updated_at"
    )
    payload = []
    for row in rows:
        item = dict(row)
        if item.get("operator_name"):
            item["operator_name"] = standardize_operator_name(item["operator_name"])
        item.setdefault("oil", 0)
        item.setdefault("gas", 0)
        item.setdefault("org_status", "")
        item.setdefault("org_type", "")
        item.setdefault("status", "queued")
        item.setdefault("wells", 0)
        item.setdefault("error", None)
        payload.append(_values(item, OPERATOR_FIELDS))
    conn.executemany(sql, payload)
    return len(rows)


def apply_operator_catalog(conn: sqlite3.Connection, state: str) -> dict:
    """Fill blank well/permit operator fields from operators_{state}."""
    from wellnav.ingest.classify import utcnow

    ot = operators_table(state)
    now = utcnow()
    conn.execute("DROP TABLE IF EXISTS _op_by_name")
    conn.execute("DROP TABLE IF EXISTS _op_by_number")
    conn.execute("CREATE TEMP TABLE _op_by_name (operator_name TEXT PRIMARY KEY, operator_number TEXT)")
    conn.execute("CREATE TEMP TABLE _op_by_number (operator_number TEXT PRIMARY KEY, operator_name TEXT)")
    by_name: dict[str, tuple[str, str]] = {}
    by_number: dict[str, str] = {}
    for row in conn.execute(
        f"SELECT operator_number, operator_name, org_status FROM {ot}"
    ):
        number = (row["operator_number"] or "").strip()
        name = standardize_operator_name(row["operator_name"])
        if not number or not name:
            continue
        by_number[number] = name
        status = (row["org_status"] or "").upper()
        prev = by_name.get(name)
        if prev is None or (status == "OPEN" and prev[1] != "OPEN"):
            by_name[name] = (number, status)
    conn.executemany(
        "INSERT OR REPLACE INTO _op_by_name(operator_name, operator_number) VALUES (?, ?)",
        [(name, number) for name, (number, _) in by_name.items()],
    )
    conn.executemany(
        "INSERT OR REPLACE INTO _op_by_number(operator_number, operator_name) VALUES (?, ?)",
        list(by_number.items()),
    )
    unassigned = (
        "TRIM(COALESCE({table}.operator, '')) = '' OR "
        "UPPER({table}.operator) IN "
        "('OTC/OCC NOT ASSIGNED', 'UNKNOWN', 'N/A', 'NONE', 'NULL')"
    )
    patched = {}
    for table, extra in (
        (wells_table(state), ""),
        (permits_table(state), "AND {table}.status NOT IN ('migrated')"),
    ):
        blank = unassigned.format(table=table)
        extra_sql = extra.format(table=table)
        named = conn.execute(
            f"""
            UPDATE {table}
            SET operator_number = (
                SELECT operator_number FROM _op_by_name
                WHERE _op_by_name.operator_name = {table}.operator
            ),
            updated_at = ?
            WHERE TRIM(COALESCE(operator_number, '')) = ''
              AND NOT ({blank})
              {extra_sql}
              AND EXISTS (
                SELECT 1 FROM _op_by_name WHERE _op_by_name.operator_name = {table}.operator
              )
            """,
            (now,),
        ).rowcount
        numbered = conn.execute(
            f"""
            UPDATE {table}
            SET operator = (
                SELECT operator_name FROM _op_by_number
                WHERE _op_by_number.operator_number = {table}.operator_number
            ),
            updated_at = ?
            WHERE TRIM(COALESCE(operator_number, '')) != ''
              {extra_sql}
              AND EXISTS (
                SELECT 1 FROM _op_by_number
                WHERE _op_by_number.operator_number = {table}.operator_number
                  AND _op_by_number.operator_name != COALESCE({table}.operator, '')
              )
            """,
            (now,),
        ).rowcount
        patched["wells" if table == wells_table(state) else "permits"] = named + numbered
    conn.execute("DROP TABLE IF EXISTS _op_by_name")
    conn.execute("DROP TABLE IF EXISTS _op_by_number")
    from wellnav.operators import canonicalize_state

    patched["canonical"] = canonicalize_state(conn, state)
    return patched


def upsert_wells(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    table = wells_table(state)
    placeholders = ",".join("?" * len(WELL_FIELDS))
    cols = ",".join(WELL_FIELDS)
    updates = _conflict_set(
        WELL_FIELDS, {"api", "first_seen_at", "migrated_from_permit"}, table
    )
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(api) DO UPDATE SET {updates}"
    )
    payload = []
    for row in rows:
        item = _with_normalized_operator(row)
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
    updates = _conflict_set(
        PERMIT_FIELDS, {"api8", "permit_no", "first_seen_at", "migrated_at"}, table
    )
    sql = (
        f"INSERT INTO {table} ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(api8, permit_no) DO UPDATE SET {updates}, "
        f"status=CASE WHEN {table}.status='migrated' THEN {table}.status ELSE excluded.status END"
    )
    payload = []
    for row in rows:
        item = _with_normalized_operator(row)
        item.setdefault("permit_no", f"GIS-{item.get('api8')}")
        item.setdefault("as_drilled_ready", 0)
        item.setdefault("migrated_at", None)
        payload.append(_values(item, PERMIT_FIELDS))
    conn.executemany(sql, payload)
    return len(rows)


def upsert_ewa_permits(conn: sqlite3.Connection, state: str, rows: list[dict]) -> int:
    """Insert EWA W-1 rows, folding GIS placeholders for the same API into the real permit_no."""
    if not rows:
        return 0
    table = permits_table(state)
    prepared: list[dict] = []
    for row in rows:
        item = _with_normalized_operator(row)
        api8 = (item.get("api8") or "").strip()
        permit_no = (item.get("permit_no") or "").strip() or f"EWA-{api8}"
        item["permit_no"] = permit_no
        item.setdefault("as_drilled_ready", 0)
        item.setdefault("migrated_at", None)
        existing = conn.execute(
            f"""
            SELECT permit_no, wellhead_lat, wellhead_lon, wellhead_crs, first_seen_at,
                   migrated_at, as_drilled_ready, status
            FROM {table}
            WHERE api8 = ? AND status NOT IN ('migrated')
              AND (permit_no = ? OR permit_no = ?)
            ORDER BY CASE WHEN permit_no = ? THEN 0 ELSE 1 END
            LIMIT 1
            """,
            (api8, permit_no, f"GIS-{api8}", permit_no),
        ).fetchone()
        if existing:
            if item.get("wellhead_lat") is None:
                item["wellhead_lat"] = existing["wellhead_lat"]
                item["wellhead_lon"] = existing["wellhead_lon"]
                item["wellhead_crs"] = existing["wellhead_crs"]
            item["first_seen_at"] = existing["first_seen_at"]
            item["migrated_at"] = existing["migrated_at"]
            item["as_drilled_ready"] = existing["as_drilled_ready"]
            if existing["permit_no"] != permit_no:
                clash = conn.execute(
                    f"SELECT 1 FROM {table} WHERE api8 = ? AND permit_no = ?",
                    (api8, permit_no),
                ).fetchone()
                if not clash:
                    conn.execute(
                        f"UPDATE {table} SET permit_no = ? WHERE api8 = ? AND permit_no = ?",
                        (permit_no, api8, existing["permit_no"]),
                    )
        prepared.append(item)
    return upsert_permits(conn, state, prepared)


def update_identities(conn: sqlite3.Connection, state: str, identities: dict[str, dict]) -> int:
    """Patch lease/operator/district on existing wells and permits by api8.

    Never inserts rows and never writes coordinates. Blank incoming fields
    leave the stored value in place.
    """
    if not identities:
        return 0
    from wellnav.ingest.classify import utcnow

    now = utcnow()
    wells = wells_table(state)
    permits = permits_table(state)
    updated = 0
    for api8, ident in identities.items():
        key = (api8 or "").strip()
        if len(key) != 8:
            continue
        lease_name = (ident.get("lease_name") or "").strip()
        lease_no = (ident.get("lease_no") or "").strip()
        district = (ident.get("district") or "").strip()
        operator = normalize_operator_name(ident.get("operator") or "")
        operator_number = (ident.get("operator_number") or "").strip()
        field = (ident.get("field") or "").strip()
        well_no = (ident.get("well_no") or "").strip()
        well_name = (ident.get("well_name") or "").strip()
        if lease_name:
            well_name = f"{lease_name} #{well_no}".strip(" #") if well_no else lease_name
        wcur = conn.execute(
            f"""
            UPDATE {wells}
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
            """,
            (
                well_name, well_no, lease_name, lease_no, district,
                operator, operator_number, field, now, key,
            ),
        )
        pcur = conn.execute(
            f"""
            UPDATE {permits}
            SET well_name=COALESCE(NULLIF(?, ''), well_name),
                well_no=COALESCE(NULLIF(?, ''), well_no),
                lease_name=COALESCE(NULLIF(?, ''), lease_name),
                lease_no=COALESCE(NULLIF(?, ''), lease_no),
                district=COALESCE(NULLIF(?, ''), district),
                operator=COALESCE(NULLIF(?, ''), operator),
                operator_number=COALESCE(NULLIF(?, ''), operator_number),
                updated_at=?
            WHERE api8=? AND status NOT IN ('migrated')
            """,
            (
                well_name, well_no, lease_name, lease_no, district,
                operator, operator_number, now, key,
            ),
        )
        if wcur.rowcount or pcur.rowcount:
            updated += 1
    return updated


def update_identity(
    conn: sqlite3.Connection,
    state: str,
    rows: list[dict],
    county_code: str | None = None,
) -> int:
    """List-shaped wrapper used by tests and enrich; county_code is unused."""
    identities: dict[str, dict] = {}
    for row in rows:
        api8 = ((row.get("api") or row.get("api8") or "").strip())[-8:]
        if len(api8) == 8:
            identities[api8] = row
    return update_identities(conn, state, identities)
