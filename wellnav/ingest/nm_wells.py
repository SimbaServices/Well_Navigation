"""New Mexico wells, permits, and operators from the official OCD public GIS.

wells_nm / permits_nm / operators_nm use the same columns as the Texas tables.
Approved APDs are well points on Wells_Public; statuses New, Never Drilled, and
Cancelled are stored as permits_nm. OGRID is the operator_number catalog.
"""

from __future__ import annotations

from pathlib import Path

from wellnav.db import connect, init_schema, session, set_cursor, set_meta
from wellnav.ingest.arcgis import iter_features, query_page
from wellnav.ingest.classify import utcnow
from wellnav.ingest.neighbors import (
    NM_WELLS,
    NM_WELLS_FALLBACK,
    _api10,
    _well_row,
    nm_feature_to_permit,
    nm_feature_to_well,
)
from wellnav.ingest.persist import apply_operator_catalog, upsert_operators, upsert_permits, upsert_wells
from wellnav.operators import normalize_operator_name
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, operators_table, permits_table, wells_table

UNASSIGNED = {"", "unknown", "n/a", "none", "null"}


def _log(message: str) -> None:
    print(message, flush=True)


def choose_nm_wells_url(*, allow_fallback: bool = True, has_existing: bool = False) -> str:
    """Prefer the daily official layer. Do not overwrite a loaded catalog with the 2024 snapshot."""
    try:
        payload = query_page(NM_WELLS, where="1=1", offset=0, page_size=1)
        if payload.get("features"):
            return NM_WELLS
    except Exception as exc:
        _log(f"official OCD Wells_Public unavailable ({exc})")
        if allow_fallback and not has_existing:
            _log("using Nov 2024 snapshot because wells_nm is empty")
            return NM_WELLS_FALLBACK
        raise
    if allow_fallback and not has_existing:
        return NM_WELLS_FALLBACK
    raise RuntimeError("official OCD Wells_Public returned no features")


def operators_from_nm_tables(conn, *, now: str) -> list[dict]:
    seen: dict[str, dict] = {}
    for table in (wells_table("nm"), permits_table("nm")):
        extra = "" if table.startswith("wells_") else " AND status NOT IN ('migrated')"
        type_col = "well_type" if table.startswith("wells_") else "NULL"
        rows = conn.execute(
            f"""
            SELECT operator_number, operator, {type_col} AS well_type, COUNT(*) AS n
            FROM {table}
            WHERE TRIM(COALESCE(operator_number, '')) != ''
              AND TRIM(COALESCE(operator, '')) != ''
              {extra}
            GROUP BY operator_number, operator, {type_col}
            """
        )
        for row in rows:
            number = (row["operator_number"] or "").strip()
            name = normalize_operator_name(row["operator"])
            if not number or name.lower() in UNASSIGNED:
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
            kind = (row["well_type"] or "").lower()
            if "oil" in kind:
                item["oil"] = 1
            if "gas" in kind:
                item["gas"] = 1
    return list(seen.values())


def remove_nm_wells_that_are_permits(conn) -> int:
    """Drop stale wells_nm rows that official OCD still classifies as APDs."""
    cur = conn.execute(
        f"""
        DELETE FROM {wells_table("nm")}
        WHERE api IN (
            SELECT api FROM {permits_table("nm")}
            WHERE status NOT IN ('migrated')
        )
        """
    )
    conn.commit()
    return cur.rowcount


def migrate_nm_permits(conn) -> int:
    """Mark permits_nm migrated once the same API exists in wells_nm."""
    now = utcnow()
    cur = conn.execute(
        f"""
        UPDATE {permits_table("nm")}
        SET status = 'migrated', migrated_at = ?, as_drilled_ready = 1, updated_at = ?
        WHERE status NOT IN ('migrated', 'expired', 'cancelled')
          AND api8 IN (SELECT api8 FROM {wells_table("nm")})
        """,
        (now, now),
    )
    conn.commit()
    return cur.rowcount


