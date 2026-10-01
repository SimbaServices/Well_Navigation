#!/bin/bash
# Recreate the public wellnav container on the live bind + data volume.
set -eu
cd /home/wellnav/Well_Navigation
docker rm -f wellnav 2>/dev/null || true
docker compose -f docker-compose.yml up -d --build --force-recreate --no-deps web
if ! docker ps --format '{{.Names}}' | grep -qx wellnav-ingest; then
  docker compose -f docker-compose.yml up -d --no-deps --no-build ingest
fi
sleep 2
docker inspect wellnav --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
echo 'web restored onto /home/wellnav/Well_Navigation and well_navigation_wellnav-data'
