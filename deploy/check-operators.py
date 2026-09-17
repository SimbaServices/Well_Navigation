from wellnav.db import connect

conn = connect()
tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("tables", tables)
if "operators_tx" in tables:
    print("operators", conn.execute("SELECT COUNT(*) FROM operators_tx").fetchone()[0])
    print("ops_by_status", dict(conn.execute("SELECT status, COUNT(*) FROM operators_tx GROUP BY status").fetchall()))
else:
    print("operators_tx missing")
print("wells", conn.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0])
print(
    "with_operator",
    conn.execute("SELECT COUNT(*) FROM wells_tx WHERE operator IS NOT NULL AND operator != ''").fetchone()[0],
)
print(
    "with_lease",
    conn.execute("SELECT COUNT(*) FROM wells_tx WHERE lease_name IS NOT NULL AND lease_name != ''").fetchone()[0],
)
