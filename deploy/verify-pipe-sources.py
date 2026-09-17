import urllib.request

from wellnav.pipelines import connect, owner_summary, segment_detail

conn = connect()
nm = conn.execute(
    "SELECT tpms_id, operator, quality, system_name FROM pipelines_nm "
    "WHERE quality = 'blm_row' LIMIT 1"
).fetchone()
la = conn.execute(
    "SELECT tpms_id, operator, quality, system_name, status FROM pipelines_la "
    "WHERE quality = 'bsee' AND status = 'I' LIMIT 1"
).fetchone()
print("blm", dict(nm))
print("bsee", dict(la))
print("labels", segment_detail(nm["tpms_id"])["quality_label"], segment_detail(la["tpms_id"])["quality_label"])
print("disclaimer", owner_summary(operator=la["operator"])["disclaimer"])
conn.close()

html = urllib.request.urlopen("http://127.0.0.1:5050/").read().decode()
print("pipes2", "map.js?v=pipes2" in html)
print("legend", "LA EIA+BSEE" in html)
print("hint", "BSEE" in html)
