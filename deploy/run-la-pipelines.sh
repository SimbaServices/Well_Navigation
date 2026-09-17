#!/bin/bash
set -euo pipefail
docker rm -f wellnav-la-pipelines >/dev/null 2>&1 || true
docker run -d --name wellnav-la-pipelines --restart=no \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-neighbors --states la --skip-wells --skip-disposal --delay 0.12
sleep 5
docker logs --tail 30 wellnav-la-pipelines || true
