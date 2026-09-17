from wellnav.db import connect
from wellnav.rrc import RrcClient

conn = connect()
client = RrcClient()

# Anderson: was 00100782 in the county CSV?
page = client.download_wellbore_csv(county_code="001", schedule="N")
wells = {w["api"]: w for w in page.get("wells") or []}
print("anderson N rows", len(wells))
print("00100782 in csv", "00100782" in wells, wells.get("00100782"))
print("00100168 in csv", "00100168" in wells, wells.get("00100168"))

# Jones blanks in CSV: do they have operator names?
blank = {
    r["api8"]
    for r in conn.execute(
        "SELECT api8 FROM wells_tx WHERE county_code='253' AND (operator IS NULL OR operator = '')"
    )
}
page = client.download_wellbore_csv(county_code="253", schedule="N")
in_csv = [w for w in page.get("wells") or [] if w.get("api") in blank]
with_op = [w for w in in_csv if w.get("operator")]
with_lease = [w for w in in_csv if w.get("lease_name")]
print("jones blanks in N csv", len(in_csv), "with_op", len(with_op), "with_lease", len(with_lease))
print("sample in csv", in_csv[:3])
print("sample with op", with_op[:3])

# HTML vs CSV for one Jones blank that is in CSV with empty op, and one not in CSV
need = next((w for w in in_csv if not w.get("operator")), None)
print("empty-op csv row", need)
if need:
    html = client.search_wellbores(api=need["api"], page_size=10, schedule="N")
    hits = html.get("wells") or []
    print("html for empty-op", need["api"], hits[:1])

not_in = next(iter(blank - {w.get("api") for w in page.get("wells") or []}), None)
print("jones blank not in csv", not_in)
if not_in:
    html = client.search_wellbores(api=not_in, page_size=10, schedule="N")
    print("html not-in-csv", not_in, "hits", len(html.get("wells") or []), (html.get("wells") or [{}])[0])
