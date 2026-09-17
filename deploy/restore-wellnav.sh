#!/bin/bash
set -eu
echo "Recreating wellnav on the real data volume with current source"
cd /home/wellnav/Well_Navigation
docker compose -f docker-compose.yml up -d --force-recreate --no-deps web
sleep 3
docker ps --filter name=wellnav --format "{{.Names}} {{.Status}}"
docker logs --tail 15 wellnav
curl -sS -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:5050/healthz || true
