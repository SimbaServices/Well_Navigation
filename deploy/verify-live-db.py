import sqlite3

con = sqlite3.connect("/app/data/wellnav.db")
c = con.cursor()
print("bytes", __import__("os").path.getsize("/app/data/wellnav.db"))
print("wells", c.execute("select count(*) from wells_tx").fetchone()[0])
print(
    "ops",
    c.execute(
        "select count(*) from wells_tx where operator is not null and trim(operator) != ''"
    ).fetchone()[0],
)
print(
    "lease",
    c.execute(
        "select count(*) from wells_tx where lease_name is not null and trim(lease_name) != ''"
    ).fetchone()[0],
)
con.close()
