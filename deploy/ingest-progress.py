from wellnav.db import connect

conn = connect()
jobs = conn.execute("SELECT * FROM sync_jobs ORDER BY id DESC LIMIT 4").fetchall()
for job in jobs:
    print("job", dict(job))
    parts = conn.execute(
        """
        SELECT status, COUNT(*) AS n, COALESCE(SUM(wells),0) AS wells, COALESCE(SUM(permits),0) AS permits
        FROM sync_partitions WHERE job_id = ? GROUP BY status
        """,
        (job["id"],),
    ).fetchall()
    for row in parts:
        print("  part", dict(row))
    open_rows = conn.execute(
        """
        SELECT partition_key, partition_name, status, wells, permits, substr(error,1,120) AS error
        FROM sync_partitions
        WHERE job_id = ? AND status != 'ok'
        ORDER BY status, partition_key
        """,
        (job["id"],),
    ).fetchall()
    if open_rows:
        print("  unfinished", len(open_rows))
        for row in open_rows[:12]:
            print("   ", dict(row))
        if len(open_rows) > 12:
            print(f"    ... {len(open_rows) - 12} more")
counts = conn.execute("SELECT COUNT(*) AS n FROM wells_tx").fetchone()
permits = conn.execute("SELECT COUNT(*) AS n FROM permits_tx").fetchone()
print("wells_tx", counts["n"], "permits_tx", permits["n"])
