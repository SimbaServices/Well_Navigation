#!/bin/bash
set -euo pipefail
sed -i 's/\r$//' /tmp/start-identity-only.sh
docker rm -f wellnav-ingest 2>/dev/null || true
bash /tmp/start-identity-only.sh
echo '==== coverage ===='
docker exec -e PYTHONPATH=/app -w /app wellnav-ingest python deploy/check-identity.py
echo '==== logs ===='
docker logs --tail 20 wellnav-ingest
