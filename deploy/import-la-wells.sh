#!/bin/bash
set -euo pipefail
cd /home/wellnav/Well_Navigation
docker cp /tmp/la_wells_full.db wellnav:/tmp/la_wells_full.db
docker exec -w /app -e PYTHONPATH=/app wellnav python deploy/import-neighbor-sqlite.py --wells /tmp/la_wells_full.db
docker exec -w /app -e PYTHONPATH=/app wellnav python deploy/check-prod-la-wells.py
docker restart wellnav
docker exec wellnav rm -f /tmp/la_wells_full.db
rm -f /tmp/la_wells_full.db
echo imported
