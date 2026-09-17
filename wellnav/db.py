"""SQLite schema: one wells table and one permits table per state."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from wellnav.states import (
    APP_STATES,
    DEFAULT_PERMIT_LIFETIME_DAYS,
    US_STATES,
    operators_table,
    permits_table,
    wells_table,
)

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "wellnav.db"

OPERATOR_COLUMNS = """
    operator_number TEXT PRIMARY KEY,
    operator_name TEXT,
    oil INTEGER NOT NULL DEFAULT 0,
    gas INTEGER NOT NULL DEFAULT 0,
    org_status TEXT,
    org_type TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    wells INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    updated_at TEXT
"""

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
    db_path = Path(path) if path else DB_PATH
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
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            domain TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            stripe_subscription_item_id TEXT,
            billing_status TEXT NOT NULL DEFAULT 'none',
            seat_count INTEGER NOT NULL DEFAULT 0,
            billing_email TEXT,
            current_period_end TEXT
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            phone_verified_at TEXT,
            email_verified_at TEXT,
            org_id INTEGER,
            role TEXT NOT NULL DEFAULT 'member',
            created_at TEXT NOT NULL,
            last_login_at TEXT,
            last_activity_at TEXT,
            session_version INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (org_id) REFERENCES organizations(id)
        );
        CREATE TABLE IF NOT EXISTS saved_wells (
            user_id INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'tx',
            api8 TEXT NOT NULL,
            well_name TEXT,
            well_no TEXT,
            lease_name TEXT,
            county TEXT,
            operator TEXT,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, state, api8),
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
        CREATE TABLE IF NOT EXISTS operators_tx (
            operator_number TEXT PRIMARY KEY,
            operator_name TEXT,
            oil INTEGER NOT NULL DEFAULT 0,
            gas INTEGER NOT NULL DEFAULT 0,
            org_status TEXT,
            org_type TEXT,
            status TEXT NOT NULL DEFAULT 'queued',
            wells INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            updated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_saved_wells_user ON saved_wells(user_id, saved_at DESC);
        CREATE INDEX IF NOT EXISTS idx_user_cache_user ON user_cache(user_id, kind);
        CREATE INDEX IF NOT EXISTS idx_user_cache_expires ON user_cache(expires_at);
        CREATE TABLE IF NOT EXISTS pending_signups (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL,
            password_hash TEXT NOT NULL,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS otp_challenges (
            id TEXT PRIMARY KEY,
            purpose TEXT NOT NULL,
            pending_id TEXT,
            user_id INTEGER,
            phone TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            sent_at TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_pending_signups_username ON pending_signups(username);
        CREATE INDEX IF NOT EXISTS idx_pending_signups_phone ON pending_signups(phone);
        CREATE INDEX IF NOT EXISTS idx_otp_phone_sent ON otp_challenges(phone, sent_at);
        """
    )
    _ensure_column(conn, "users", "phone", "TEXT")
    _ensure_column(conn, "users", "phone_verified_at", "TEXT")
    _ensure_column(conn, "users", "email", "TEXT")
    _ensure_column(conn, "users", "email_verified_at", "TEXT")
    _ensure_column(conn, "users", "org_id", "INTEGER")
    _ensure_column(conn, "users", "role", "TEXT")
    _ensure_column(conn, "users", "last_login_at", "TEXT")
    _ensure_column(conn, "users", "last_activity_at", "TEXT")
    _ensure_column(conn, "users", "session_version", "INTEGER")
    conn.execute("UPDATE users SET session_version = 0 WHERE session_version IS NULL")
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_phone "
        "ON users(phone) WHERE phone IS NOT NULL AND phone != ''"
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email "
        "ON users(email) WHERE email IS NOT NULL AND email != ''"
    )
    conn.execute(
        """
        UPDATE users
        SET email = username
        WHERE (email IS NULL OR email = '') AND instr(username, '@') > 0
        """
    )
    conn.execute("UPDATE users SET role = 'member' WHERE role IS NULL OR role = ''")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_users_org ON users(org_id, role)")
    _ensure_column(conn, "organizations", "stripe_customer_id", "TEXT")
    _ensure_column(conn, "organizations", "stripe_subscription_id", "TEXT")
    _ensure_column(conn, "organizations", "stripe_subscription_item_id", "TEXT")
    _ensure_column(conn, "organizations", "billing_status", "TEXT")
    _ensure_column(conn, "organizations", "seat_count", "INTEGER")
    _ensure_column(conn, "organizations", "billing_email", "TEXT")
    _ensure_column(conn, "organizations", "current_period_end", "TEXT")
    conn.execute(
        "UPDATE organizations SET billing_status = 'none' "
        "WHERE billing_status IS NULL OR billing_status = ''"
    )
    conn.execute("UPDATE organizations SET seat_count = 0 WHERE seat_count IS NULL")
    conn.execute(
        """
        UPDATE organizations
        SET billing_status = 'complimentary', seat_count = 10000
        WHERE lower(domain) = 'simba.services' OR lower(domain) LIKE '%.simba.services'
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_orgs_stripe_customer "
        "ON organizations(stripe_customer_id) "
        "WHERE stripe_customer_id IS NOT NULL AND stripe_customer_id != ''"
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('permit_lifetime_days', ?)",
        (str(DEFAULT_PERMIT_LIFETIME_DAYS),),
    )
    conn.execute(
        "INSERT OR IGNORE INTO meta(key, value) VALUES ('permit_refresh_hours', '168')"
    )
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', '5')")
    conn.execute(
        "UPDATE meta SET value = '168' WHERE key = 'permit_refresh_hours' AND value = '24'"
    )
    conn.execute(
        "UPDATE meta SET value = '5' WHERE key = 'schema_version' AND CAST(value AS INTEGER) < 5"
    )
    _migrate_saved_wells(conn)

    for code in APP_STATES:
        table = operators_table(code)
        conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({OPERATOR_COLUMNS})")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_name ON {table}(operator_name)")

    for code in US_STATES:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {wells_table(code)} ({WELL_COLUMNS})")
        conn.execute(f"CREATE TABLE IF NOT EXISTS {permits_table(code)} ({PERMIT_COLUMNS})")
        w, p = wells_table(code), permits_table(code)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_name ON {w}(well_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_lease ON {w}(lease_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_operator ON {w}(operator)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_operator_number ON {w}(operator_number)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_api8 ON {w}(api8)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{w}_county ON {w}(county)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_name ON {p}(well_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_lease ON {p}(lease_name)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_operator ON {p}(operator)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_operator_number ON {p}(operator_number)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_status ON {p}(status)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{p}_expires ON {p}(expires_at)")

    from wellnav.operators import normalize_stored_operators

    normalize_stored_operators(conn)


def _migrate_saved_wells(conn: sqlite3.Connection) -> None:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(saved_wells)")}
    if "state" in cols:
        return
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.executescript(
        """
        CREATE TABLE saved_wells_v2 (
            user_id INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'tx',
            api8 TEXT NOT NULL,
            well_name TEXT,
            well_no TEXT,
            lease_name TEXT,
            county TEXT,
            operator TEXT,
            saved_at TEXT NOT NULL,
            PRIMARY KEY (user_id, state, api8),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        );
        INSERT INTO saved_wells_v2 (
            user_id, state, api8, well_name, well_no, lease_name, county, operator, saved_at
        )
        SELECT user_id, 'tx', api8, well_name, well_no, lease_name, county, operator, saved_at
        FROM saved_wells;
        DROP TABLE saved_wells;
        ALTER TABLE saved_wells_v2 RENAME TO saved_wells;
        CREATE INDEX IF NOT EXISTS idx_saved_wells_user ON saved_wells(user_id, saved_at DESC);
        """
    )
    conn.execute("PRAGMA foreign_keys=ON")


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


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
