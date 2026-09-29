"""Waste-disposal and injection wells for the SWD map layer.

Texas commercial public SWDs come from RRC Public GIS layer 3 (Commercial
Disposal) plus commercial waste facilities on layer 36. Operator SWD and
injection wells come from layer 4 (Injection/Disposal) and from injection
wells already stored in the well catalog.
"""

from __future__ import annotations

import math
import re
import sqlite3
from pathlib import Path

from wellnav.db import ROOT
from wellnav.states import APP_STATES, STATE_BBOX

DISPOSAL_DB_PATH = ROOT / "data" / "disposal.db"

DEFAULT_LIMIT = 400
# RRC layer 3/4 OBJECTIDs are ~1.4e6. Keep each class in its own id band.
COMMERCIAL_WELL_ID_BASE = 100_000_000
OPERATOR_WELL_ID_BASE = 300_000_000
WELL_ID_SPAN = 100_000_000
RUNTIME_OPERATOR_ID_BASE = 9_000_000_000
_STATE_ID_SLOT = {"tx": 0, "nm": 1, "ok": 2, "la": 3}
EARTH_RADIUS_KM = 6371.0088
KM_PER_MI = 1.609344
# Straight-line distance understates roads. 1.3 is a typical rural road factor.
ROAD_FACTOR = 1.3
# Planning speed for a haul to a disposal facility.
DRIVE_MPH = 50.0

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
    lon REAL NOT NULL,
    swd_class TEXT
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
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_xy ON {table}(lon, lat)")
    _ensure_column(conn, table, "swd_class", "TEXT")
    return table


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    have = {info[1] for info in conn.execute(f"PRAGMA table_info({table})")}
    if column not in have:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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


SWD_COMMERCIAL = "commercial"
SWD_OPERATOR = "operator"
SWD_CLASS_LABELS = {
    SWD_COMMERCIAL: "Commercial public SWD",
    SWD_OPERATOR: "Operator SWD / injection",
}

# Rows copied from neighbor well inventories, not the Texas commercial-facility layer.
_NM_OPERATOR_ID = (2_000_000_000, 2_100_000_000)
_OK_COMMERCIAL_ID = (2_100_000_000, 2_200_000_000)
_LA_OPERATOR_ID = (2_200_000_000, 2_300_000_000)


def api8_key(value: object) -> str:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 10:
        return digits[2:10]
    if len(digits) >= 8:
        return digits[-8:]
    return ""


def classify_swd(
    *,
    swd_class: str = "",
    permit_type: str = "",
    discharge_type: str = "",
    facility: str = "",
    site_id: int | None = None,
) -> str:
    """Commercial public SWD vs an operator's own SWD or injection well."""
    explicit = (swd_class or "").strip().lower()
    if explicit in {SWD_COMMERCIAL, SWD_OPERATOR}:
        return explicit
    blob = " ".join((permit_type or "", discharge_type or "", facility or "")).lower()
    if "commercial" in blob:
        return SWD_COMMERCIAL
    if site_id is not None:
        sid = int(site_id)
        if _OK_COMMERCIAL_ID[0] <= sid < _OK_COMMERCIAL_ID[1]:
            return SWD_COMMERCIAL
        if COMMERCIAL_WELL_ID_BASE <= sid < OPERATOR_WELL_ID_BASE:
            return SWD_COMMERCIAL
        if OPERATOR_WELL_ID_BASE <= sid < OPERATOR_WELL_ID_BASE + WELL_ID_SPAN:
            return SWD_OPERATOR
        if _NM_OPERATOR_ID[0] <= sid < _NM_OPERATOR_ID[1] or _LA_OPERATOR_ID[0] <= sid < _LA_OPERATOR_ID[1]:
            return SWD_OPERATOR
        if sid >= RUNTIME_OPERATOR_ID_BASE:
            return SWD_OPERATOR
    ptype = (permit_type or "").lower()
    if any(token in ptype for token in ("inject", "salt water", "swd")) and "plant" not in ptype:
        return SWD_OPERATOR
    return SWD_COMMERCIAL


def swd_class_label(swd_class: str) -> str:
    return SWD_CLASS_LABELS.get(swd_class, SWD_CLASS_LABELS[SWD_COMMERCIAL])


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


def _row_text(row: sqlite3.Row, key: str) -> str:
    if key not in row.keys():
        return ""
    return row[key] or ""


