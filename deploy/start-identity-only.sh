#!/bin/bash
set -eu
docker rm -f wellnav-ingest 2>/dev/null || true
docker run -d --name wellnav-ingest -e PYTHONUNBUFFERED=1 -v /home/wellnav/Well_Navigation:/app -v well_navigation_wellnav-data:/app/data wellnav:latest python -m wellnav.ingest load-texas --identity-only --workers 2 --delay 0.2
echo started
docker logs --tail 8 wellnav-ingest