def recount_nm_operators(conn) -> int:
    table = operators_table("nm")
    wells = wells_table("nm")
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
              AND UPPER(COALESCE({wells}.well_type, '')) LIKE '%OIL%'
        ) THEN 1 ELSE oil END,
        gas = CASE WHEN EXISTS (
            SELECT 1 FROM {wells}
            WHERE {wells}.operator_number = {table}.operator_number
              AND UPPER(COALESCE({wells}.well_type, '')) LIKE '%GAS%'
        ) THEN 1 ELSE gas END,
        status = 'ok',
        updated_at = ?
        """,
        (now,),
    )
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def load_nm_operators(conn=None) -> dict:
    own = conn is None
    if own:
        conn = connect()
        init_schema(conn)
    try:
        now = utcnow()
        rows = operators_from_nm_tables(conn, now=now)
        upsert_operators(conn, "nm", rows)
        patched = apply_operator_catalog(conn, "nm")
        count = recount_nm_operators(conn)
        set_cursor(conn, "nm_operators_loaded_at", now, now)
        conn.commit()
        _log(f"nm operators catalog {count} patched {patched}")
        return {"operators": count, "patched": patched, "status": "ok"}
    finally:
        if own:
            conn.close()


def _load_fracfocus_nm(conn, existing: set[str]) -> dict:
    try:
        from wellnav.ingest.la_refresh import CACHE_DIR as LA_CACHE
        from wellnav.ingest.la_refresh import FRACFOCUS_ZIP, _col, download_file
    except Exception as exc:
        _log(f"nm fracfocus skipped: {exc}")
        return {"added": 0, "patched": 0}
    try:
        zip_path = download_file(FRACFOCUS_ZIP, LA_CACHE / "fracfocuscsv.zip")
    except Exception as exc:
        _log(f"nm fracfocus download skipped: {exc}")
        return {"added": 0, "patched": 0}

    import csv
    import io
    import zipfile

    added = 0
    patched = 0
    batch: list[dict] = []
    latest: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
            and Path(name).name.lower().startswith(("disclosurelist", "registryupload"))
        ]
        for name in names:
            with archive.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                for row in csv.DictReader(text):
                    api_digits = "".join(ch for ch in _col(row, "APINumber", "api") if ch.isdigit())
                    state = _col(row, "StateName", "StateNumber").lower()
                    if not api_digits.startswith("30") and "mexico" not in state:
                        continue
                    api = _api10(api_digits, "nm")
                    if not api:
                        continue
                    try:
                        lat = float(_col(row, "Latitude", "lat"))
                        lon = float(_col(row, "Longitude", "lon"))
                    except (TypeError, ValueError):
                        continue
                    lon = -abs(lon) if lon > 0 else lon
                    well_name = _col(row, "WellName", "well_name")
                    latest[api] = _well_row(
                        "nm",
                        api,
                        well_name=well_name,
                        lease_name=well_name,
                        operator=_col(row, "OperatorName", "operator"),
                        county=_col(row, "CountyName", "county"),
                        well_type=_col(row, "ProductionType") or "stimulated",
                        symbol="FracFocus",
                        lat=lat,
                        lon=lon,
                        source="nm_fracfocus",
                    )
    for api, well in latest.items():
        if api in existing:
            if well.get("operator"):
                conn.execute(
                    f"""
                    UPDATE {wells_table("nm")}
                    SET operator = CASE WHEN TRIM(COALESCE(operator,''))='' THEN ? ELSE operator END,
                        updated_at = ?
                    WHERE api = ?
                    """,
                    (well["operator"], utcnow(), api),
                )
                patched += 1
            continue
        existing.add(api)
        batch.append(well)
        if len(batch) >= 1000:
            upsert_wells(conn, "nm", batch)
            conn.commit()
            added += len(batch)
            batch = []
    if batch:
        upsert_wells(conn, "nm", batch)
        conn.commit()
        added += len(batch)
    _log(f"nm fracfocus added {added} patched {patched}")
    return {"added": added, "patched": patched}


def load_nm_wells(conn=None, *, delay: float = 0.12, limit: int = 0, skip_fracfocus: bool = False) -> dict:
    own = conn is None
    if own:
        conn = connect()
        init_schema(conn)
    now = utcnow()
    existing = conn.execute(f"SELECT COUNT(*) FROM {wells_table('nm')}").fetchone()[0]
    url = choose_nm_wells_url(allow_fallback=True, has_existing=existing > 0)
    _log(f"nm wells from {url}")
    wells: list[dict] = []
    permits: list[dict] = []
    seen: set[str] = set()
    well_count = 0
    permit_count = 0
    try:
        for feature in iter_features(url, delay=delay, limit=limit, page_size=4000):
            permit = nm_feature_to_permit(
                feature, now=now, lifetime_days=DEFAULT_PERMIT_LIFETIME_DAYS
            )
            if permit:
                if permit["api"] in seen:
                    continue
                seen.add(permit["api"])
                permits.append(permit)
                if len(permits) >= 1000:
                    upsert_permits(conn, "nm", permits)
                    conn.commit()
                    permit_count += len(permits)
                    _log(f"nm permits {permit_count}")
                    permits = []
                continue
            well = nm_feature_to_well(feature)
            if not well or well["api"] in seen:
                continue
            seen.add(well["api"])
            wells.append(well)
            if len(wells) >= 1000:
                upsert_wells(conn, "nm", wells)
                conn.commit()
                well_count += len(wells)
                _log(f"nm wells {well_count}")
                wells = []
        if wells:
            upsert_wells(conn, "nm", wells)
            conn.commit()
            well_count += len(wells)
        if permits:
            upsert_permits(conn, "nm", permits)
            conn.commit()
            permit_count += len(permits)
        frac = {"added": 0, "patched": 0}
        if not skip_fracfocus and limit <= 0:
            frac = _load_fracfocus_nm(conn, seen)
            well_count += int(frac.get("added") or 0)
        removed = remove_nm_wells_that_are_permits(conn)
        migrated = migrate_nm_permits(conn)
        ops = load_nm_operators(conn)
        set_meta(conn, "nm_wells_loaded_at", now)
        set_cursor(conn, "nm_wells_loaded_at", now, now)
        conn.commit()
        stats = {
            "status": "ok",
            "source": url,
            "wells": well_count,
            "permits": permit_count,
            "removed_permit_wells": removed,
            "migrated": migrated,
            "operators": ops.get("operators") or 0,
            "fracfocus": frac,
        }
        _log(f"nm load {stats}")
        return stats
    finally:
        if own:
            conn.close()


def refresh_nm_permits(*, delay: float = 0.12, limit: int = 0, db_path=None) -> dict:
    with session(Path(db_path) if db_path else None) as conn:
        init_schema(conn)
        try:
            url = choose_nm_wells_url(allow_fallback=False)
        except Exception as exc:
            _log(f"nm permits refresh skipped: {exc}")
            return {"status": "error", "error": str(exc), "permits": 0}
        now = utcnow()
        batch: list[dict] = []
        count = 0
        where = "status IN ('New','Never Drilled','Cancelled')"
        for feature in iter_features(url, where=where, delay=delay, limit=limit, page_size=4000):
            row = nm_feature_to_permit(feature, now=now, lifetime_days=DEFAULT_PERMIT_LIFETIME_DAYS)
            if not row:
                continue
            batch.append(row)
            if len(batch) >= 1000:
                upsert_permits(conn, "nm", batch)
                conn.commit()
                count += len(batch)
                batch = []
        if batch:
            upsert_permits(conn, "nm", batch)
            conn.commit()
            count += len(batch)
        removed = remove_nm_wells_that_are_permits(conn)
        migrated = migrate_nm_permits(conn)
        expired = conn.execute(
            f"""
            UPDATE {permits_table("nm")}
            SET status = 'expired', updated_at = ?
            WHERE status IN ('approved', 'validated')
              AND expires_at IS NOT NULL
              AND expires_at < ?
            """,
            (now, now),
        ).rowcount
        set_cursor(conn, "nm_permits_refreshed_at", now, now)
        conn.commit()
    _log(f"nm permits refreshed {count} migrated {migrated}")
    return {
        "status": "ok",
        "permits": count,
        "removed_permit_wells": removed,
        "migrated": migrated,
        "expired": expired,
    }
