#!/bin/sh
docker exec -d wellnav sh -c 'cd /app && PYTHONPATH=/app python -u deploy/ingest-ready-rolls.py > /app/data/rebuild-rolls3.log 2>&1'
echo started
