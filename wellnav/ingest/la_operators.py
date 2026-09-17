"""Louisiana operator catalog: same columns and role as operators_tx."""

from __future__ import annotations

from wellnav.db import connect, init_schema, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.ingest.la_permits import expire_la_permits, migrate_la_permits, split_la_permit_rows
from wellnav.ingest.persist import apply_operator_catalog, upsert_operators
from wellnav.operators import normalize_operator_name
from wellnav.states import operators_table, permits_table, wells_table

UNASSIGNED = {"", "OTC/OCC NOT ASSIGNED", "UNKNOWN", "N/A", "NONE", "NULL"}


def _log(message: str) -> None:
    print(message, flush=True)


def operators_from_activity(conn, state: str = "la", *, now: str | None = None) -> list[dict]:
    """Build operators_{state} rows from wells and live permits (same fields as Texas)."""
    stamp = now or utcnow()
    seen: dict[str, dict] = {}
    for table in (wells_table(state), permits_table(state)):
        extra = "" if table.startswith("wells_") else " AND status NOT IN ('migrated')"
        rows = conn.execute(
            f"""
            SELECT operator_number, operator, COUNT(*) AS n
            FROM {table}
            WHERE TRIM(COALESCE(operator_number, '')) != ''
              AND TRIM(COALESCE(operator, '')) != ''
              AND UPPER(operator) NOT IN
                ('OTC/OCC NOT ASSIGNED', 'UNKNOWN', 'N/A', 'NONE', 'NULL')
              {extra}
            GROUP BY operator_number, operator
            """
        )
        for row in rows:
            number = (row["operator_number"] or "").strip()
            name = normalize_operator_name(row["operator"])
            if not number or name in UNASSIGNED:
                continue
            item = seen.setdefault(
                number,
                {
                    "operator_number": number,
                    "operator_name": name,
                    "oil": 0,
                    "gas": 0,
                    "org_status": "OPEN",
                    "org_type": "oil/gas",
                    "status": "ok",
                    "wells": 0,
                    "error": None,
                    "updated_at": stamp,
                },
            )
            if name:
                item["operator_name"] = name
            item["wells"] += int(row["n"] or 0)
    return list(seen.values())


def recount_operators(conn, state: str = "la") -> int:
    table = operators_table(state)
    wells = wells_table(state)
    now = utcnow()
    conn.execute(
        f"""
        UPDATE {table}
        SET wells = (
            SELECT COUNT(*) FROM {wells}
            WHERE {wells}.operator_number = {table}.operator_number
        ),
        oil = CASE WHEN EXISTS (
            SELECT 1 FROM {wells}
            WHERE {wells}.operator_number = {table}.operator_number
              AND (
                UPPER(COALESCE({wells}.well_type, '')) LIKE '%OIL%'
                OR UPPER(COALESCE({wells}.symbol, '')) LIKE '%OIL%'
              )
        ) THEN 1 ELSE oil END,
        gas = CASE WHEN EXISTS (
            SELECT 1 FROM {wells}
            WHERE {wells}.operator_number = {table}.operator_number
              AND (
                UPPER(COALESCE({wells}.well_type, '')) LIKE '%GAS%'
                OR UPPER(COALESCE({wells}.symbol, '')) LIKE '%GAS%'
                OR UPPER(COALESCE({wells}.well_type, '')) LIKE '%OG%'
              )
        ) THEN 1 ELSE gas END,
        status = 'ok',
        updated_at = ?
        """,
        (now,),
    )
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fetch_la_operators(*, refresh: bool = True, conn=None) -> dict:
    """Upsert operators_la from wells_la / permits_la. Louisiana has no public P-5 list."""
    close = False
    if conn is None:
        conn = connect()
        init_schema(conn)
        close = True
    try:
        existing = conn.execute(f"SELECT COUNT(*) FROM {operators_table('la')}").fetchone()[0]
        if existing and not refresh:
            _log(f"la operators already stored {existing}, skipping fetch")
            return {"operators": existing, "status": "ok", "skipped": True}
        now = utcnow()
        rows = operators_from_activity(conn, "la", now=now)
        upsert_operators(conn, "la", rows)
        set_cursor(conn, "la_operators_loaded_at", now, now)
        conn.commit()
        _log(f"la operators stored {len(rows)}")
        return {"operators": len(rows), "status": "ok"}
    finally:
        if close:
            conn.close()


def sync_louisiana_catalog(conn) -> dict:
    """Align LA permits/operators with the Texas wells/permits/operators layout."""
    split = split_la_permit_rows(conn)
    migrated = migrate_la_permits(conn)
    expired = expire_la_permits(conn)
    now = utcnow()
    rows = operators_from_activity(conn, "la", now=now)
    upsert_operators(conn, "la", rows)
    patched = apply_operator_catalog(conn, "la")
    count = recount_operators(conn, "la")
    set_cursor(conn, "la_operators_loaded_at", now, now)
    set_cursor(conn, "la_catalog_synced_at", now, now)
    conn.commit()
    stats = {
        "permits_moved": split["moved"],
        "permits": split["permits"],
        "migrated": migrated,
        "expired": expired,
        "operators": count,
        "patched": patched,
        "status": "ok",
    }
    _log(f"la catalog {stats}")
    return stats


def load_la_operators(*, refresh: bool = True, conn=None) -> dict:
    """Split permit-only wells, rebuild operators_la, and patch identities."""
    close = False
    if conn is None:
        conn = connect()
        init_schema(conn)
        close = True
    try:
        if not refresh:
            return fetch_la_operators(refresh=False, conn=conn)
        return sync_louisiana_catalog(conn)
    finally:
        if close:
            conn.close()
