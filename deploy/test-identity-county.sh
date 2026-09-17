#!/bin/bash
set -euo pipefail
docker run --rm --name wellnav-ingest-test \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONPATH=/app \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -m wellnav.ingest load-texas --workers 1 --delay 0.2 --counties 001
