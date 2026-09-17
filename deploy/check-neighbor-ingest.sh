#!/bin/bash
set -euo pipefail
echo "=== ingest logs ==="
docker logs --tail 30 wellnav-neighbor-ingest || true
echo "=== ingest status ==="
docker inspect -f '{{.State.Status}} {{.State.ExitCode}} {{.State.Running}}' wellnav-neighbor-ingest || true
echo "=== health ==="
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5050/healthz || true
echo "=== counts ==="
docker exec wellnav python - <<'PY'
from wellnav.repository import REPO
print("all", REPO.counts("all"))
for state in ("tx", "nm", "ok", "la"):
    print(state, REPO.counts(state))
PY
echo "=== login has state copy ==="
curl -sS http://127.0.0.1:5050/login | grep -E "New Mexico|Oklahoma|Louisiana|TX, NM" | head
