#!/bin/bash
set +e
echo "==== mounts ===="
docker inspect wellnav --format '{{json .Mounts}}'
echo
echo "==== app import line ===="
docker exec wellnav sed -n '16p' /app/app.py
echo "==== host app import line ===="
sed -n '16p' /home/wellnav/Well_Navigation/app.py
echo "==== accounts exports ===="
docker exec wellnav grep -n '^USERS\|^SAVED\|^CACHE\|^RECENT' /app/wellnav/accounts.py
echo "==== ingest still up ===="
docker ps --filter name=wellnav-ingest --format '{{.Names}} {{.Status}}'
docker logs --tail 8 wellnav-ingest
