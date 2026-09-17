import sqlite3
import urllib.parse
import urllib.request

con = sqlite3.connect("/app/data/wellnav.db")
row = con.execute(
    """
    SELECT operator_number, operator
    FROM wells_tx
    WHERE operator_number = '386310'
    LIMIT 1
    """
).fetchone()
qs = urllib.parse.urlencode(
    [
        ("opn", f"{row[0]}|{row[1]}"),
        ("op", row[0]),
        ("name", "UNIVERSITY"),
        ("use_name", "1"),
        ("mode", "name"),
    ]
)
html = urllib.request.urlopen(f"http://127.0.0.1:5050/search?{qs}").read().decode()
print("stacked_name_chip", "Name UNIVERSITY" in html or "Name “UNIVERSITY”" in html or "UNIVERSITY" in html)
print("still_operator", "HILCORP" in html)
print("operator_col", "col-operator" in html)
print("len", len(html))
