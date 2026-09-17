"""Pull RRC Public GIS commercial waste disposal sites into disposal.db."""

from __future__ import annotations

from pathlib import Path

from wellnav.disposal import DISPOSAL_DB_PATH, connect, init_schema
from wellnav.gis import GIS_MAPSERVER, LAYER_COMMERCIAL_WASTE, fetch_layer
from wellnav.ingest.classify import utcnow
from wellnav.db import set_meta

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
    }


def upsert_site(conn, row: dict) -> None:
    conn.execute(
        """
        INSERT INTO disposal_tx(
            id, operator, facility, permit_no, permit_type, discharge_type,
            permit_expiration, district, county, permit_url, lat, lon
        ) VALUES (
            :id, :operator, :facility, :permit_no, :permit_type, :discharge_type,
            :permit_expiration, :district, :county, :permit_url, :lat, :lon
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
            lon=excluded.lon
        """,
        row,
    )


def load_disposal(*, db_path: Path | None = None, delay: float = 0.15) -> dict:
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
        conn.execute(
            f"DELETE FROM disposal_tx WHERE id NOT IN ({','.join('?' * len(seen))})",
            tuple(seen),
        )
    set_meta(conn, "disposal_loaded_at", utcnow())
    set_meta(conn, "disposal_source", LAYER_URL)
    conn.commit()
    total = conn.execute("SELECT COUNT(*) AS n FROM disposal_tx").fetchone()["n"]
    conn.close()
    status = "ok" if pulled.get("complete") else "partial"
    if pulled.get("blocked"):
        status = "blocked"
    return {
        "status": status,
        "fetched": len(features),
        "written": written,
        "skipped": skipped,
        "count": int(total),
        "complete": bool(pulled.get("complete")),
        "blocked": bool(pulled.get("blocked")),
        "error": pulled.get("error"),
        "db": str(path),
    }
