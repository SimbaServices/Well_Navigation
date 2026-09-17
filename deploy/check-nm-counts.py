"""Print live wellnav.db counts for NM vs TX. Run inside the wellnav container."""

from __future__ import annotations

import sqlite3

from wellnav.repository import REPO

print("counts", {state: REPO.counts(state) for state in ("tx", "nm", "ok", "la")})
conn = sqlite3.connect("/app/data/wellnav.db")
tables = {
    row[0]
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
}
for name in ("wells_nm", "permits_nm", "operators_nm", "wells_tx", "permits_tx", "operators_tx"):
    if name not in tables:
        print(name, "missing")
        continue
    print(name, conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
try:
    from wellnav.disposal import connect as disposal_connect

    dconn = disposal_connect()
    print("disposal_nm", dconn.execute("SELECT COUNT(*) FROM disposal_nm").fetchone()[0])
    dconn.close()
except Exception as exc:
    print("disposal_nm", exc)
if "wells_nm" in tables:
    print("wells_nm sources", dict(conn.execute("SELECT source, COUNT(*) FROM wells_nm GROUP BY source")))
    print(
        "permits_nm status",
        dict(conn.execute("SELECT status, COUNT(*) FROM permits_nm GROUP BY status")),
    )
conn.close()
