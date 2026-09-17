import sqlite3
con = sqlite3.connect("/app/data/wellnav.db")
c = con.cursor()
print("wells_tx", c.execute("select count(*) from wells_tx").fetchone()[0])
print("with_operator", c.execute("select count(*) from wells_tx where operator is not null and trim(operator) != ''").fetchone()[0])
print("with_lease", c.execute("select count(*) from wells_tx where lease is not null and trim(lease) != ''").fetchone()[0])
print("operators_tx", c.execute("select count(*) from operators_tx").fetchone()[0])
print("permits_tx", c.execute("select count(*) from permits_tx").fetchone()[0])
con.close()
