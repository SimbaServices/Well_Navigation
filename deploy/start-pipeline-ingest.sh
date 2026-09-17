#!/bin/bash
set -eu
mkdir -p /home/wellnav/Well_Navigation/data/pipelines
# Log on the data volume so it survives and is visible in the container.
docker exec -e PYTHONPATH=/app -e TZ=America/Chicago wellnav \
  python -m wellnav.ingest load-pipelines \
  > /tmp/pipeline-ingest.outer.log 2>&1 &
echo "pid $!"
sleep 2
docker exec wellnav ls -lh /app/data/pipelines.db /app/data/pipelines/zips 2>/dev/null || true
