"""Texas RRC pipeline locations stored locally as WGS84 polylines."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shapely import wkb
from shapely.geometry import mapping

from wellnav.coords import TEXAS_BBOX
from wellnav.db import ROOT
from wellnav.states import APP_STATES, TX_COUNTY_NAME

PIPE_DB_PATH = ROOT / "data" / "pipelines.db"

STATUS_LABELS = {
    "I": "In service",
    "B": "Abandoned",
    "A": "Abandoned",
    "R": "Removed",
}
SYSTYPE_LABELS = {
    "T": "Transmission",
    "G": "Gathering",
    "L": "Liquid",
    "O": "Other",
    "P": "Production",
    "Q": "Other",
}
QUALITY_LABELS = {
    "eia": "EIA/HIFLD transmission",
    "bsee": "BSEE Gulf of America OCS",
    "blm_row": "BLM right-of-way",
}
PIPELINE_DISCLAIMER = (
    "TX: RRC T-4. NM: EIA/HIFLD + BLM ROW. OK: EIA/HIFLD. "
    "LA: EIA/HIFLD + BSEE OCS. These are not state T-4 surveys. "
    "Not for excavation — call 811."
)
COMMODITY_GROUP = {
    "NGG": "gas",
    "NGT": "gas",
    "NFG": "gas",
    "NFT": "gas",
    "NGZ": "gas",
    "OGG": "gas",
    "OGT": "gas",
    "CRL": "crude",
    "CRO": "crude",
    "CRA": "crude",
    "CFL": "crude",
    "HVL": "hvl",
    "AA": "hvl",
    "PRD": "product",
    "CO2": "co2",
}

DEFAULT_LIMIT = 12000

_PIPE_COLUMNS = """
    tpms_id INTEGER PRIMARY KEY,
    county_code TEXT,
    county_name TEXT,
    operator TEXT,
    p5_num TEXT,
    system_name TEXT,
    subsystem TEXT,
    t4_permit TEXT,
    diameter REAL,
    commodity TEXT,
    commodity_desc TEXT,
    status TEXT,
    systype TEXT,
    interstate TEXT,
    quality TEXT,
    pipeline_id TEXT,
    geom BLOB NOT NULL,
    minx REAL NOT NULL,
    miny REAL NOT NULL,
    maxx REAL NOT NULL,
    maxy REAL NOT NULL
"""


def pipeline_table(state: str) -> str:
    code = (state or "tx").strip().lower()
    if code not in APP_STATES:
        raise ValueError(f"unsupported pipeline state: {state}")
    return f"pipelines_{code}"


def ensure_pipeline_table(conn: sqlite3.Connection, state: str) -> str:
    table = pipeline_table(state)
    conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({_PIPE_COLUMNS})")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_bbox ON {table}(minx, maxx, miny, maxy)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_status ON {table}(status)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_operator ON {table}(operator)")
    conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_system ON {table}(system_name)")
    return table


def listed_pipeline_tables(conn: sqlite3.Connection) -> list[str]:
    names = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'pipelines_%'"
        )
    }
    return [pipeline_table(code) for code in APP_STATES if pipeline_table(code) in names]


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else PIPE_DB_PATH
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
        ensure_pipeline_table(conn, code)
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS pipelines_tx_rtree USING rtree(
                id, minx, maxx, miny, maxy
            )
            """
        )
    except sqlite3.OperationalError:
        pass


def has_rtree(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pipelines_tx_rtree'"
    ).fetchone()
    return row is not None


def stats(conn: sqlite3.Connection | None = None, path: Path | None = None) -> dict:
    own = conn is None
    if own:
        conn = connect(path)
        init_schema(conn)
    try:
        total = 0
        for table in listed_pipeline_tables(conn):
            total += conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
        loaded = conn.execute(
            "SELECT value FROM meta WHERE key='pipelines_loaded_at'"
        ).fetchone()
        if not loaded:
            loaded = conn.execute(
                """
                SELECT value FROM meta
                WHERE key LIKE 'pipelines_%_loaded_at'
                ORDER BY value DESC LIMIT 1
                """
            ).fetchone()
        share = conn.execute(
            "SELECT value FROM meta WHERE key='pipelines_share_url'"
        ).fetchone()
        return {
            "count": int(total),
            "loaded_at": loaded["value"] if loaded else None,
            "share_url": share["value"] if share else None,
        }
    finally:
        if own:
            conn.close()


def commodity_group(code: str | None) -> str:
    return COMMODITY_GROUP.get((code or "").strip().upper(), "other")


def status_label(code: str | None) -> str:
    key = (code or "").strip().upper()
    return STATUS_LABELS.get(key, key or "Unknown")


