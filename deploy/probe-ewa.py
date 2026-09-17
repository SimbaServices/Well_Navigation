from bs4 import BeautifulSoup
from wellnav.rrc import WELLBORE_URL, RrcClient

client = RrcClient()
html = client.http.get(WELLBORE_URL)
doc = BeautifulSoup(html, "html.parser")
print("==== inputs matching lease/schedule/district ====")
for tag in doc.find_all(["input", "select"]):
    name = tag.get("name") or ""
    if any(token in name.lower() for token in ("lease", "schedule", "district", "county")):
        print(tag.name, name, "type=", tag.get("type"), "value=", tag.get("value"))
        if tag.name == "select":
            print("  options", [o.get("value") for o in tag.find_all("option")[:8]])

print("==== Anderson 001 Y page ====")
page = client.search_wellbores(county_code="001", schedule="Y", page_size=10, offset=0)
print({k: page.get(k) for k in ("total", "start", "end", "pager")})
print("wells", len(page.get("wells") or []))
if page.get("wells"):
    print(page["wells"][0])

print("==== Anderson 001 Both page ====")
page = client.search_wellbores(county_code="001", schedule="Both", page_size=10, offset=0)
print({k: page.get(k) for k in ("total", "start", "end", "pager")})
print("wells", len(page.get("wells") or []))
