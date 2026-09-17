#!/bin/bash
set -euo pipefail
# Test one small county, then full-state identity reload.
docker rm -f wellnav-ingest >/dev/null 2>&1 || true
echo "=== test Blanco (031) ==="
docker run --rm --name wellnav-ingest-test \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-texas --workers 1 --delay 0.2 --counties 031
echo "=== start full load ==="
docker run -d --name wellnav-ingest --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-texas --workers 2 --delay 0.2
sleep 3
docker logs --tail 20 wellnav-ingest
