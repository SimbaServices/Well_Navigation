"""Print production LA table counts. Run inside the wellnav container."""

from __future__ import annotations

import sqlite3

conn = sqlite3.connect("/app/data/wellnav.db")
tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
print("tables", sorted(name for name in tables if name.endswith("_la")))
for table in ("wells_la", "permits_la", "operators_la"):
    if table in tables:
        print(table, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    else:
        print(table, "MISSING")
print(
    "sources",
    dict(conn.execute("SELECT IFNULL(source,''), COUNT(*) FROM wells_la GROUP BY 1")),
)
print(
    "bpx",
    conn.execute(
        "SELECT api, well_name, operator FROM wells_la WHERE api='1703127357'"
    ).fetchone(),
)
conn.close()
