from wellnav.pipelines import connect, init_schema, query_geojson

conn = connect()
init_schema(conn)
for table in ("pipelines_tx", "pipelines_nm", "pipelines_ok", "pipelines_la"):
    total = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
    rows = conn.execute(
        f"SELECT IFNULL(quality,'t4') AS q, COUNT(*) AS n FROM {table} GROUP BY q ORDER BY n DESC"
    ).fetchall()
    print(table, "total", total, {row["q"]: row["n"] for row in rows})
conn.close()

nm = query_geojson(bbox="-104.4,32.1,-103.6,32.8", zoom=11, abandoned=False)
la = query_geojson(bbox="-90.4,28.9,-89.4,29.6", zoom=10, abandoned=False)
print("nm permian overlay", nm["meta"]["count"], "of", nm["meta"]["stored"])
print("la coast overlay", la["meta"]["count"], "of", la["meta"]["stored"])
