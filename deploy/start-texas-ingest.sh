#!/bin/bash
set -euo pipefail
docker exec wellnav bash -c 'rm -f /app/data/ingest.log'
docker exec -d wellnav bash -c 'python -m wellnav.ingest load-texas --workers 4 --delay 0.15 > /app/data/ingest.log 2>&1'
sleep 4
echo '--- processes ---'
docker exec wellnav bash -c 'ps -ef | grep "[w]ellnav.ingest" || true'
echo '--- log ---'
docker exec wellnav bash -c 'tail -n 50 /app/data/ingest.log || true'
