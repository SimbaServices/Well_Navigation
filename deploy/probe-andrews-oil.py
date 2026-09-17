from wellnav.rrc import RrcClient

client = RrcClient()
queries = [
    {"county_code": "003", "schedule": "Y", "lease_type": "O", "district": "08"},
    {"county_code": "003", "schedule": "Y", "lease_type": "O", "district": "8A"},
    {"county_code": "003", "schedule": "N", "lease_type": "O", "district": "08"},
    {"county_code": "003", "schedule": "Y", "lease_type": "O", "api_prefix": "0031"},
    {"county_code": "003", "schedule": "Both", "lease_type": "O", "district": "08"},
    {"county_code": "003", "schedule": "Both", "lease_type": "", "district": "08"},
]
for q in queries:
    try:
        page = client.search_wellbores(page_size=10, offset=0, **q)
        print(
            "OK",
            q,
            "total",
            page.get("total"),
            "wells",
            len(page.get("wells") or []),
            "err",
            (page.get("error") or "")[:80],
        )
    except Exception as exc:
        print("ERR", q, type(exc).__name__, str(exc)[:200])
