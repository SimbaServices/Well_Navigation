"""Pull RRC Public GIS SWD layers into disposal.db.

Layer 36 is commercial waste facilities. Layer 3 is commercial public disposal
wells. Layer 4 is operator injection and disposal wells, minus the commercial
APIs already taken from layer 3.
"""

from __future__ import annotations

from pathlib import Path

from wellnav.db import DB_PATH, set_meta
from wellnav.disposal import (
    COMMERCIAL_WELL_ID_BASE,
    DISPOSAL_DB_PATH,
    OPERATOR_WELL_ID_BASE,
    SWD_COMMERCIAL,
    SWD_OPERATOR,
    WELL_ID_SPAN,
    api8_key,
    connect,
    init_schema,
)
from wellnav.gis import (
    GIS_MAPSERVER,
    LAYER_COMMERCIAL_DISPOSAL,
    LAYER_COMMERCIAL_WASTE,
    LAYER_INJECTION_DISPOSAL,
    fetch_layer,
)
from wellnav.ingest.classify import utcnow

LAYER_URL = f"{GIS_MAPSERVER}/{LAYER_COMMERCIAL_WASTE}"


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coord(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0:
        return None
    return number


def feature_to_row(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    object_id = attrs.get("OBJECTID")
    if object_id in (None, ""):
        return None
    lat = _coord(attrs.get("LATITUDE")) or _coord(geom.get("y"))
    lon = _coord(attrs.get("LONGITUDE")) or _coord(geom.get("x"))
    if lat is None or lon is None:
        return None
    if not (25.5 <= lat <= 36.6 and -107.0 <= lon <= -93.0):
        return None
    return {
        "id": int(object_id),
        "operator": _text(attrs.get("OPERATOR_NAME")),
        "facility": _text(attrs.get("LEASE_OR_FACILITY_NAME")),
        "permit_no": _text(attrs.get("PERMIT_NO")),
        "permit_type": _text(attrs.get("PERMIT_TYPE")),
        "discharge_type": _text(attrs.get("DISCHARGE_TYPE")),
        "permit_expiration": _text(attrs.get("PERMIT_EXPIRATION")),
        "district": _text(attrs.get("RRC_DISTRICT_OFFICE")),
        "county": _text(attrs.get("COUNTY")),
        "permit_url": _text(attrs.get("PERMIT_URL")).replace("http://rrc.texas.gov", "https://www.rrc.texas.gov"),
        "lat": lat,
        "lon": lon,
        "swd_class": SWD_COMMERCIAL,
    }


def _well_facility(row: dict | None, api8: str) -> str:
    if not row:
        return f"API {api8}" if api8 else "Injection well"
    name = _text(row.get("well_name") or row.get("lease_name"))
    number = _text(row.get("well_no"))
    if name and number and number not in name:
        name = f"{name} {number}".strip()
    return name or (f"API {api8}" if api8 else "Injection well")


def uic_feature_to_row(
    feature: dict,
    *,
    swd_class: str,
    id_base: int,
    well: dict | None = None,
) -> dict | None:
    """Map an RRC commercial-disposal or injection/disposal well to a site row."""
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    object_id = attrs.get("OBJECTID")
    if object_id in (None, ""):
        return None
    try:
        oid = int(object_id)
    except (TypeError, ValueError):
        return None
    if oid < 0 or oid >= WELL_ID_SPAN:
        return None
    api8 = api8_key(attrs.get("API"))
    lat = _coord(geom.get("y"))
    lon = _coord(geom.get("x"))
    if lat is None or lon is None or not api8:
        return None
    if not (25.5 <= lat <= 36.6 and -107.0 <= lon <= -93.0):
        return None
    kind = SWD_COMMERCIAL if swd_class == SWD_COMMERCIAL else SWD_OPERATOR
    permit_type = "Commercial disposal" if kind == SWD_COMMERCIAL else "Injection/disposal"
    if well and _text(well.get("symbol") or well.get("well_type")) and kind == SWD_OPERATOR:
        permit_type = _text(well.get("symbol") or well.get("well_type"))
    return {
        "id": id_base + oid,
        "operator": _text(well.get("operator") if well else ""),
        "facility": _well_facility(well, api8),
        "permit_no": _text(well.get("api") if well else "") or api8,
        "permit_type": permit_type,
        "discharge_type": "",
        "permit_expiration": "",
        "district": _text(well.get("district") if well else ""),
        "county": _text(well.get("county") if well else ""),
        "permit_url": "",
        "lat": lat,
        "lon": lon,
        "swd_class": kind,
    }


def upsert_site(conn, row: dict) -> None:
    payload = dict(row)
    payload.setdefault("swd_class", SWD_COMMERCIAL)
    conn.execute(
        """
        INSERT INTO disposal_tx(
            id, operator, facility, permit_no, permit_type, discharge_type,
            permit_expiration, district, county, permit_url, lat, lon, swd_class
        ) VALUES (
            :id, :operator, :facility, :permit_no, :permit_type, :discharge_type,
            :permit_expiration, :district, :county, :permit_url, :lat, :lon, :swd_class
        )
        ON CONFLICT(id) DO UPDATE SET
            operator=excluded.operator,
            facility=excluded.facility,
            permit_no=excluded.permit_no,
            permit_type=excluded.permit_type,
            discharge_type=excluded.discharge_type,
            permit_expiration=excluded.permit_expiration,
            district=excluded.district,
            county=excluded.county,
            permit_url=excluded.permit_url,
            lat=excluded.lat,
            lon=excluded.lon,
            swd_class=excluded.swd_class
        """,
        payload,
    )


def _delete_id_range(conn, low: int, high: int, seen: set[int]) -> None:
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS disposal_seen(id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM disposal_seen")
    if seen:
        conn.executemany("INSERT INTO disposal_seen(id) VALUES (?)", ((item,) for item in seen))
    conn.execute(
        """
        DELETE FROM disposal_tx
        WHERE id >= ? AND id < ?
          AND id NOT IN (SELECT id FROM disposal_seen)
        """,
        (low, high),
    )


def _wells_by_api8(api8s: set[str], wells_path: Path | None) -> dict[str, dict]:
    path = wells_path or DB_PATH
    if not api8s or not path.is_file():
        return {}
    import sqlite3

    conn = sqlite3.connect(str(path), timeout=60)
    conn.row_factory = sqlite3.Row
    found: dict[str, dict] = {}
    try:
        items = [key for key in api8s if key]
        for offset in range(0, len(items), 400):
            chunk = items[offset : offset + 400]
            marks = ",".join("?" * len(chunk))
            try:
                rows = conn.execute(
                    f"""
                    SELECT api, api8, well_name, well_no, lease_name, operator, county,
                           district, symbol, well_type
                    FROM wells_tx
                    WHERE api8 IN ({marks})
                    """,
                    chunk,
                ).fetchall()
            except sqlite3.OperationalError:
                return found
            for row in rows:
                key = api8_key(row["api8"] or row["api"])
                if key:
                    found[key] = {name: row[name] for name in row.keys()}
        return found
    finally:
        conn.close()


def _load_uic_class(
    conn,
    layer_id: int,
    *,
    swd_class: str,
    id_base: int,
    delay: float,
    wells: dict[str, dict],
    wells_path: Path | None = None,
    skip_api8: set[str] | None = None,
) -> dict:
    pulled = fetch_layer(layer_id, "1=1", delay=delay)
    features = pulled.get("features") or []
    pending: list[tuple[dict, str]] = []
    needed: set[str] = set()
    skipped = 0
    for feature in features:
        api8 = api8_key((feature.get("attributes") or {}).get("API"))
        if not api8 or (skip_api8 and api8 in skip_api8):
            if api8 and skip_api8 and api8 in skip_api8:
                skipped += 1
            elif not api8:
                skipped += 1
            continue
        if api8 not in wells:
            needed.add(api8)
        pending.append((feature, api8))
    if needed:
        wells.update(_wells_by_api8(needed, wells_path))
    seen: set[int] = set()
    written = 0
    seen_api8: set[str] = set()
    for feature, api8 in pending:
        if api8 in seen_api8:
            continue
        row = uic_feature_to_row(
            feature,
            swd_class=swd_class,
            id_base=id_base,
            well=wells.get(api8),
        )
        if not row:
            skipped += 1
            continue
        upsert_site(conn, row)
        seen.add(row["id"])
        seen_api8.add(api8)
        written += 1
    return {
        "features": features,
        "written": written,
        "skipped": skipped,
        "seen": seen,
        "apis": seen_api8,
        "complete": bool(pulled.get("complete")),
        "blocked": bool(pulled.get("blocked")),
        "error": pulled.get("error"),
    }


def load_disposal(
    *,
    db_path: Path | None = None,
    delay: float = 0.15,
    wells_path: Path | None = None,
) -> dict:
    path = db_path or DISPOSAL_DB_PATH
    pulled = fetch_layer(
        LAYER_COMMERCIAL_WASTE,
        "1=1",
        delay=delay,
    )
    features = pulled.get("features") or []
    conn = connect(path)
    init_schema(conn)
    written = 0
    skipped = 0
    seen: set[int] = set()
    for feature in features:
        row = feature_to_row(feature)
        if not row:
            skipped += 1
            continue
        upsert_site(conn, row)
        seen.add(row["id"])
        written += 1
    if pulled.get("complete") and seen:
        _delete_id_range(conn, 0, COMMERCIAL_WELL_ID_BASE, seen)
    wells: dict[str, dict] = {}
    commercial = _load_uic_class(
        conn,
        LAYER_COMMERCIAL_DISPOSAL,
        swd_class=SWD_COMMERCIAL,
        id_base=COMMERCIAL_WELL_ID_BASE,
        delay=delay,
        wells=wells,
        wells_path=wells_path,
    )
    operator = _load_uic_class(
        conn,
        LAYER_INJECTION_DISPOSAL,
        swd_class=SWD_OPERATOR,
        id_base=OPERATOR_WELL_ID_BASE,
        delay=delay,
        wells=wells,
        wells_path=wells_path,
        skip_api8=commercial["apis"],
    )
    if commercial["apis"]:
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS commercial_api(api8 TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM commercial_api")
        conn.executemany(
            "INSERT INTO commercial_api(api8) VALUES (?)",
            ((item,) for item in commercial["apis"]),
        )
        conn.execute(
            """
            DELETE FROM disposal_tx
            WHERE id >= ? AND id < ?
              AND substr(replace(replace(coalesce(permit_no, ''), '-', ''), ' ', ''), -8)
                  IN (SELECT api8 FROM commercial_api)
            """,
            (OPERATOR_WELL_ID_BASE, OPERATOR_WELL_ID_BASE + WELL_ID_SPAN),
        )
    if commercial["complete"] and commercial["seen"]:
        _delete_id_range(
            conn,
            COMMERCIAL_WELL_ID_BASE,
            OPERATOR_WELL_ID_BASE,
            commercial["seen"],
        )
    if operator["complete"] and operator["seen"]:
        _delete_id_range(
            conn,
            OPERATOR_WELL_ID_BASE,
            OPERATOR_WELL_ID_BASE + WELL_ID_SPAN,
            operator["seen"],
        )
    set_meta(conn, "disposal_loaded_at", utcnow())
    set_meta(conn, "disposal_source", LAYER_URL)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS n FROM disposal_tx").fetchone()["n"]
    conn.close()
    complete = bool(pulled.get("complete") and commercial["complete"] and operator["complete"])
    blocked = bool(pulled.get("blocked") or commercial["blocked"] or operator["blocked"])
    status = "ok" if complete else "partial"
    if blocked:
        status = "blocked"
    error = pulled.get("error") or commercial.get("error") or operator.get("error")
    return {
        "status": status,
        "fetched": len(features),
        "written": written,
        "skipped": skipped,
        "commercial_wells": commercial["written"],
        "operator_wells": operator["written"],
        "count": int(total),
        "complete": complete,
        "blocked": blocked,
        "error": error,
        "db": str(path),
    }
