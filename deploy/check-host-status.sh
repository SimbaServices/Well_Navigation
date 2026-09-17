#!/bin/bash
set +e
echo "==== wellnav logs ===="
docker logs --tail 40 wellnav
echo "==== wellnav inspect ===="
docker inspect wellnav --format "status={{.State.Status}} health={{.State.Health.Status}} started={{.State.StartedAt}}"
echo "==== db ===="
docker run --rm -e PYTHONPATH=/app \
  -v /home/wellnav/Well_Navigation:/app \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest python deploy/check-operators.py
echo "==== ingest ===="
docker logs --tail 20 wellnav-ingest
echo "==== curl host ===="
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5050/healthz
