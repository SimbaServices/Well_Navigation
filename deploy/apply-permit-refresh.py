"""Replace permitted locations in wellnav.db from data/permit_refresh/*.jsonl.

Drilled wells stay. permits_{state} is replaced. wells_{state} rows whose
symbol is Permitted, or whose symnum is 2 or 9, are removed. Each state's
permits_refreshed_at cursor is set to the time of this load.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

STATES = ("tx", "nm", "ok", "la")
FIELDS = (
    "api",
    "api8",
    "permit_no",
    "status",
    "well_name",
    "well_no",
    "lease_name",
    "lease_no",
    "county",
    "county_code",
    "district",
    "operator",
    "operator_number",
    "profile",
    "symbol",
    "symnum",
    "wellhead_lat",
    "wellhead_lon",
    "wellhead_crs",
    "approved_at",
    "submitted_at",
    "expires_at",
    "lifetime_days",
    "as_drilled_ready",
    "migrated_at",
    "source",
    "first_seen_at",
    "last_seen_at",
    "updated_at",
)


def _kept_wells_sql(table: str) -> str:
    return (
        f"SELECT COUNT(*) FROM {table} WHERE NOT ("
        f"COALESCE(symbol, '') = 'Permitted' OR IFNULL(symnum, -1) IN (2, 9))"
    )


def _load(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"{path} is empty")
    keys = {(row.get("api8"), row.get("permit_no")) for row in rows}
    if len(keys) != len(rows):
        raise SystemExit(f"{path} has duplicate api8/permit_no keys")
    return rows


def apply(db_path: Path, folder: Path) -> None:
    conn = sqlite3.connect(str(db_path), timeout=180)
    conn.execute("PRAGMA busy_timeout=180000")
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    try:
        for state in STATES:
            rows = _load(folder / f"{state}.jsonl")
            permits = f"permits_{state}"
            wells = f"wells_{state}"
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({permits})")}
            missing = [name for name in FIELDS if name not in columns]
            if missing:
                raise SystemExit(f"{permits} is missing columns {missing}")
            before_kept = conn.execute(_kept_wells_sql(wells)).fetchone()[0]
            before_permits = conn.execute(f"SELECT COUNT(*) FROM {permits}").fetchone()[0]
            conn.execute("BEGIN")
            deleted_permits = conn.execute(f"DELETE FROM {permits}").rowcount
            deleted_wells = conn.execute(
                f"DELETE FROM {wells} WHERE symbol = 'Permitted' OR symnum IN (2, 9)"
            ).rowcount
            conn.executemany(
                f"INSERT INTO {permits} ({','.join(FIELDS)}) "
                f"VALUES ({','.join('?' * len(FIELDS))})",
                [[row.get(name) for name in FIELDS] for row in rows],
            )
            conn.execute(
                """
                INSERT INTO ingest_cursors(name, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (f"{state}_permits_refreshed_at", now, now),
            )
            after_kept = conn.execute(_kept_wells_sql(wells)).fetchone()[0]
            after_permits = conn.execute(f"SELECT COUNT(*) FROM {permits}").fetchone()[0]
            if after_kept != before_kept or after_permits != len(rows):
                conn.rollback()
                raise SystemExit(
                    f"{state} aborted kept {before_kept}->{after_kept} "
                    f"permits {after_permits} expected {len(rows)}"
                )
            conn.commit()
            print(
                f"{state} permits {before_permits} -> {after_permits} "
                f"removed_permit_rows {deleted_permits} "
                f"removed_permitted_wells {deleted_wells} "
                f"drilled_wells {after_kept}",
                flush=True,
            )
    finally:
        conn.close()


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        raise SystemExit("usage: apply-permit-refresh.py <wellnav.db> <jsonl-dir>")
    apply(Path(argv[1]), Path(argv[2]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
