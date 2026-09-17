from wellnav.rrc import WELLBORE_URL, RrcClient

client = RrcClient()
client.http.get(WELLBORE_URL)
data = {
    "methodToCall": "generateWellboreCriteriaReportCsv",
    "searchArgs.fieldNumbersArg": "",
    "searchArgs.operatorNumbersArg": "",
    "searchArgs.leaseTypeArg": "",
    "searchArgs.districtCodeArg": "None Selected",
    "searchArgs.leaseNumberArg": "",
    "searchArgs.wellTypeArg": "None Selected",
    "searchArgs.countyCodeArg": "001",
    "operatorNames": "",
    "searchArgs.drillingPermitArg": "",
    "searchArgs.apiNoPrefixArg": "",
    "searchArgs.apiNoSuffixArg": "",
    "searchArgs.scheduleTypeArg": "N",
    "pager.pageSize": "-1",
    "pager.offset": "0",
}
text = client.http.post(WELLBORE_URL, data=data, timeout=180)
for api in ("00100782", "00100168", "00100008"):
    for line in text.splitlines():
        if api in line:
            print("LINE", api, repr(line[:300]))
            break
    else:
        print("MISSING LINE", api)

print("--- header ---")
for line in text.splitlines():
    if "API No." in line:
        print(repr(line))
        break

from wellnav.db import connect
conn = connect()
print(
    "25300246",
    dict(
        conn.execute(
            "SELECT api8, operator, lease_name FROM wells_tx WHERE api8='25300246'"
        ).fetchone()
        or {}
    ),
)
print(
    "00100782",
    dict(
        conn.execute(
            "SELECT api8, operator, lease_name FROM wells_tx WHERE api8='00100782'"
        ).fetchone()
        or {}
    ),
)
