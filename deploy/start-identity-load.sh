#!/bin/bash
set -euo pipefail
docker rm -f wellnav-ingest >/dev/null 2>&1 || true
docker run -d --name wellnav-ingest --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-texas --workers 2 --delay 0.2
sleep 2
docker ps --filter name=wellnav-ingest
docker logs --tail 15 wellnav-ingest
