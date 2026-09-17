from wellnav.accounts import USERS
from wellnav.db import connect

conn = connect()
row = conn.execute("PRAGMA table_info(users)").fetchall()
names = {item["name"] if isinstance(item, dict) else item[1] for item in row}
if "session_version" not in names:
    raise SystemExit("missing session_version")
print("session_version_ok")
print("rotate", hasattr(USERS, "rotate_session_version"))
