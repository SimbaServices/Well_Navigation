from wellnav.rrc import RrcClient

client = RrcClient()
queries = [
    {"county_code": "009", "schedule": "Both", "lease_type": ""},
    {"county_code": "009", "schedule": "Y", "lease_type": ""},
    {"county_code": "009", "schedule": "Y", "lease_type": "O"},
    {"county_code": "009", "schedule": "Y", "lease_type": "G"},
    {"county_code": "009", "schedule": "N", "lease_type": "O"},
    {"county_code": "003", "schedule": "Both", "lease_type": ""},
    {"county_code": "003", "schedule": "Y", "lease_type": "O"},
    {"county_code": "029", "schedule": "Both", "lease_type": ""},
    {"county_code": "029", "schedule": "Y", "lease_type": "O"},
]
for q in queries:
    try:
        page = client.search_wellbores(page_size=10, offset=0, **q)
        print("OK", q, "total", page.get("total"), "wells", len(page.get("wells") or []))
    except Exception as exc:
        print("ERR", q, type(exc).__name__, str(exc)[:160])
