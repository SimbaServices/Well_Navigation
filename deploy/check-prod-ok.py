"""Print Oklahoma table presence and counts on the connected wellnav.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from wellnav.db import DB_PATH


def main() -> None:
    conn = sqlite3.connect(str(Path("/app/data/wellnav.db") if Path("/app/data/wellnav.db").is_file() else DB_PATH))
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    print("has_wells_ok", "wells_ok" in tables)
    print("has_permits_ok", "permits_ok" in tables)
    print("has_operators_ok", "operators_ok" in tables)
    for name in ("wells_ok", "permits_ok", "operators_ok", "wells_tx", "permits_tx", "operators_tx"):
        if name in tables:
            print(name, conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        else:
            print(name, "MISSING")
    if "permits_ok" in tables:
        print(
            "permits_ok_active",
            conn.execute(
                "SELECT COUNT(*) FROM permits_ok WHERE status NOT IN ('migrated')"
            ).fetchone()[0],
        )
    conn.close()


if __name__ == "__main__":
    main()
