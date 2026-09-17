from wellnav.db import connect
from wellnav.gis import GIS_MAPSERVER, LAYER_WELL_LOCATIONS
from wellnav.http_client import EWA_BASE, gis_get_json
from wellnav.rrc import RrcClient

conn = connect()
samples = [
    dict(r)
    for r in conn.execute(
        """
        SELECT api8, county_code, symbol, well_no
        FROM wells_tx
        WHERE operator IS NULL OR operator = ''
        ORDER BY api8
        LIMIT 6
        """
    )
]
lease_only = [
    dict(r)
    for r in conn.execute(
        """
        SELECT api8, operator, lease_name
        FROM wells_tx
        WHERE operator IS NOT NULL AND operator != ''
          AND (lease_name IS NULL OR lease_name = '')
        ORDER BY api8
        LIMIT 3
        """
    )
]
print("blank samples", samples)
print("op no lease", lease_only)

client = RrcClient()
for well in samples + lease_only:
    api = well["api8"]
    gis = gis_get_json(
        f"{GIS_MAPSERVER}/{LAYER_WELL_LOCATIONS}/query",
        {
            "where": f"API='{api}'",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        },
    )
    attrs = ((gis.get("features") or [{}])[0].get("attributes") or {}) if gis.get("features") else {}
    print("gis", api, "keys", sorted(attrs)[:20], "nkeys", len(attrs))
    for sch in ("Y", "N", "Both", ""):
        page = client.search_wellbores(api=api, page_size=10, schedule=sch)
        wells = page.get("wells") or []
        hit = wells[0] if wells else {}
        if wells or sch in {"Y", "N"}:
            print(
                "  ewa",
                api,
                "sch",
                repr(sch),
                "hits",
                len(wells),
                "op",
                hit.get("operator"),
                "lease",
                hit.get("lease_name"),
                "none",
                page.get("no_results"),
            )

# Does Jones CSV already contain blank APIs?
blank_jones = {
    r["api8"]
    for r in conn.execute(
        "SELECT api8 FROM wells_tx WHERE county_code='253' AND (operator IS NULL OR operator = '')"
    )
}
print("jones blanks", len(blank_jones))
found = set()
for sch in ("Y", "N"):
    page = client.download_wellbore_csv(county_code="253", schedule=sch)
    apis = {w.get("api") for w in page.get("wells") or []}
    found |= apis
    print("jones csv", sch, "rows", len(apis), "overlap_blanks", len(apis & blank_jones))
print("jones blanks in csv", len(found & blank_jones), "of", len(blank_jones))

# Completion probe
api = samples[0]["api8"]
html = client.http.get(
    f"{EWA_BASE.replace('webapps2', 'webapps')}/CMPL/ewaSearchAction.do?methodToCall=searchByApiNo&apiNo={api}&relatedLink=Y"
)
print("cmpl", api, "len", len(html), "has_op", "Operator" in html, "head", html[html.find("Operator"):html.find("Operator")+200] if "Operator" in html else html[:180])
