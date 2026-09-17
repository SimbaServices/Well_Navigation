#!/bin/bash
set -eu
echo "=== health ==="
curl -sS -o /dev/null -w "healthz=%{http_code}\n" http://127.0.0.1:5050/healthz
curl -sS -o /dev/null -w "nginx=%{http_code}\n" http://127.0.0.1:80/
echo "=== shapely + db ==="
docker exec wellnav python -c 'import shapely, sqlite3; c=sqlite3.connect("/app/data/pipelines.db"); print("shapely", shapely.__version__); print("rows", c.execute("select count(*) from pipelines_tx").fetchone()[0])'
echo "=== pipelines api ==="
curl -sS "http://127.0.0.1:5050/pipelines?bbox=-102.5,31.0,-101.5,32.0&z=8&abandoned=0" -o /tmp/pipelines.json
python3 -c 'import json; d=json.load(open("/tmp/pipelines.json")); print({k:d.get(k) for k in d if k!="features"}); print("n_features", len(d.get("features",[])))'
echo "=== homepage toggle ==="
curl -sS http://127.0.0.1:80/ | grep -c pipeline-toggle || true
echo "=== mounts ==="
docker inspect wellnav --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
echo "=== logs ==="
docker logs --tail 25 wellnav
