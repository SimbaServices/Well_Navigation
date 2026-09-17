import time

from wellnav.db import connect
from wellnav.operators import CANON_META_KEY, canonicalize_state
from wellnav.states import APP_STATES

conn = connect()
started = time.time()
total = 0
for code in APP_STATES:
    t0 = time.time()
    changed = canonicalize_state(conn, code)
    conn.commit()
    total += changed
    print(code, "changed", changed, "sec", round(time.time() - t0, 1), flush=True)
conn.execute(
    "INSERT INTO meta(key, value) VALUES (?, ?) "
    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
    (CANON_META_KEY, "1"),
)
conn.commit()
print("total", total, "sec", round(time.time() - started, 1), flush=True)
