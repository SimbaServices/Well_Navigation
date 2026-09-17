from wellnav.db import connect
from wellnav.repository import REPO

conn = connect()
print("wells_la", conn.execute("SELECT COUNT(*) FROM wells_la").fetchone()[0])
print(
    "sources",
    dict(conn.execute("SELECT source, COUNT(*) FROM wells_la GROUP BY 1")),
)
print(
    "marshall",
    conn.execute(
        "SELECT api, well_name, county FROM wells_la WHERE location_source = 'la_serial:5'"
    ).fetchone(),
)
print(
    "bpx_minerals",
    conn.execute(
        "SELECT api, well_name, operator FROM wells_la WHERE api='1703127357'"
    ).fetchone(),
)
print(
    "bpx_count",
    conn.execute(
        "SELECT COUNT(*) FROM wells_la WHERE operator LIKE '%BPX%'"
    ).fetchone()[0],
)
print(
    "placeholder_001",
    conn.execute("SELECT COUNT(*) FROM wells_la WHERE well_name='001'").fetchone()[0],
)
conn.close()
print("repo la", REPO.counts("la"))
