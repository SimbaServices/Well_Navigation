from bs4 import BeautifulSoup
from wellnav.rrc import WELLBORE_URL, RrcClient

client = RrcClient()
html = client.http.get(WELLBORE_URL)
doc = BeautifulSoup(html, "html.parser")
sel = doc.find("select", attrs={"name": "pager.pageSize"})
print("pageSize options:")
if sel:
    for opt in sel.find_all("option"):
        print(repr(opt.get("value")), "->", opt.get_text(" ", strip=True))
else:
    print("missing")

for size in (25, 50, 100):
    page = client.search_wellbores(county_code="001", schedule="Both", page_size=size, offset=0)
    print(
        "size",
        size,
        "total",
        page.get("total"),
        "end",
        page.get("end"),
        "wells",
        len(page.get("wells") or []),
        "pager",
        page.get("pager"),
    )
