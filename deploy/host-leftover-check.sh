#!/bin/bash
set -euo pipefail
echo "=== disk ==="
df -h /
echo "=== leftover candidates ==="
ls -lh /home/wellnav/wellnav.db.hostbak 2>/dev/null || echo "no hostbak"
ls -lh /home/wellnav/la_import.db 2>/dev/null || echo "no home la_import"
docker exec wellnav ls -lh /tmp/la_import.db 2>/dev/null || echo "no container la_import"
docker exec wellnav du -sh /app/data/pipelines/zips 2>/dev/null || echo "no pipeline zips"
docker exec wellnav du -sh /app/data/ingest_scratch /app/data/ingest.log /app/data/pipeline-ingest.log 2>/dev/null || true
echo "=== docker images ==="
docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}'
echo "=== docker volumes ==="
docker volume ls
echo "=== unused containers ==="
docker ps -a --format '{{.Names}} {{.Status}}'
