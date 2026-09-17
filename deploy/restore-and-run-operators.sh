#!/bin/bash
set -eu
echo "Recreating wellnav on the real data volume"
docker rm -f wellnav wellnav-ingest 2>/dev/null || true
docker run -d --name wellnav --restart unless-stopped \
  -e TZ=America/Chicago \
  -p 127.0.0.1:5050:5050 \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  uvicorn app:app --host 0.0.0.0 --port 5050 --workers 2
sleep 2
docker ps --filter name=wellnav --format "{{.Names}} {{.Status}}"
echo "Resuming operator wellbore enrich"
exec docker run --rm --name wellnav-ingest \
  -e PYTHONUNBUFFERED=1 \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-operators --workers 1 --delay 0.15
