from wellnav.rrc import WELLBORE_URL, RrcClient

client = RrcClient()
client.http.get(WELLBORE_URL)


def pull(county: str, schedule: str, lease_type: str = "") -> None:
    data = {
        "methodToCall": "generateWellboreCriteriaReportCsv",
        "searchArgs.fieldNumbersArg": "",
        "searchArgs.operatorNumbersArg": "",
        "searchArgs.leaseTypeArg": lease_type,
        "searchArgs.districtCodeArg": "None Selected",
        "searchArgs.leaseNumberArg": "",
        "searchArgs.wellTypeArg": "None Selected",
        "searchArgs.countyCodeArg": county,
        "operatorNames": "",
        "searchArgs.drillingPermitArg": "",
        "searchArgs.apiNoPrefixArg": "",
        "searchArgs.apiNoSuffixArg": "",
        "searchArgs.scheduleTypeArg": schedule,
        "pager.pageSize": "-1",
        "pager.offset": "0",
    }
    body = client.http.post(WELLBORE_URL, data=data)
    lines = [ln for ln in body.splitlines() if ln.strip()]
    header = next((ln for ln in lines if ln.startswith('"API No."') or ln.startswith("API No.")), "")
    rows = [ln for ln in lines if header and ln != header and (ln[:1].isdigit() or ln[:1] == '"')]
    print(
        county,
        "sch",
        schedule,
        "lt",
        lease_type or "-",
        "bytes",
        len(body),
        "lines",
        len(lines),
        "rows",
        len(rows),
        "over",
        "exceeds the maximum" in body or "Ewa_123" in body or "Application Error" in body,
        "csv",
        bool(header),
    )


pull("003", "N")
pull("003", "Y")
pull("485", "N")
pull("485", "Y")
