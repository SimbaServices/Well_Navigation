#!/bin/bash
set -eu
echo "==== before ===="
docker ps -a --format "{{.Names}} {{.Status}}" | sed -n "/wellnav/p"
echo "==== relaunch ===="
cd /home/wellnav/Well_Navigation
docker compose -f docker-compose.yml up -d --force-recreate --no-deps web
sleep 3
echo "==== after ===="
docker ps --filter name=wellnav --format "{{.Names}} {{.Status}}"
docker inspect wellnav --format "image={{.Config.Image}} mounts={{range .Mounts}}{{.Name}}{{.Source}}->{{.Destination}} {{end}}"
echo "==== logs ===="
docker logs --tail 20 wellnav
echo "==== health ===="
curl -sS -o /dev/null -w "health=%{http_code}\n" http://127.0.0.1:5050/healthz || true
curl -sS -o /dev/null -w "site=%{http_code}\n" http://127.0.0.1:80/ || true
