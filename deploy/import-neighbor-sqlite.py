"""Copy wells_la / disposal_la from a sidecar SQLite into the live databases."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def copy_table(src: sqlite3.Connection, dst: sqlite3.Connection, table: str) -> int:
    cols = [row[1] for row in src.execute(f"PRAGMA table_info({table})")]
    if not cols:
        return 0
    col_sql = ",".join(cols)
    placeholders = ",".join("?" * len(cols))
    rows = src.execute(f"SELECT {col_sql} FROM {table}").fetchall()
    dst.executemany(
        f"INSERT OR REPLACE INTO {table} ({col_sql}) VALUES ({placeholders})",
        rows,
    )
    return len(rows)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wells", required=True)
    parser.add_argument("--disposal")
    parser.add_argument("--wells-db", default="/app/data/wellnav.db")
    parser.add_argument("--disposal-db", default="/app/data/disposal.db")
    args = parser.parse_args()

    src = sqlite3.connect(args.wells)
    dst = sqlite3.connect(args.wells_db)
    dst.execute("PRAGMA journal_mode=WAL")
    count = copy_table(src, dst, "wells_la")
    dst.commit()
    src.close()
    dst.close()
    print("wells_la", count)

    dest = sqlite3.connect(args.disposal_db)
    dest.row_factory = sqlite3.Row
    dest.execute(
        """
        CREATE TABLE IF NOT EXISTS disposal_la (
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
        )
        """
    )
    well_db = sqlite3.connect(args.wells_db)
    well_db.row_factory = sqlite3.Row
    inject = well_db.execute(
        """
        SELECT api, well_name, operator, county, district, well_type,
               wellhead_lat, wellhead_lon
        FROM wells_la
        WHERE wellhead_lat IS NOT NULL AND wellhead_lon IS NOT NULL
          AND (
            INSTR(UPPER(IFNULL(well_type,'')), 'IW') > 0
            OR INSTR(UPPER(IFNULL(well_type,'')), 'INJECT') > 0
          )
        """
    ).fetchall()
    seeded = 0
    for row in inject:
        dest.execute(
            """
            INSERT OR REPLACE INTO disposal_la(
                id, operator, facility, permit_no, permit_type, discharge_type,
                permit_expiration, district, county, permit_url, lat, lon
            ) VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, '', ?, ?)
            """,
            (
                2_200_000_000 + int(row["api"][2:10]),
                row["operator"] or "",
                row["well_name"] or "",
                row["api"] or "",
                row["well_type"] or "Injection",
                "",
                row["district"] or "",
                row["county"] or "",
                row["wellhead_lat"],
                row["wellhead_lon"],
            ),
        )
        seeded += 1
    dest.commit()
    dest.close()
    well_db.close()
    print("disposal_la_from_wells", seeded)

    if args.disposal and Path(args.disposal).is_file():
        src = sqlite3.connect(args.disposal)
        dst = sqlite3.connect(args.disposal_db)
        dst.execute("PRAGMA journal_mode=WAL")
        dumped = copy_table(src, dst, "disposal_la")
        dst.commit()
        src.close()
        dst.close()
        print("disposal_la", dumped)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
