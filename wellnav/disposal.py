"""Texas RRC commercial waste disposal sites (Public GIS layer 36)."""

from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

from wellnav.db import ROOT
from wellnav.states import APP_STATES, STATE_BBOX

DISPOSAL_DB_PATH = ROOT / "data" / "disposal.db"

DEFAULT_LIMIT = 400
EARTH_RADIUS_KM = 6371.0088
KM_PER_MI = 1.609344

# Case-insensitive tokens that mark radium / NORM / radioactive acceptance.
_RADIUM_KEYWORDS = (
    "radium",
    "norm",
    "tenorm",
    "radioactive",
    "radioactiv",
    "naturally occurring radioactive",
)

_DISPOSAL_COLUMNS = """
    id INTEGER PRIMARY KEY,
    operator TEXT,
    facility TEXT,
    permit_no TEXT,
    permit_type TEXT,
    discharge_type TEXT,
    permit_expiration TEXT,
    district TEXT,
    county TEXT,
    permit_url TEXT,
    lat REAL NOT NULL,
    lon REAL NOT NULL
"""


def disposal_table(state: str) -> str:
    code = (state or "tx").strip().lower()
    if code not in APP_STATES:
        raise ValueError(f"unsupported disposal state: {state}")
    return f"disposal_{code}"


def ensure_disposal_table(conn: sqlite3.Connection, state: str) -> str:
    table = disposal_table(state)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({_DISPOSAL_COLUMNS})")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_facility ON {table}(facility)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_operator ON {table}(operator)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_permit ON {table}(permit_no)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_county ON {table}(county)")
    return table


def listed_disposal_tables(conn: sqlite3.Connection) -> list[str]:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'disposal_%'"
        )
    }
    return [disposal_table(code) for code in APP_STATES if disposal_table(code) in names]


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else DISPOSAL_DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    for code in APP_STATES:
        ensure_disposal_table(conn, code)


def stats(path: Path | None = None) -> dict:
    conn = connect(path)
    init_schema(conn)
    try:
        total = 0
        for table in listed_disposal_tables(conn):
            total += conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        loaded = conn.execute(
            "SELECT value FROM meta WHERE key='disposal_loaded_at'"
        ).fetchone()
        if not loaded:
            loaded = conn.execute(
                """
                SELECT value FROM meta
                WHERE key LIKE 'disposal_%_loaded_at'
                ORDER BY value DESC LIMIT 1
                """
            ).fetchone()
        return {
            "count": int(total),
            "loaded_at": loaded["value"] if loaded else None,
        }
    finally:
        conn.close()


