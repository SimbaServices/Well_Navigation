from wellnav.db import connect

conn = connect()
cur = conn.execute("DELETE FROM users WHERE username LIKE 'UiCheck%'")
conn.commit()
print("deleted", cur.rowcount)
