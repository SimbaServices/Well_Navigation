import sqlite3
from pathlib import Path
p = Path("/app/data/wellnav.db")
print("db", p, "exists", p.exists(), "bytes", p.stat().st_size if p.exists() else 0)
con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
c = con.cursor()
print("wells", c.execute("select count(*) from wells").fetchone()[0])
print("with_operator", c.execute("select count(*) from wells where operator is not null and trim(operator) != ''").fetchone()[0])
print("with_lease", c.execute("select count(*) from wells where lease is not null and trim(lease) != ''").fetchone()[0])
print("operators_tx", c.execute("select count(*) from operators_tx").fetchone()[0])
con.close()
