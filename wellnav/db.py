"""SQLite schema: one wells table and one permits table per state."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from wellnav.states import (
    DEFAULT_PERMIT_LIFETIME_DAYS,
    US_STATES,
    permits_table,
    wells_table,
)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "wellnav.db"

WELL_COLUMNS = """
    api TEXT PRIMARY KEY,
    api8 TEXT NOT NULL,
    well_name TEXT,
    well_no TEXT,
    lease_name TEXT,
    lease_no TEXT,
    county TEXT,
    county_code TEXT,
    district TEXT,
    operator TEXT,
    operator_number TEXT,
    field TEXT,
    well_type TEXT,
    symbol TEXT,
    symnum INTEGER,
    profile TEXT,
    wellhead_lat REAL,
    wellhead_lon REAL,
    wellhead_crs TEXT,
    toe_lat REAL,
    toe_lon REAL,
    toe_crs TEXT,
    location_kind TEXT,
    location_source TEXT,
    gis_lat83 REAL,
    gis_long83 REAL,
    gis_lat27 REAL,
    gis_long27 REAL,
    source TEXT,
    migrated_from_permit INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
"""

PERMIT_COLUMNS = """
    api TEXT NOT NULL,
    api8 TEXT NOT NULL,
    permit_no TEXT,
    status TEXT NOT NULL,
    well_name TEXT,
    well_no TEXT,
    lease_name TEXT,
    lease_no TEXT,
    county TEXT,
    county_code TEXT,
    district TEXT,
    operator TEXT,
    operator_number TEXT,
    profile TEXT,
    symbol TEXT,
    symnum INTEGER,
    wellhead_lat REAL,
    wellhead_lon REAL,
    wellhead_crs TEXT,
    approved_at TEXT,
    submitted_at TEXT,
    expires_at TEXT,
    lifetime_days INTEGER NOT NULL DEFAULT 730,
    as_drilled_ready INTEGER NOT NULL DEFAULT 0,
    migrated_at TEXT,
    source TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (api8, permit_no)
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db_path = path or DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=60, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=60000")
    return conn


@contextmanager
def session(path: Path | None = None):
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sync_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            state TEXT NOT NULL,
            status TEXT NOT NULL,
            workers INTEGER,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            message TEXT
        );
        CREATE TABLE IF NOT EXISTS sync_partitions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            partition_key TEXT NOT NULL,
            partition_name TEXT,
            status TEXT NOT NULL,
            wells INTEGER NOT NULL DEFAULT 0,
            permits INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            started_at TEXT,
            finished_at TEXT,
            FOREIGN KEY (job_id) REFERENCES sync_jobs(id)
        );
        CREATE TABLE IF NOT EXISTS ingest_cursors (
            name TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS saved_wells (
            user_id INTEGER NOT NULL,
            api8 TEXT NOT NULL,
            well_name TEXT,
            well_no TEXT,
            lease_name TEXT,
            county TEXT,
            operator TEXT,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, api8),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS user_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            cache_key TEXT NOT NULL,
            payload TEXT NOT NULL,
            hits INTEGER NOT NULL DEFAULT 0,
            bytes INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_hit_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            UNIQUE (user_id, kind, cache_key),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_saved_wells_user ON saved_wells(user_id, saved_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_cache_user ON user_cache(user_id, kind);
        CREATE INDEX IF NOT EXISTS idx_user_cache_expires ON user_cache(expires_at);
        """
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('permit_lifetime_days', ?)",
        (str(DEFAULT_PERMIT_LIFETIME_DAYS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('permit_refresh_hours', '24')"
    )
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '2')")
    conn.execute(
        "UPDATE meta SET value = '2' WHERE key = 'schema_version' AND CAST(value AS INTEGER) < 2"
    )

    for code in US_STATES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {wells_table(code)} ({WELL_COLUMNS})")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {permits_table(code)} ({PERMIT_COLUMNS})")
        w, p = wells_table(code), permits_table(code)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_name ON {w}(well_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_lease ON {w}(lease_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_operator ON {w}(operator)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_api8 ON {w}(api8)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_county ON {w}(county)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_name ON {p}(well_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_lease ON {p}(lease_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_operator ON {p}(operator)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_status ON {p}(status)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_expires ON {p}(expires_at)")


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def get_cursor(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT value FROM ingest_cursors WHERE name = ?", (name,)).fetchone()
    return row["value"] if row else None


def set_cursor(conn: sqlite3.Connection, name: str, value: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO ingest_cursors(name, value, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (name, value, now),
    )