def parse_bbox(raw: str | None) -> tuple[float, float, float, float]:
    if not raw:
        west = min(STATE_BBOX[code]["lon_min"] for code in APP_STATES)
        south = min(STATE_BBOX[code]["lat_min"] for code in APP_STATES)
        east = max(STATE_BBOX[code]["lon_max"] for code in APP_STATES)
        north = max(STATE_BBOX[code]["lat_max"] for code in APP_STATES)
        return west, south, east, north
    parts = [float(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = parts
    if east < west:
        west, east = east, west
    if north < south:
        south, north = north, south
    return west, south, east, north


def _contains(q: str) -> str:
    return f"%{(q or '').strip()}%"


def _type_label(raw: str) -> str:
    text = (raw or "").replace("_", " ").strip()
    return text.title() if text else ""


def _meaningful_label(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    if lowered in {"n/a", "na", "none", "null", "unknown", "-", "--"}:
        return ""
    return _type_label(text)


def waste_classifications_for(*, permit_type: str = "", discharge_type: str = "") -> list[str]:
    """Human-readable accepted waste labels from permit + discharge fields (deduped)."""
    labels: list[str] = []
    seen: set[str] = set()
    for raw in (permit_type, discharge_type):
        label = _meaningful_label(raw)
        if not label:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _site_row(row: sqlite3.Row) -> dict:
    permit_type = row["permit_type"] or ""
    discharge_type = row["discharge_type"] or ""
    return {
        "id": int(row["id"]),
        "operator": row["operator"] or "",
        "facility": row["facility"] or "",
        "permit_no": row["permit_no"] or "",
        "permit_type": permit_type,
        "discharge_type": discharge_type,
        "permit_expiration": row["permit_expiration"] or "",
        "district": row["district"] or "",
        "county": row["county"] or "",
        "permit_url": row["permit_url"] or "",
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "permit_type_label": _type_label(permit_type),
        "waste_classifications": waste_classifications_for(
            permit_type=permit_type,
            discharge_type=discharge_type,
        ),
    }


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (Haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


_NORM_WORD_RE = re.compile(r"\bnorm\b", re.IGNORECASE)


def _suggests_radium(site: dict) -> bool:
    blob = " ".join(
        str(site.get(key) or "")
        for key in ("facility", "permit_type", "discharge_type", "permit_type_label")
    ).casefold()
    if not blob.strip():
        return False
    for keyword in _RADIUM_KEYWORDS:
        token = keyword.casefold()
        if token == "norm":
            # Avoid matching substrings like "normal"; require a word boundary.
            if _NORM_WORD_RE.search(blob) or "tenorm" in blob:
                return True
            continue
        if token in blob:
            return True
    return False


def _iter_all_sites(conn: sqlite3.Connection) -> list[dict]:
    tables = listed_disposal_tables(conn)
    if not tables:
        return []
    union = " UNION ALL ".join(f"SELECT * FROM {table}" for table in tables)
    rows = conn.execute(
        f"SELECT * FROM ({union}) ORDER BY facility, operator, id"
    ).fetchall()
    return [_site_row(row) for row in rows]


def nearest_sites(
    lat: float,
    lon: float,
    *,
    limit: int = 20,
    radium_only: bool = False,
    max_km: float | None = None,
    path: Path | None = None,
) -> list[dict]:
    """Return disposal sites sorted by Haversine distance from (lat, lon).

    When ``radium_only`` is True, only sites whose facility / permit / discharge
    fields suggest radium, NORM, TENORM, or similar radioactive waste acceptance
    are returned. Zero matches yields an empty list (no silent fallback).
    """
    origin_lat = float(lat)
    origin_lon = float(lon)
    cap = max(1, int(limit))
    conn = connect(path)
    init_schema(conn)
    try:
        sites = _iter_all_sites(conn)
    finally:
        conn.close()

    if radium_only:
        sites = [site for site in sites if _suggests_radium(site)]
        if not sites:
            return []

    ranked: list[dict] = []
    for site in sites:
        km = distance_km(origin_lat, origin_lon, site["lat"], site["lon"])
        if max_km is not None and km > float(max_km):
            continue
        item = dict(site)
        item["distance_km"] = round(km, 3)
        item["distance_mi"] = round(km / KM_PER_MI, 3)
        ranked.append(item)

    ranked.sort(key=lambda row: (row["distance_km"], row.get("facility") or "", row["id"]))
    return ranked[:cap]


def get_site(site_id: int, path: Path | None = None) -> dict | None:
    conn = connect(path)
    init_schema(conn)
    try:
        row = None
        for table in listed_disposal_tables(conn):
            row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (int(site_id),)).fetchone()
            if row:
                break
        return _site_row(row) if row else None
    finally:
        conn.close()


def search_sites(
    q: str,
    *,
    mode: str = "name",
    limit: int = 80,
    lat: float | None = None,
    lon: float | None = None,
    max_km: float | None = None,
    path: Path | None = None,
) -> list[dict]:
    kind = (mode or "name").strip().lower()
    if kind in {"near", "radium_near"}:
        if lat is None or lon is None:
            raise ValueError("lat and lon are required for nearest disposal search")
        return nearest_sites(
            float(lat),
            float(lon),
            limit=limit,
            radium_only=(kind == "radium_near"),
            max_km=max_km,
            path=path,
        )

    needle = (q or "").strip()
    if len(needle) < 2:
        return []
    like = _contains(needle)
    if kind == "operator":
        where = "operator LIKE ?"
        params: tuple = (like,)
    elif kind == "permit":
        where = "permit_no LIKE ?"
        params = (like,)
    elif kind == "county":
        where = "county LIKE ?"
        params = (like,)
    else:
        where = "facility LIKE ? OR operator LIKE ? OR permit_no LIKE ? OR county LIKE ?"
        params = (like, like, like, like)
    conn = connect(path)
    init_schema(conn)
    try:
        tables = listed_disposal_tables(conn)
        if not tables:
            return []
        union = " UNION ALL ".join(f"SELECT * FROM {table} WHERE {where}" for table in tables)
        all_params: list = []
        for _ in tables:
            all_params.extend(params)
        rows = conn.execute(
            f"""
            SELECT * FROM ({union})
            ORDER BY facility, operator, id
            LIMIT ?
            """,
            (*all_params, int(limit)),
        ).fetchall()
        return [_site_row(row) for row in rows]
    finally:
        conn.close()


def query_geojson(
    bbox: str | None = None,
    *,
    site_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    path: Path | None = None,
) -> dict:
    west, south, east, north = parse_bbox(bbox)
    conn = connect(path)
    init_schema(conn)
    try:
        stored = 0
        for table in listed_disposal_tables(conn):
            stored += conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        loaded = conn.execute(
            "SELECT value FROM meta WHERE key='disposal_loaded_at'"
        ).fetchone()
        if not loaded:
            loaded = conn.execute(
                """
                SELECT value FROM meta
                WHERE key LIKE 'disposal_%_loaded_at'
                ORDER BY value DESC LIMIT 1
                """
            ).fetchone()
        clauses = ["lon BETWEEN ? AND ?", "lat BETWEEN ? AND ?"]
        params: list = [west, east, south, north]
        if site_id:
            clauses.append("id = ?")
            params.append(int(site_id))
        where = " AND ".join(clauses)
        tables = listed_disposal_tables(conn)
        union = " UNION ALL ".join(f"SELECT * FROM {table} WHERE {where}" for table in tables)
        all_params: list = []
        for _ in tables:
            all_params.extend(params)
        rows = conn.execute(
            f"""
            SELECT * FROM ({union})
            ORDER BY facility
            LIMIT ?
            """,
            (*all_params, int(limit)),
        ).fetchall()
        features = []
        for row in rows:
            site = _site_row(row)
            features.append(
                {
                    "type": "Feature",
                    "id": site["id"],
                    "geometry": {
                        "type": "Point",
                        "coordinates": [round(site["lon"], 6), round(site["lat"], 6)],
                    },
                    "properties": {
                        "kind": "disposal",
                        "id": site["id"],
                        "operator": site["operator"],
                        "facility": site["facility"],
                        "permit_no": site["permit_no"],
                        "permit_type": site["permit_type"],
                        "permit_type_label": site["permit_type_label"],
                        "discharge_type": site["discharge_type"],
                        "waste_classifications": site["waste_classifications"],
                        "county": site["county"],
                        "district": site["district"],
                        "permit_url": site["permit_url"],
                        "lat": site["lat"],
                        "lon": site["lon"],
                    },
                }
            )
        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "count": len(features),
                "stored": int(stored),
                "loaded_at": loaded["value"] if loaded else None,
                "truncated": len(features) >= int(limit),
            },
        }
    finally:
        conn.close()
