from wellnav.db import connect

conn = connect()
wells = conn.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0]
permits = conn.execute("SELECT COUNT(*) FROM permits_tx").fetchone()[0]
lease = conn.execute("SELECT COUNT(*) FROM wells_tx WHERE lease_name IS NOT NULL AND lease_name != ''").fetchone()[0]
op = conn.execute("SELECT COUNT(*) FROM wells_tx WHERE operator IS NOT NULL AND operator != ''").fetchone()[0]
dist = conn.execute("SELECT COUNT(*) FROM wells_tx WHERE district IS NOT NULL AND district != ''").fetchone()[0]
pleas = conn.execute("SELECT COUNT(*) FROM permits_tx WHERE lease_name IS NOT NULL AND lease_name != ''").fetchone()[0]
print("wells", wells, "permits", permits)
print("wells_with_lease", lease, "wells_with_operator", op, "wells_with_district", dist)
print("permits_with_lease", pleas)
job = conn.execute("SELECT id, status, message, started_at, finished_at FROM sync_jobs ORDER BY id DESC LIMIT 1").fetchone()
print("job", dict(job))
parts = conn.execute(
    "SELECT status, COUNT(*) n FROM sync_partitions WHERE job_id=? GROUP BY status",
    (job["id"],),
).fetchall()
for row in parts:
    print("part", dict(row))
sample = conn.execute(
    """
    SELECT api8, well_name, lease_name, lease_no, district, operator, operator_number, county
    FROM wells_tx
    WHERE lease_name IS NOT NULL AND lease_name != ''
    LIMIT 5
    """
).fetchall()
for row in sample:
    print("sample", dict(row))
zeros = conn.execute(
    """
    SELECT county_code, county, COUNT(*) n,
           SUM(CASE WHEN lease_name IS NOT NULL AND lease_name != '' THEN 1 ELSE 0 END) with_lease
    FROM wells_tx
    GROUP BY county_code
    HAVING with_lease = 0 AND n > 50
    ORDER BY n DESC
    LIMIT 15
    """
).fetchall()
print("zero_lease_counties", len(zeros))
for row in zeros:
    print("zero", dict(row))
