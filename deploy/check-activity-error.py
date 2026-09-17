import sqlite3
import traceback

from wellnav.db import DB_PATH

print("db", DB_PATH)
conn = sqlite3.connect(str(DB_PATH))
cols = [row[1] for row in conn.execute("PRAGMA table_info(users)")]
print("users_columns", cols)
print("has_last_activity", "last_activity_at" in cols)
print("has_last_login", "last_login_at" in cols)
try:
    conn.execute("SELECT last_activity_at FROM users LIMIT 1")
    print("select_ok")
except Exception:
    traceback.print_exc()
conn.close()
