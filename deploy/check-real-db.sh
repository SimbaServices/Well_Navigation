#!/bin/bash
set -eu
docker run --rm -e PYTHONUNBUFFERED=1 -e PYTHONPATH=/app -v /home/wellnav/Well_Navigation:/app -v well_navigation_wellnav-data:/app/data wellnav:latest python deploy/check-operators.py
echo '==== cursors ===='
docker run --rm -e PYTHONPATH=/app -v /home/wellnav/Well_Navigation:/app -v well_navigation_wellnav-data:/app/data wellnav:latest python -c "from wellnav.db import connect; c=connect(); print([dict(r) for r in c.execute('select * from ingest_cursors').fetchall()])"
