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
        ("sort", "county"),
        ("dir", "asc"),
        ("mode", "operator"),
    ]
)
html = urllib.request.urlopen(f"http://127.0.0.1:5050/search?{qs}").read().decode()
print("sort_link", 'class="sort-link is-active"' in html or "sort-link is-active" in html)
print("county_sort", "sort=county" in html or "dir=desc" in html)
print("pin_button", ">Pin<" in html)
print("map_selected", "Map selected" in html)
print("pick_all", "well-pick-all" in html)
print("row_data", "data-lat=" in html)
