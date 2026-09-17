#!/bin/bash
set -eu
echo "==== uploaded files ===="
ls -lh /tmp/wellnav.db.gz /tmp/swap-live-db.sh
python3 - <<'PY'
import os
p = "/tmp/wellnav.db.gz"
n = os.path.getsize(p)
print("gz_bytes", n)
if n != 138866059:
    raise SystemExit(f"gzip size mismatch: got {n} expected 138866059")
PY
echo "==== decompress ===="
gzip -dc /tmp/wellnav.db.gz > /tmp/wellnav.db
ls -lh /tmp/wellnav.db
python3 - <<'PY'
import os
n = os.path.getsize("/tmp/wellnav.db")
print("db_bytes", n)
if n != 604614656:
    raise SystemExit(f"db size mismatch: got {n} expected 604614656")
PY
echo "==== stop wellnav ===="
docker stop wellnav
echo "==== backup live db ===="
docker run --rm \
  -v well_navigation_wellnav-data:/data \
  -v /tmp:/out \
  alpine cp /data/wellnav.db /out/wellnav.db.hostbak
ls -lh /tmp/wellnav.db.hostbak
echo "==== replace volume db ===="
docker run --rm \
  -v well_navigation_wellnav-data:/data \
  -v /tmp:/in \
  alpine sh -c 'rm -f /data/wellnav.db-wal /data/wellnav.db-shm; cp /in/wellnav.db /data/wellnav.db; ls -lh /data/wellnav.db'
echo "==== start wellnav ===="
docker start wellnav
sleep 4
echo "==== status ===="
docker ps --filter name=wellnav --format "{{.Names}} {{.Status}}"
docker logs --tail 15 wellnav
curl -sS -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:5050/healthz || true
curl -sS -o /dev/null -w "site=%{http_code}\n" http://127.0.0.1:80/ || true
