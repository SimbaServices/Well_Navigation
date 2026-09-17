from wellnav.db import connect, set_cursor
from wellnav.ingest.classify import utcnow
from wellnav.states import TX_COUNTIES

conn = connect()
now = utcnow()
conn.execute(
    """
    UPDATE sync_jobs
    SET status = 'interrupted', finished_at = ?, message = ?
    WHERE id = 1 AND status = 'running'
    """,
    (now, "Container recreated; remaining 34 counties completed in job 2"),
)
conn.execute(
    """
    UPDATE sync_partitions
    SET status = 'superseded', finished_at = ?, error = 'completed in job 2'
    WHERE job_id = 1 AND status = 'queued'
    """,
    (now,),
)
set_cursor(conn, "tx_full_load_finished_at", now, now)
conn.commit()
codes = {row[0] for row in conn.execute("SELECT DISTINCT county_code FROM wells_tx")}
missing = [(code, name) for code, name in TX_COUNTIES if code not in codes]
print("wells", conn.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0])
print("permits", conn.execute("SELECT COUNT(*) FROM permits_tx").fetchone()[0])
print("counties_with_wells", len(codes), "of", len(TX_COUNTIES))
print("counties_without_wells", missing)
print("checkpoint", conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone())
conn.close()
