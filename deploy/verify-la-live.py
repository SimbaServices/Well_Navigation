from wellnav.db import connect
from wellnav.repository import WellRepository

conn = connect()
row = conn.execute(
    "SELECT api, well_name, operator, county FROM wells_la WHERE api='1703127357'"
).fetchone()
print("target", tuple(row) if row else None)
print("bpx", conn.execute("SELECT COUNT(*) FROM wells_la WHERE UPPER(operator) LIKE '%BPX%'").fetchone()[0])
print("total", conn.execute("SELECT COUNT(*) FROM wells_la").fetchone()[0])
repo = WellRepository(conn)
found = repo.search(state="la", mode="api", q="1703127357")
print("search", found["total"], found["wells"][0]["well_name"] if found["wells"] else None)
ops = repo.search(state="la", mode="operator", q="bpx")
print("search_bpx", ops["total"])
conn.close()
