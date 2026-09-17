#!/bin/bash
df -h / /var/lib/docker /home/wellnav
docker exec wellnav ls -lh /app/data/*.db /app/data/*.log 2>/dev/null || true
