#!/bin/bash
set -eu
echo "=== health ==="
curl -sS -o /dev/null -w "healthz=%{http_code}\n" http://127.0.0.1:5050/healthz
curl -sS -o /dev/null -w "nginx=%{http_code}\n" http://127.0.0.1:80/
echo "=== homepage context ==="
curl -sS http://127.0.0.1:80/ | grep -c 'name="scope" value="pipelines"'
curl -sS http://127.0.0.1:80/ | grep -c 'pipe_mode'
echo "=== search enterprise ==="
curl -sS "http://127.0.0.1:5050/pipelines/search?scope=pipelines&pipe_mode=operator&q=ENTERPRISE&commit=1" -o /tmp/pipe-search.html
python3 - <<'PY'
from pathlib import Path
html = Path("/tmp/pipe-search.html").read_text(encoding="utf-8", errors="replace")
print("bytes", len(html))
print("has_table", "pipeline-table" in html or "pipeline-row" in html)
print("has_error_banner", 'class="banner error"' in html)
print("sample", html[html.find("<h2>"):html.find("<h2>")+80] if "<h2>" in html else html[:200])
PY
echo "=== owner + segment ==="
docker exec wellnav python - <<'PY'
from wellnav.pipelines import connect, search_operators, segment_detail, owner_summary
ops = search_operators("ENTERPRISE", limit=3)
print("operators", [(o["operator"], o["p5"], o["segments"]) for o in ops[:3]])
if ops and ops[0]["p5"]:
    summary = owner_summary(p5=ops[0]["p5"])
    print("summary", {k: summary.get(k) for k in ("operator","p5","segments","systems","t4_permits")})
    print("identity", (summary or {}).get("identity"))
conn = connect()
row = conn.execute("SELECT tpms_id FROM pipelines_tx WHERE p5_num = ? LIMIT 1", (ops[0]["p5"],)).fetchone() if ops else None
conn.close()
if row:
    detail = segment_detail(row["tpms_id"])
    print("segment", detail["id"], detail["operator"], detail["p5"], detail["system"], detail["t4"])
PY
echo "=== api ==="
P5=$(docker exec wellnav python -c "from wellnav.pipelines import search_operators; r=search_operators('ENTERPRISE', limit=1); print(r[0]['p5'] if r else '')")
echo "p5=$P5"
curl -sS "http://127.0.0.1:5050/pipelines/owner?p5=${P5}" | python3 -c "import sys,json; d=json.load(sys.stdin); print({k:d.get(k) for k in ('operator','p5','segments','systems')}); print('identity', d.get('identity'))"
echo "=== logs ==="
docker logs --tail 12 wellnav
