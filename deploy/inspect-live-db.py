import sqlite3
from pathlib import Path
data = Path("/app/data")
print("files")
for p in sorted(data.iterdir()):
    print(f"  {p.name} {p.stat().st_size}")
p = data / "wellnav.db"
con = sqlite3.connect(str(p))
c = con.cursor()
print("tables")
for row in c.execute("select name from sqlite_master where type='table' order by 1"):
    print(" ", row[0])
print("user_version", c.execute("pragma user_version").fetchone()[0])
print("journal_mode", c.execute("pragma journal_mode").fetchone()[0])
con.close()
