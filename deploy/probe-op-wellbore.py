from wellnav.rrc import RrcClient

client = RrcClient()
for num, name in (("000016", "A & E MINERALS, LLC"), ("630591", "OXY USA INC.")):
    page = client.search_wellbores(
        operator_numbers=[num],
        operator_names=f"{num} - {name}",
        schedule="Y",
        page_size=100,
        offset=0,
    )
    wells = page.get("wells") or []
    sample = wells[0] if wells else None
    print(
        num,
        "total",
        page.get("total"),
        "wells",
        len(wells),
        "over",
        page.get("over_limit"),
        "sample",
        {k: sample.get(k) for k in ("api", "lease_name", "lease_no", "operator", "district")}
        if sample
        else None,
    )
