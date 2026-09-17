#!/bin/bash
set -eu
echo '==== volumes ===='
docker volume ls
echo '==== wellnav mounts ===='
docker inspect wellnav --format '{{json .Mounts}}'
echo '==== compose ===='
ls -la /home/wellnav/Well_Navigation/docker-compose.yml /home/wellnav/Well_Navigation/docker-compose.override.yml 2>/dev/null || true
echo '==== old volume db ===='
docker run --rm -e PYTHONPATH=/app -w /app -v /home/wellnav/Well_Navigation:/app -v well_navigation_wellnav-data:/app/data wellnav:latest python deploy/check-operators.py
echo '==== new volume db ===='
docker run --rm -e PYTHONPATH=/app -w /app -v /home/wellnav/Well_Navigation:/app -v wellnav-data:/app/data wellnav:latest python deploy/check-operators.py
