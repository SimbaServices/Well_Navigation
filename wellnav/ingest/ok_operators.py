"""Oklahoma operator catalog: same columns and role as operators_tx."""

from __future__ import annotations

from pathlib import Path

import requests

from wellnav.db import ROOT, connect, init_schema, session, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.ingest.persist import apply_operator_catalog, upsert_operators
from wellnav.ingest.xlsx import iter_xlsx_dicts
from wellnav.operators import normalize_operator_name
from wellnav.states import operators_table, permits_table, wells_table

OCC_OPERATOR_XLSX = (
    "https://oklahoma.gov/content/dam/ok/en/occ/documents/og/"
    "ogdatafiles/operator-list.xlsx"
)
CACHE_DIR = ROOT / "data" / "ok_refresh"
USER_AGENT = "Mozilla/5.0 (compatible; WellNavigation/1.0)"
UNASSIGNED = {"", "OTC/OCC NOT ASSIGNED", "UNKNOWN", "N/A", "NONE", "NULL"}


def _log(message: str) -> None:
    print(message, flush=True)


def download_occ_file(url: str, dest: Path, *, timeout: int = 180) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 1000:
        return dest
    resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    dest.write_bytes(resp.content)
    return dest


def operator_from_occ_row(row: dict, *, now: str) -> dict | None:
    number = str(row.get("Operator_No") or row.get("operator_number") or "").strip()
    name = normalize_operator_name(row.get("Company_Name") or row.get("operator_name") or "")
    if not number or name in UNASSIGNED:
        return None
    status = str(row.get("Operator_Status") or row.get("org_status") or "").strip().upper()
    return {
        "operator_number": number,
        "operator_name": name,
        "oil": 0,
        "gas": 0,
        "org_status": status,
        "org_type": "oil/gas",
        "status": "ok",
        "wells": 0,
        "error": None,
        "updated_at": now,
    }


def parse_operator_list(path: Path, *, now: str | None = None) -> list[dict]:
    stamp = now or utcnow()
    seen: dict[str, dict] = {}
    for row in iter_xlsx_dicts(path):
        item = operator_from_occ_row(row, now=stamp)
        if item:
            seen[item["operator_number"]] = item
    return list(seen.values())


def _operators_from_tables(conn, *, now: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for table in (wells_table("ok"), permits_table("ok")):
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
                    "org_status": "",
                    "org_type": "oil/gas",
                    "status": "ok",
                    "wells": 0,
                    "error": None,
                    "updated_at": now,
                },
            )
            if name:
                item["operator_name"] = name
            item["wells"] += int(row["n"] or 0)
    return list(seen.values())


def recount_ok_operators(conn) -> int:
    table = operators_table("ok")
    now = utcnow()
    conn.execute(
        f"""
        UPDATE {table}
        SET wells = (
            SELECT COUNT(*) FROM {wells_table("ok")}
            WHERE {wells_table("ok")}.operator_number = {table}.operator_number
        ),
        oil = CASE WHEN EXISTS (
            SELECT 1 FROM {wells_table("ok")}
            WHERE {wells_table("ok")}.operator_number = {table}.operator_number
              AND UPPER(COALESCE({wells_table("ok")}.well_type, '')) LIKE '%OIL%'
        ) THEN 1 ELSE oil END,
        gas = CASE WHEN EXISTS (
            SELECT 1 FROM {wells_table("ok")}
            WHERE {wells_table("ok")}.operator_number = {table}.operator_number
              AND (
                UPPER(COALESCE({wells_table("ok")}.well_type, '')) LIKE '%GAS%'
                OR UPPER(COALESCE({wells_table("ok")}.well_type, '')) LIKE '%OG%'
              )
        ) THEN 1 ELSE gas END,
        status = 'ok',
        updated_at = ?
        """,
        (now,),
    )
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def fetch_ok_operators(*, refresh: bool = False, path: Path | None = None) -> dict:
    """Download the daily OCC operator list and upsert operators_ok."""
    with session() as conn:
        init_schema(conn)
        existing = conn.execute(f"SELECT COUNT(*) FROM {operators_table('ok')}").fetchone()[0]
    if existing and not refresh and path is None:
        _log(f"ok operators already stored {existing}, skipping fetch")
        return {"operators": existing, "status": "ok", "skipped": True}

    now = utcnow()
    source = path or download_occ_file(OCC_OPERATOR_XLSX, CACHE_DIR / "operator-list.xlsx")
    rows = parse_operator_list(source, now=now)
    with session() as conn:
        init_schema(conn)
        upsert_operators(conn, "ok", rows)
        set_cursor(conn, "ok_operators_loaded_at", now, now)
    _log(f"ok operators stored {len(rows)}")
    return {"operators": len(rows), "status": "ok"}


def load_ok_operators(*, refresh: bool = False, path: Path | None = None) -> dict:
    """Fetch the OCC catalog, add operators seen on wells, and patch identities."""
    loaded = fetch_ok_operators(refresh=refresh, path=path)
    now = utcnow()
    with session() as conn:
        init_schema(conn)
        extra = _operators_from_tables(conn, now=now)
        if extra:
            upsert_operators(conn, "ok", extra)
        patched = apply_operator_catalog(conn, "ok")
        count = recount_ok_operators(conn)
        set_cursor(conn, "ok_operators_enriched_at", now, now)
    _log(f"ok operators patched {patched} catalog {count}")
    return {
        **loaded,
        "catalog": count,
        "patched": patched,
        "status": "ok",
    }


def load_operators_ok_into(conn, *, path: Path | None = None) -> dict:
    """Same work against an already-open connection (used by well ingest)."""
    now = utcnow()
    source = path or download_occ_file(OCC_OPERATOR_XLSX, CACHE_DIR / "operator-list.xlsx")
    rows = parse_operator_list(source, now=now)
    upsert_operators(conn, "ok", rows)
    extra = _operators_from_tables(conn, now=now)
    if extra:
        upsert_operators(conn, "ok", extra)
    patched = apply_operator_catalog(conn, "ok")
    count = recount_ok_operators(conn)
    set_cursor(conn, "ok_operators_loaded_at", now, now)
    set_cursor(conn, "ok_operators_enriched_at", now, now)
    conn.commit()
    _log(f"ok operators catalog {count} patched {patched}")
    return {"operators": count, "patched": patched, "status": "ok"}
