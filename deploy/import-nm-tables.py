"""Replace wells_nm / permits_nm / operators_nm from a sidecar SQLite file.

Does not touch Texas, Oklahoma, or Louisiana tables.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/nm_official.db")
DST = Path(sys.argv[2] if len(sys.argv) > 2 else "/app/data/wellnav.db")
TABLES = ("wells_nm", "permits_nm", "operators_nm")


def _cols(conn: sqlite3.Connection, table: str, *, schema: str = "main") -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def main() -> int:
    if not SRC.is_file():
        print(f"missing source {SRC}", flush=True)
        return 1
    dst = sqlite3.connect(str(DST), timeout=60)
    dst.row_factory = sqlite3.Row
    dst.execute("PRAGMA busy_timeout=60000")
    dst.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    tx_before = dst.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0]
    permits_tx_before = dst.execute("SELECT COUNT(*) FROM permits_tx").fetchone()[0]
    dst.execute("ATTACH DATABASE ? AS src", (str(SRC),))
    for table in TABLES:
        if _cols(dst, table) != _cols(dst, table, schema="src"):
            print(
                f"column mismatch {table}",
                _cols(dst, table),
                _cols(dst, table, schema="src"),
                flush=True,
            )
            dst.close()
            return 1
        src_count = dst.execute(f"SELECT COUNT(*) FROM src.{table}").fetchone()[0]
        dst.execute(f"DELETE FROM {table}")
        dst.execute(f"INSERT INTO {table} SELECT * FROM src.{table}")
        print(f"imported {table} {src_count}", flush=True)
    dst.commit()
    dst.execute("DETACH DATABASE src")
    tx_after = dst.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0]
    permits_tx_after = dst.execute("SELECT COUNT(*) FROM permits_tx").fetchone()[0]
    if tx_before != tx_after or permits_tx_before != permits_tx_after:
        print("texas counts changed", tx_before, tx_after, permits_tx_before, permits_tx_after, flush=True)
        dst.close()
        return 1
    print(
        "ok",
        {table: dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES},
        "wells_tx",
        tx_after,
        flush=True,
    )
    dst.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
