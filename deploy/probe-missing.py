from wellnav.db import connect
from wellnav.rrc import RrcClient

conn = connect()
counties = list(
    conn.execute(
        """
        SELECT county_code, COUNT(*) AS missing
        FROM wells_tx
        WHERE operator IS NULL OR operator = ''
        GROUP BY county_code
        ORDER BY missing DESC
        """
    )
)
print("counties_with_missing", len(counties), "total_missing", sum(r["missing"] for r in counties))
print("counties_over_2k", sum(1 for r in counties if r["missing"] >= 2000))

# Small county: how many off-schedule wellbores exist, and do they match blanks?
client = RrcClient()
page = client.search_wellbores(county_code="001", schedule="N", page_size=100, offset=0)
print(
    "anderson_N",
    "total",
    page.get("total"),
    "got",
    len(page.get("wells") or []),
    "over",
    page.get("over_limit"),
)
page_y = client.search_wellbores(county_code="001", schedule="Y", page_size=100, offset=0)
print(
    "anderson_Y",
    "total",
    page_y.get("total"),
    "got",
    len(page_y.get("wells") or []),
    "over",
    page_y.get("over_limit"),
)

missing = {
    r["api8"]
    for r in conn.execute(
        "SELECT api8 FROM wells_tx WHERE county_code='001' AND (operator IS NULL OR operator = '')"
    )
}
print("anderson_missing", len(missing))
found = {w.get("api") for w in (page.get("wells") or []) if w.get("api") in missing}
print("anderson_N_page1_hits_missing", len(found), "of", min(100, page.get("total") or 0))
