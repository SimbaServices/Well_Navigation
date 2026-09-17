import sqlite3
from pathlib import Path

p = Path(r"c:\Users\Sam Parker\Simba\Well_Navigation\data\wellnav.db")
print("path", p, "bytes", p.stat().st_size)
con = sqlite3.connect(str(p))
print("checkpoint", con.execute("PRAGMA wal_checkpoint(FULL)").fetchone())
print("wells", con.execute("select count(*) from wells_tx").fetchone()[0])
print(
    "ops",
    con.execute(
        "select count(*) from wells_tx where operator is not null and trim(operator) != ''"
    ).fetchone()[0],
)
print(
    "lease",
    con.execute(
        "select count(*) from wells_tx where lease_name is not null and trim(lease_name) != ''"
    ).fetchone()[0],
)
con.close()
