#!/bin/bash
echo "==== log ===="
docker exec wellnav tail -n 40 /app/data/pipeline-ingest.log
echo "==== files ===="
docker exec wellnav sh -c 'ls /app/data/pipelines/zips 2>/dev/null | wc -l; du -sh /app/data/pipelines 2>/dev/null || true'
echo "==== db ===="
docker exec wellnav python -c "import sqlite3; c=sqlite3.connect('/app/data/pipelines.db'); print('rows', c.execute('select count(*) from pipelines_tx').fetchone()[0])"
echo "==== process ===="
docker exec wellnav sh -c 'ps aux | grep ingest | grep -v grep || true'
