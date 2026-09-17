from collections import Counter
from wellnav.parsers import parse_wellbore_csv
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
parsed = parse_wellbore_csv(text)
wells = parsed["wells"]
counts = Counter(w["api"] for w in wells)
dups = [api for api, n in counts.items() if n > 1]
print("rows", len(wells), "unique", len(counts), "dup_apis", len(dups))
hits = [w for w in wells if w["api"] == "00100782"]
print("00100782 occurrences", hits)
print("parsed 00100782 last", hits[-1] if hits else None)
print("parsed 00100782 any op", [w for w in hits if w.get("operator")])
