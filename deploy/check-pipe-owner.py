from wellnav.pipelines import connect, segment_detail

conn = connect()
row = conn.execute(
    "SELECT tpms_id FROM pipelines_tx WHERE p5_num = '253368' LIMIT 1"
).fetchone()
conn.close()
detail = segment_detail(row["tpms_id"])
print(detail["id"], detail["operator"], detail["p5"], detail["t4"], detail["system"])
print("identity", detail.get("identity"))
