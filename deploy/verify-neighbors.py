from wellnav.disposal import stats as disposal_stats
from wellnav.pipelines import listed_pipeline_tables, connect as pipe_connect, init_schema as init_pipes
from wellnav.repository import REPO

print("wells", REPO.counts("all"))
for state in ("tx", "nm", "ok", "la"):
    print(state, REPO.counts(state))
    hit = REPO.search(state=state, mode="name", q="UNIT", page_size=3)
    print(" search", state, "total", hit["total"], "sample", [w["api_display"] for w in hit["wells"][:3]])

print("disposal", disposal_stats())
conn = pipe_connect()
init_pipes(conn)
for table in listed_pipeline_tables(conn):
    print(table, conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
conn.close()
