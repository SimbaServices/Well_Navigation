from wellnav.accounts import USERS

print("has_touch", hasattr(USERS, "touch_activity"))
print("has_record", hasattr(USERS, "record_login"))
row = USERS._conn().execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone() if False else None
user = None
from wellnav.repository import REPO

first = REPO.conn.execute("SELECT id FROM users ORDER BY id LIMIT 1").fetchone()
print("first_user_id", first["id"] if first else None)
if first:
    before = REPO.conn.execute(
        "SELECT last_activity_at FROM users WHERE id = ?",
        (first["id"],),
    ).fetchone()["last_activity_at"]
    USERS.touch_activity(int(first["id"]), min_interval=0)
    after = USERS.get(int(first["id"]))
    print("public_activity", after.get("last_activity_at") if after else None)
    print("touched", bool(after and after.get("last_activity_at")))