def _site_row(row: sqlite3.Row) -> dict:
    permit_type = row["permit_type"] or ""
    discharge_type = row["discharge_type"] or ""
    facility = row["facility"] or ""
    site_id = int(row["id"])
    swd_class = classify_swd(
        swd_class=_row_text(row, "swd_class"),
        permit_type=permit_type,
        discharge_type=discharge_type,
        facility=facility,
        site_id=site_id,
    )
    waste = (
        waste_classifications_for(permit_type=permit_type, discharge_type=discharge_type)
        if swd_class == SWD_COMMERCIAL
        else []
    )
    return {
        "id": site_id,
        "operator": row["operator"] or "",
        "facility": facility,
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
        "waste_classifications": waste,
        "swd_class": swd_class,
        "swd_class_label": swd_class_label(swd_class),
    }


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres (Haversine)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def drive_minutes(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Trip duration in minutes from current position to a destination.

    Road miles are the great-circle distance times ``ROAD_FACTOR``, driven at
    ``DRIVE_MPH``. Same coordinates are a zero-minute trip.
    """
    miles = distance_km(float(lat1), float(lon1), float(lat2), float(lon2)) / KM_PER_MI
    minutes = int(round((miles * ROAD_FACTOR) / DRIVE_MPH * 60.0))
    if minutes < 0:
        return 0
    return minutes


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
    path: Path | None = None,
) -> list[dict]:
    kind = (mode or "name").strip().lower()
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


# Operator rows already copied into disposal.db (neighbor inventories and Texas layer 4).
_OPERATOR_MATCH_SQL = """
(
  lower(coalesce(swd_class, '')) = 'operator'
  OR (
    lower(coalesce(swd_class, '')) <> 'commercial'
    AND instr(lower(
      coalesce(permit_type, '') || ' ' || coalesce(facility, '') || ' ' || coalesce(discharge_type, '')
    ), 'commercial') = 0
    AND (
      (id >= 2000000000 AND id < 2100000000)
      OR (id >= 2200000000 AND id < 2300000000)
      OR (id >= 300000000 AND id < 400000000)
      OR instr(lower(coalesce(permit_type, '')), 'inject') > 0
      OR instr(lower(coalesce(permit_type, '')), 'salt water') > 0
      OR instr(lower(coalesce(permit_type, '')), 'swd') > 0
    )
  )
)
"""

_WELL_ROLE_SQL = """
lower(trim(coalesce(symbol, '')) || ' ' || trim(coalesce(well_type, '')))
"""


def _feature_from_site(site: dict) -> dict:
    return {
        "type": "Feature",
        "id": site["id"],
        "geometry": {
            "type": "Point",
            "coordinates": [round(site["lon"], 6), round(site["lat"], 6)],
        },
        "properties": {
            "kind": "disposal",
            "id": site["id"],
            "swd_class": site["swd_class"],
            "swd_class_label": site["swd_class_label"],
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


def _bbox_rows(
    conn: sqlite3.Connection,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    extra_sql: str = "",
    site_id: int | None = None,
    limit: int,
) -> tuple[list[sqlite3.Row], bool]:
    tables = listed_disposal_tables(conn)
    if not tables:
        return [], False
    clauses = ["lon BETWEEN ? AND ?", "lat BETWEEN ? AND ?"]
    params: list = [west, east, south, north]
    if extra_sql:
        clauses.append(f"({extra_sql})")
    if site_id:
        clauses.append("id = ?")
        params.append(int(site_id))
    where = " AND ".join(clauses)
    union = " UNION ALL ".join(f"SELECT * FROM {table} WHERE {where}" for table in tables)
    all_params: list = []
    for _ in tables:
        all_params.extend(params)
    cap = max(1, int(limit))
    rows = conn.execute(
        f"""
        SELECT * FROM ({union})
        ORDER BY facility
        LIMIT ?
        """,
        (*all_params, cap + 1),
    ).fetchall()
    truncated = len(rows) > cap
    return rows[:cap], truncated


def _runtime_well_id(state: str, api8: str) -> int:
    slot = _STATE_ID_SLOT.get(state, 0)
    return RUNTIME_OPERATOR_ID_BASE + slot * 100_000_000 + int(api8)


def _well_facility(well_name: str, well_no: str, lease_name: str, api8: str) -> str:
    name = (well_name or lease_name or "").strip()
    number = (well_no or "").strip()
    if name and number and number not in name:
        name = f"{name} {number}".strip()
    if name:
        return name
    return f"API {api8}" if api8 else "Injection well"


def _operator_wells_in_bbox(
    wells_path: Path | None,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int,
    exclude_api8: set[str],
) -> tuple[list[dict], bool]:
    """Injection / SWD wells from the well catalog that are not already on the layer."""
    if limit <= 0 or wells_path is None or not Path(wells_path).is_file():
        return [], False
    from wellnav.states import APP_STATES, wells_table

    conn = sqlite3.connect(str(wells_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        present = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'wells_%'"
            )
        }
        blob = _WELL_ROLE_SQL
        compact = f"replace(replace({blob}, ' ', ''), '-', '')"
        role_sql = f"""
            instr({compact}, 'plugged') = 0
            AND instr({blob}, 'commercial') = 0
            AND (
              instr({blob}, 'inject') > 0
              OR instr({blob}, 'disposal') > 0
              OR instr({blob}, 'swd') > 0
              OR instr({blob}, 'salt water') > 0
            )
        """
        fetched: list[dict] = []
        truncated = False
        remaining = int(limit)
        for state in APP_STATES:
            table = wells_table(state)
            if table not in present or remaining <= 0:
                continue
            raw_limit = min(2000, max(remaining * 3, remaining + len(exclude_api8)))
            try:
                rows = conn.execute(
                    f"""
                    SELECT api, api8, well_name, well_no, lease_name, operator, county, district,
                           symbol, well_type, wellhead_lat, wellhead_lon
                    FROM {table}
                    WHERE wellhead_lon BETWEEN ? AND ?
                      AND wellhead_lat BETWEEN ? AND ?
                      AND wellhead_lat IS NOT NULL
                      AND wellhead_lon IS NOT NULL
                      AND {role_sql}
                    ORDER BY api
                    LIMIT ?
                    """,
                    (west, east, south, north, raw_limit),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            for row in rows:
                api8 = api8_key(row["api8"] or row["api"])
                if not api8 or api8 in exclude_api8:
                    continue
                exclude_api8.add(api8)
                kind = (row["symbol"] or row["well_type"] or "Injection/disposal").strip()
                site = {
                    "id": _runtime_well_id(state, api8),
                    "operator": row["operator"] or "",
                    "facility": _well_facility(
                        row["well_name"] or "",
                        row["well_no"] or "",
                        row["lease_name"] or "",
                        api8,
                    ),
                    "permit_no": row["api"] or api8,
                    "permit_type": kind,
                    "discharge_type": "",
                    "permit_expiration": "",
                    "district": row["district"] or "",
                    "county": row["county"] or "",
                    "permit_url": "",
                    "lat": float(row["wellhead_lat"]),
                    "lon": float(row["wellhead_lon"]),
                    "permit_type_label": _type_label(kind),
                    "waste_classifications": [],
                    "swd_class": SWD_OPERATOR,
                    "swd_class_label": swd_class_label(SWD_OPERATOR),
                }
                fetched.append(site)
                remaining -= 1
                if remaining <= 0:
                    break
            if remaining <= 0 or len(rows) >= raw_limit:
                truncated = True
            if remaining <= 0:
                break
        return fetched, truncated
    finally:
        conn.close()


def query_geojson(
    bbox: str | None = None,
    *,
    site_id: int | None = None,
    limit: int = DEFAULT_LIMIT,
    path: Path | None = None,
    wells_path: Path | None = None,
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
        cap = max(1, int(limit))
        if site_id:
            rows, truncated = _bbox_rows(
                conn,
                west=west,
                south=south,
                east=east,
                north=north,
                site_id=site_id,
                limit=cap,
            )
            features = [_feature_from_site(_site_row(row)) for row in rows]
        else:
            commercial_rows, commercial_truncated = _bbox_rows(
                conn,
                west=west,
                south=south,
                east=east,
                north=north,
                extra_sql=f"NOT {_OPERATOR_MATCH_SQL}",
                limit=cap,
            )
            operator_rows, operator_truncated = _bbox_rows(
                conn,
                west=west,
                south=south,
                east=east,
                north=north,
                extra_sql=_OPERATOR_MATCH_SQL,
                limit=cap,
            )
            seen_ids: set[int] = set()
            seen_api8: set[str] = set()
            features = []
            for row in (*commercial_rows, *operator_rows):
                site = _site_row(row)
                if site["id"] in seen_ids:
                    continue
                seen_ids.add(site["id"])
                key = api8_key(site["permit_no"])
                if key:
                    seen_api8.add(key)
                features.append(_feature_from_site(site))
            operator_count = sum(
                1 for feat in features if feat["properties"]["swd_class"] == SWD_OPERATOR
            )
            extra, wells_truncated = _operator_wells_in_bbox(
                wells_path,
                west=west,
                south=south,
                east=east,
                north=north,
                limit=max(0, cap - operator_count),
                exclude_api8=seen_api8,
            )
            features.extend(_feature_from_site(site) for site in extra)
            truncated = commercial_truncated or operator_truncated or wells_truncated
        commercial_n = sum(
            1 for feat in features if feat["properties"]["swd_class"] == SWD_COMMERCIAL
        )
        operator_n = sum(
            1 for feat in features if feat["properties"]["swd_class"] == SWD_OPERATOR
        )
        return {
            "type": "FeatureCollection",
            "features": features,
            "meta": {
                "count": len(features),
                "commercial": commercial_n,
                "operator": operator_n,
                "stored": int(stored),
                "loaded_at": loaded["value"] if loaded else None,
                "truncated": truncated,
            },
        }
    finally:
        conn.close()
