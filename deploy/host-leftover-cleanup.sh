#!/bin/bash
set -euo pipefail
rm -f /home/wellnav/wellnav.db.hostbak
docker exec wellnav rm -rf /app/data/pipelines/zips /app/data/ingest_scratch
docker exec wellnav rm -f /app/data/ingest.log /app/data/pipeline-ingest.log /tmp/la_import.db
docker rm -f wellnav-la-pipelines wellnav-neighbor-ingest >/dev/null 2>&1 || true
echo "=== after ==="
df -h /
ls -lh /home/wellnav/wellnav.db.hostbak 2>/dev/null || echo "hostbak gone"
docker exec wellnav sh -c 'du -sh /app/data /app/data/pipelines 2>/dev/null; ls /app/data'
docker ps -a --format '{{.Names}} {{.Status}}'
docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
