#!/bin/bash
set -euo pipefail
cd /home/wellnav/Well_Navigation
docker rm -f wellnav-neighbor-pipes >/dev/null 2>&1 || true
docker run --name wellnav-neighbor-pipes --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-neighbors --skip-wells --skip-disposal --skip-eia --delay 0.12
echo '--- neighbor pipe counts ---'
docker exec -w /app -e PYTHONPATH=/app wellnav python deploy/check-neighbor-pipes.py
docker restart wellnav
