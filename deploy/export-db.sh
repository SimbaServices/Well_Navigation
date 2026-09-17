#!/bin/bash
set -eu
docker rm -f wellnav-ingest 2>/dev/null || true
docker run --rm \
  -v well_navigation_wellnav-data:/app/data \
  wellnav:latest \
  python -c "import sqlite3; c=sqlite3.connect('/app/data/wellnav.db'); c.execute('PRAGMA wal_checkpoint(FULL)'); print('checkpoint', c.execute('PRAGMA page_count').fetchone()[0]); c.close()"
rm -f /tmp/wellnav.db
docker run --rm \
  -v well_navigation_wellnav-data:/data \
  -v /tmp:/out \
  wellnav:latest \
  python -c "import shutil; shutil.copyfile('/data/wellnav.db','/out/wellnav.db'); print('copied')"
ls -lh /tmp/wellnav.db
