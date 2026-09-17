"""Texas RRC commercial waste disposal sites (Public GIS layer 36)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wellnav.db import ROOT
from wellnav.states import APP_STATES, STATE_BBOX

DISPOSAL_DB_PATH = ROOT / "data" / "disposal.db"

DEFAULT_LIMIT = 400

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


def _site_row(row: sqlite3.Row) -> dict:
    return {
        "id": int(row["id"]),
        "operator": row["operator"] or "",
        "facility": row["facility"] or "",
        "permit_no": row["permit_no"] or "",
        "permit_type": row["permit_type"] or "",
        "discharge_type": row["discharge_type"] or "",
        "permit_expiration": row["permit_expiration"] or "",
        "district": row["district"] or "",
        "county": row["county"] or "",
        "permit_url": row["permit_url"] or "",
        "lat": float(row["lat"]),
        "lon": float(row["lon"]),
        "permit_type_label": _type_label(row["permit_type"] or ""),
    }


def _type_label(raw: str) -> str:
    text = (raw or "").replace("_", " ").strip()
    return text.title() if text else ""


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
    needle = (q or "").strip()
    if len(needle) < 2:
        return []
    like = _contains(needle)
    kind = (mode or "name").strip().lower()
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
                        "permit_type": site["permit_type_label"],
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
