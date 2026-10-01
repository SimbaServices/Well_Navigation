#!/bin/bash
set -eu
echo "==== stop ingest so the old full scan cannot write ===="
docker stop wellnav-ingest
echo "==== checkpoint ===="
docker exec -i wellnav python - <<'PY'
import sqlite3
conn = sqlite3.connect("/app/data/wellnav.db", timeout=120)
conn.execute("PRAGMA busy_timeout=120000")
conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
conn.close()
print("checkpoint ok")
PY
echo "==== backup ===="
docker run --rm \
  -v well_navigation_wellnav-data:/data \
  -v /tmp:/out \
  alpine cp /data/wellnav.db /out/wellnav.db.pre-permit-refresh
ls -lh /tmp/wellnav.db.pre-permit-refresh
echo "==== apply ===="
docker run --rm \
  -v well_navigation_wellnav-data:/data \
  -v /tmp/permit_refresh:/in:ro \
  -v /tmp/apply-permit-refresh.py:/apply.py:ro \
  wellnav:latest python /apply.py /data/wellnav.db /in
echo "==== counts ===="
docker exec -i wellnav python - <<'PY'
import sqlite3
conn = sqlite3.connect("/app/data/wellnav.db", timeout=120)
conn.row_factory = sqlite3.Row
for state in ("tx", "nm", "ok", "la"):
    permits = conn.execute(f"SELECT COUNT(*) FROM permits_{state}").fetchone()[0]
    drilled = conn.execute(
        f"""SELECT COUNT(*) FROM wells_{state}
            WHERE NOT (COALESCE(symbol, '') = 'Permitted' OR IFNULL(symnum, -1) IN (2, 9))"""
    ).fetchone()[0]
    cursor = conn.execute(
        "SELECT value FROM ingest_cursors WHERE name = ?",
        (f"{state}_permits_refreshed_at",),
    ).fetchone()
    print(state, "permits", permits, "drilled_wells", drilled, "cursor", cursor[0] if cursor else None)
conn.close()
PY
echo "==== done ===="