def systype_label(code: str | None) -> str:
    key = (code or "").strip().upper()
    return SYSTYPE_LABELS.get(key, key or "Unknown")


def quality_label(code: str | None) -> str:
    key = (code or "").strip()
    return QUALITY_LABELS.get(key, "RRC T-4")


def parse_bbox(raw: str | None) -> tuple[float, float, float, float]:
    if not raw:
        return (
            TEXAS_BBOX["lon_min"],
            TEXAS_BBOX["lat_min"],
            TEXAS_BBOX["lon_max"],
            TEXAS_BBOX["lat_max"],
        )
    parts = [float(part.strip()) for part in raw.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = parts
    if east < west:
        west, east = east, west
    if north < south:
        south, north = north, south
    return west, south, east, north


def _zoom_clause(zoom: int, abandoned: bool, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    clauses = []
    if not abandoned:
        clauses.append(f"{prefix}status = 'I'")
    if zoom <= 7:
        clauses.append(
            f"({prefix}interstate = 'Y' OR IFNULL({prefix}diameter, 0) >= 16 OR {prefix}systype = 'T')"
        )
    elif zoom <= 9:
        clauses.append(
            f"({prefix}interstate = 'Y' OR IFNULL({prefix}diameter, 0) >= 8 OR {prefix}systype IN ('T', 'G'))"
        )
    elif zoom <= 11:
        clauses.append(
            f"({prefix}interstate = 'Y' OR IFNULL({prefix}diameter, 0) >= 6 OR {prefix}systype IN ('T', 'G'))"
        )
    return (" AND " + " AND ".join(clauses)) if clauses else ""


def _simplify_tolerance(zoom: int) -> float:
    if zoom <= 7:
        return 0.004
    if zoom <= 9:
        return 0.0015
    if zoom <= 11:
        return 0.00035
    return 0.0


def _coord_digits(zoom: int) -> int:
    return 5 if zoom <= 12 else 6


def query_geojson(
    bbox: str | None = None,
    zoom: int = 10,
    abandoned: bool = False,
    limit: int = DEFAULT_LIMIT,
    path: Path | None = None,
    p5: str | None = None,
    system: str | None = None,
    operator: str | None = None,
) -> dict:
    west, south, east, north = parse_bbox(bbox)
    conn = connect(path)
    init_schema(conn)
    focused = bool((p5 or "").strip() or (system or "").strip() or (operator or "").strip())
    rows: list[sqlite3.Row] = []
    per_table = max(int(limit) + 1, 1)
    for table in listed_pipeline_tables(conn):
        use_rtree = table == "pipelines_tx" and has_rtree(conn)
        alias = "p" if use_rtree else ""
        extra = _zoom_clause(int(zoom), abandoned, alias=alias) if not focused else (
            "" if abandoned else f" AND {'p.' if alias else ''}status = 'I'"
        )
        focus_sql, focus_params = _focus_clause(p5=p5, system=system, operator=operator, alias=alias)
        params = [east, west, north, south, *focus_params, per_table]
        if use_rtree:
            sql = f"""
                SELECT p.*
                FROM {table} p
                JOIN pipelines_tx_rtree r ON r.id = p.tpms_id
                WHERE r.minx <= ? AND r.maxx >= ?
                  AND r.miny <= ? AND r.maxy >= ?
                  {extra}
                  {focus_sql}
                ORDER BY CASE WHEN p.interstate = 'Y' THEN 0 ELSE 1 END, IFNULL(p.diameter, 0) DESC
                LIMIT ?
            """
        else:
            sql = f"""
                SELECT *
                FROM {table}
                WHERE minx <= ? AND maxx >= ?
                  AND miny <= ? AND maxy >= ?
                  {extra}
                  {focus_sql}
                ORDER BY CASE WHEN interstate = 'Y' THEN 0 ELSE 1 END, IFNULL(diameter, 0) DESC
                LIMIT ?
            """
        rows.extend(conn.execute(sql, params).fetchall())
    conn.close()
    rows.sort(
        key=lambda row: (
            0 if (row["interstate"] or "") == "Y" else 1,
            -(row["diameter"] or 0),
        )
    )
    truncated = len(rows) > limit
    rows = rows[:limit]
    tolerance = 0.0 if focused else _simplify_tolerance(int(zoom))
    digits = _coord_digits(int(zoom))
    features = []
    for row in rows:
        feature = _feature_from_row(row, tolerance=tolerance, digits=digits)
        if feature:
            features.append(feature)
    info = stats(path=path)
    return {
        "type": "FeatureCollection",
        "features": features,
        "meta": {
            "count": len(features),
            "stored": info["count"],
            "loaded_at": info["loaded_at"],
            "truncated": truncated,
            "zoom": int(zoom),
            "abandoned": bool(abandoned),
            "p5": (p5 or "").strip(),
            "system": (system or "").strip(),
            "operator": (operator or "").strip(),
        },
    }


def _focus_clause(
    *,
    p5: str | None = None,
    system: str | None = None,
    operator: str | None = None,
    alias: str = "",
) -> tuple[str, list]:
    prefix = f"{alias}." if alias else ""
    clauses: list[str] = []
    params: list = []
    p5_val = (p5 or "").strip()
    system_val = (system or "").strip()
    operator_val = (operator or "").strip()
    if p5_val:
        variants = p5_variants(p5_val)
        placeholders = ",".join("?" * len(variants))
        clauses.append(f"{prefix}p5_num IN ({placeholders})")
        params.extend(variants)
    if system_val:
        clauses.append(f"{prefix}system_name = ?")
        params.append(system_val)
    if operator_val and not p5_val:
        clauses.append(f"{prefix}operator LIKE ?")
        params.append(f"%{operator_val}%")
    if not clauses:
        return "", []
    return " AND " + " AND ".join(clauses), params


def _feature_from_row(row: sqlite3.Row, *, tolerance: float, digits: int) -> dict | None:
    geom = wkb.loads(bytes(row["geom"]))
    if geom.is_empty:
        return None
    if tolerance:
        geom = geom.simplify(tolerance, preserve_topology=False)
        if geom.is_empty:
            return None
    geo = mapping(geom)
    _round_coords(geo, digits)
    return {
        "type": "Feature",
        "id": int(row["tpms_id"]),
        "properties": _row_props(row),
        "geometry": geo,
    }


def _row_props(row: sqlite3.Row) -> dict:
    return {
        "operator": row["operator"] or "",
        "p5": (row["p5_num"] or "").strip(),
        "system": row["system_name"] or "",
        "subsystem": row["subsystem"] or "",
        "t4": row["t4_permit"] or "",
        "pipeline_id": row["pipeline_id"] or "",
        "diameter": row["diameter"],
        "commodity": row["commodity"] or "",
        "commodity_desc": row["commodity_desc"] or "",
        "commodity_group": commodity_group(row["commodity"]),
        "status": row["status"] or "",
        "status_label": status_label(row["status"]),
        "systype": row["systype"] or "",
        "systype_label": systype_label(row["systype"]),
        "interstate": row["interstate"] or "",
        "quality": row["quality"] or "",
        "quality_label": quality_label(row["quality"]),
        "county": row["county_code"] or "",
        "county_name": row["county_name"] or TX_COUNTY_NAME.get(row["county_code"] or "", ""),
    }


def p5_variants(raw: str | None) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    out: list[str] = []
    seen: set[str] = set()
    candidates = [text]
    digits = "".join(ch for ch in text if ch.isdigit())
    if digits:
        candidates.append(digits)
        candidates.append(digits.zfill(6))
        candidates.append(digits.lstrip("0") or "0")
    for item in candidates:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _contains(q: str) -> str:
    return f"%{(q or '').strip()}%"


def search_operators(q: str, limit: int = 40, path: Path | None = None) -> list[dict]:
    needle = (q or "").strip()
    if len(needle) < 2:
        return []
    like = _contains(needle)
    conn = connect(path)
    init_schema(conn)
    tables = listed_pipeline_tables(conn)
    union = " UNION ALL ".join(
        f"SELECT p5_num, operator, system_name, t4_permit, minx, miny, maxx, maxy FROM {table} "
        f"WHERE operator LIKE ? OR p5_num LIKE ?"
        for table in tables
    )
    params: list = []
    for _ in tables:
        params.extend([like, like])
    rows = conn.execute(
        f"""
        SELECT
            IFNULL(NULLIF(TRIM(MAX(p5_num)), ''), '') AS p5,
            MAX(operator) AS operator,
            COUNT(*) AS segments,
            COUNT(DISTINCT NULLIF(TRIM(system_name), '')) AS systems,
            COUNT(DISTINCT NULLIF(TRIM(t4_permit), '')) AS t4_permits,
            MIN(minx) AS minx,
            MIN(miny) AS miny,
            MAX(maxx) AS maxx,
            MAX(maxy) AS maxy
        FROM ({union})
        GROUP BY IFNULL(NULLIF(TRIM(p5_num), ''), operator)
        ORDER BY segments DESC, operator
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    conn.close()
    return [_group_row(row, kind="operator") for row in rows]


def search_systems(q: str, limit: int = 40, path: Path | None = None) -> list[dict]:
    needle = (q or "").strip()
    if len(needle) < 2:
        return []
    like = _contains(needle)
    conn = connect(path)
    init_schema(conn)
    tables = listed_pipeline_tables(conn)
    union = " UNION ALL ".join(
        f"SELECT system_name, operator, p5_num, t4_permit, subsystem, pipeline_id, "
        f"minx, miny, maxx, maxy FROM {table} "
        f"WHERE IFNULL(TRIM(system_name), '') != '' "
        f"AND (system_name LIKE ? OR subsystem LIKE ? OR pipeline_id LIKE ?)"
        for table in tables
    )
    params: list = []
    for _ in tables:
        params.extend([like, like, like])
    rows = conn.execute(
        f"""
        SELECT
            system_name AS system,
            MAX(operator) AS operator,
            IFNULL(NULLIF(TRIM(p5_num), ''), '') AS p5,
            COUNT(*) AS segments,
            COUNT(DISTINCT NULLIF(TRIM(t4_permit), '')) AS t4_permits,
            MIN(minx) AS minx,
            MIN(miny) AS miny,
            MAX(maxx) AS maxx,
            MAX(maxy) AS maxy
        FROM ({union})
        GROUP BY system_name, IFNULL(NULLIF(TRIM(p5_num), ''), operator)
        ORDER BY segments DESC, system
        LIMIT ?
        """,
        (*params, int(limit)),
    ).fetchall()
    conn.close()
    return [_group_row(row, kind="system") for row in rows]


def list_systems(
    *,
    p5: str | None = None,
    system: str | None = None,
    operator: str | None = None,
    limit: int = 80,
    path: Path | None = None,
) -> list[dict]:
    conn = connect(path)
    init_schema(conn)
    extra, params = _focus_clause(p5=p5, system=system, operator=operator)
    tables = listed_pipeline_tables(conn)
    union = " UNION ALL ".join(
        f"SELECT system_name, operator, p5_num, t4_permit, minx, miny, maxx, maxy "
        f"FROM {table} WHERE IFNULL(TRIM(system_name), '') != '' {extra}"
        for table in tables
    )
    all_params: list = []
    for _ in tables:
        all_params.extend(params)
    rows = conn.execute(
        f"""
        SELECT
            system_name AS system,
            MAX(operator) AS operator,
            IFNULL(NULLIF(TRIM(p5_num), ''), '') AS p5,
            COUNT(*) AS segments,
            COUNT(DISTINCT NULLIF(TRIM(t4_permit), '')) AS t4_permits,
            MIN(minx) AS minx,
            MIN(miny) AS miny,
            MAX(maxx) AS maxx,
            MAX(maxy) AS maxy
        FROM ({union})
        GROUP BY system_name, IFNULL(NULLIF(TRIM(p5_num), ''), operator)
        ORDER BY segments DESC, system
        LIMIT ?
        """,
        [*all_params, int(limit)],
    ).fetchall()
    conn.close()
    return [_group_row(row, kind="system") for row in rows]


def _group_row(row: sqlite3.Row, *, kind: str) -> dict:
    return {
        "kind": kind,
        "p5": (row["p5"] or "").strip(),
        "operator": row["operator"] or "",
        "system": (row["system"] if "system" in row.keys() else "") or "",
        "segments": int(row["segments"] or 0),
        "systems": int(row["systems"] or 0) if "systems" in row.keys() else 0,
        "t4_permits": int(row["t4_permits"] or 0),
        "minx": row["minx"],
        "miny": row["miny"],
        "maxx": row["maxx"],
        "maxy": row["maxy"],
    }


def lookup_identity(p5: str | None, identity_db: Path | None = None) -> dict | None:
    variants = p5_variants(p5)
    if not variants:
        return None
    from wellnav.db import DB_PATH, connect as well_connect

    conn = well_connect(identity_db or DB_PATH)
    try:
        placeholders = ",".join("?" * len(variants))
        row = conn.execute(
            f"""
            SELECT operator_number, operator_name, org_status, org_type,
                   status, oil, gas, wells
            FROM operators_tx
            WHERE operator_number IN ({placeholders})
            LIMIT 1
            """,
            variants,
        ).fetchone()
    except sqlite3.OperationalError:
        return None
    finally:
        conn.close()
    if not row:
        return None
    return {
        "p5": row["operator_number"] or "",
        "name": row["operator_name"] or "",
        "org_status": row["org_status"] or "",
        "org_type": row["org_type"] or "",
        "status": row["status"] or "",
        "oil": int(row["oil"] or 0),
        "gas": int(row["gas"] or 0),
        "wells": int(row["wells"] or 0),
    }


def owner_summary(
    *,
    p5: str | None = None,
    system: str | None = None,
    operator: str | None = None,
    path: Path | None = None,
    identity_db: Path | None = None,
) -> dict | None:
    conn = connect(path)
    init_schema(conn)
    extra, params = _focus_clause(p5=p5, system=system, operator=operator)
    if not extra:
        conn.close()
        return None
    tables = listed_pipeline_tables(conn)
    union = " UNION ALL ".join(
        f"SELECT operator, p5_num, system_name, t4_permit, county_name, minx, miny, maxx, maxy "
        f"FROM {table} WHERE 1=1 {extra}"
        for table in tables
    )
    all_params: list = []
    for _ in tables:
        all_params.extend(params)
    row = conn.execute(
        f"""
        SELECT
            MAX(operator) AS operator,
            IFNULL(NULLIF(TRIM(MAX(p5_num)), ''), '') AS p5,
            COUNT(*) AS segments,
            COUNT(DISTINCT NULLIF(TRIM(system_name), '')) AS systems,
            COUNT(DISTINCT NULLIF(TRIM(t4_permit), '')) AS t4_permits,
            COUNT(DISTINCT county_name) AS counties,
            MIN(minx) AS minx,
            MIN(miny) AS miny,
            MAX(maxx) AS maxx,
            MAX(maxy) AS maxy
        FROM ({union})
        """,
        all_params,
    ).fetchone()
    conn.close()
    if not row or not int(row["segments"] or 0):
        return None
    p5_val = (p5 or row["p5"] or "").strip()
    return {
        "operator": row["operator"] or operator or "",
        "p5": p5_val,
        "system": (system or "").strip(),
        "segments": int(row["segments"] or 0),
        "systems": int(row["systems"] or 0),
        "t4_permits": int(row["t4_permits"] or 0),
        "counties": int(row["counties"] or 0),
        "minx": row["minx"],
        "miny": row["miny"],
        "maxx": row["maxx"],
        "maxy": row["maxy"],
        "identity": lookup_identity(p5_val, identity_db=identity_db),
        "disclaimer": PIPELINE_DISCLAIMER,
    }


def segment_detail(
    tpms_id: int,
    path: Path | None = None,
    identity_db: Path | None = None,
) -> dict | None:
    conn = connect(path)
    init_schema(conn)
    row = None
    for table in listed_pipeline_tables(conn):
        row = conn.execute(
            f"SELECT * FROM {table} WHERE tpms_id = ?", (int(tpms_id),)
        ).fetchone()
        if row:
            break
    conn.close()
    if not row:
        return None
    props = _row_props(row)
    summary = owner_summary(
        p5=props["p5"] or None,
        operator=None if props["p5"] else props["operator"] or None,
        path=path,
        identity_db=identity_db,
    )
    return {
        "id": int(row["tpms_id"]),
        **props,
        "minx": row["minx"],
        "miny": row["miny"],
        "maxx": row["maxx"],
        "maxy": row["maxy"],
        "identity": (summary or {}).get("identity"),
        "summary": summary,
        "disclaimer": (summary or {}).get("disclaimer")
        or PIPELINE_DISCLAIMER,
    }


def _round_coords(obj: dict, digits: int) -> None:
    geom_type = obj.get("type")
    coords = obj.get("coordinates")
    if coords is None:
        return
    if geom_type == "LineString":
        obj["coordinates"] = [[round(x, digits), round(y, digits)] for x, y in coords]
    elif geom_type == "MultiLineString":
        obj["coordinates"] = [
            [[round(x, digits), round(y, digits)] for x, y in line] for line in coords
        ]


def geometry_wgs84_from_nad27(geo_interface: dict) -> dict | None:
    """Reproject a GeoJSON-like geometry from NAD27 lon/lat to WGS84."""
    from wellnav.coords import transform_line_nad27

    geom_type = geo_interface.get("type")
    coords = geo_interface.get("coordinates")
    if not coords:
        return None
    if geom_type == "LineString":
        converted = transform_line_nad27(coords)
        if len(converted) < 2:
            return None
        return {"type": "LineString", "coordinates": converted}
    if geom_type == "MultiLineString":
        lines = [transform_line_nad27(line) for line in coords]
        lines = [line for line in lines if len(line) >= 2]
        if not lines:
            return None
        if len(lines) == 1:
            return {"type": "LineString", "coordinates": lines[0]}
        return {"type": "MultiLineString", "coordinates": lines}
    return None
