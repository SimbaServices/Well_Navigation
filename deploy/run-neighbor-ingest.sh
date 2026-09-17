#!/bin/bash
set -euo pipefail
cd /home/wellnav/Well_Navigation
docker restart wellnav
docker rm -f wellnav-neighbor-ingest >/dev/null 2>&1 || true
rm -f /home/wellnav/Well_Navigation/data/neighbor-ingest.log
docker run -d --name wellnav-neighbor-ingest --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-neighbors --delay 0.12
sleep 6
echo '--- neighbor ingest ---'
docker logs --tail 40 wellnav-neighbor-ingest || true
echo '--- web ---'
docker exec wellnav python -c 'from wellnav.repository import REPO; print(REPO.counts("all")); print({s: REPO.counts(s) for s in ("tx","nm","ok","la")})'
