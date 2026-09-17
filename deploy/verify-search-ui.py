import sqlite3
import urllib.parse
import urllib.request

con = sqlite3.connect("/app/data/wellnav.db")
row = con.execute(
    """
    SELECT operator_number, operator, COUNT(*) AS n
    FROM wells_tx
    WHERE operator_number IS NOT NULL AND operator_number != ''
      AND operator IS NOT NULL AND operator != ''
    GROUP BY operator_number
    ORDER BY n DESC
    LIMIT 1
    """
).fetchone()
print("sample_op", row[0], row[1], row[2])
qs = urllib.parse.urlencode(
    {
        "add_op_number": row[0],
        "add_op_name": row[1],
        "mode": "operator",
        "offset": 0,
    }
)
html = urllib.request.urlopen(f"http://127.0.0.1:5050/search?{qs}").read().decode()
checks = [
    (">Operator<" in html or "col-operator" in html, "operator_column"),
    ("filter-chip" in html, "filter_chip"),
    (row[1][:12] in html, "operator_name"),
    ("map-toggle" in html, "map_button"),
    ("well-map" in html or "Map this page" in html, "map_toolbar"),
]
for ok, name in checks:
    print(("OK" if ok else "FAIL"), name)
print("html_len", len(html))
