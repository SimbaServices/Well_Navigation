from wellnav.http_client import CurlSession
from wellnav.parsers import PAGER_RE

ORG_URL = "https://webapps2.rrc.texas.gov/EWA/organizationQueryAction.do"
http = CurlSession()
http.get(ORG_URL)

def search(code, page_size, offset=0):
    body = {
        "methodToCall": "search",
        "searchArgs.operatorNumbersArg": "",
        "searchArgs.orgStatusArg": "None Selected",
        "searchArgs.orgTypeArg": "None Selected",
        "searchArgs.builtStartDateArg": "",
        "searchArgs.builtEndDateArg": "",
        "searchArgs.lastP5FiledStartDateArg": "",
        "searchArgs.lastP5FiledEndDateArg": "",
        "searchArgs.expirationStartDateArg": "",
        "searchArgs.expirationEndDateArg": "",
        "searchArgs.locationAddressArg": "",
        "searchArgs.mailingAddressArg": "",
        "searchArgs.renewalMonthArg": "None Selected",
        "searchArgs.activitySpecialityCodes": code,
        "pager.pageSize": str(page_size),
        "pager.offset": str(offset),
    }
    html = http.post(ORG_URL, data=body)
    pager = PAGER_RE.search(html)
    ops = html.count("searchByOperatorNo&operatorNo=")
    return {
        "len": len(html),
        "pager": pager.group(0) if pager else None,
        "ops": ops,
        "err": "Application Error" in html,
        "results": "Organization Operator Query Results" in html,
    }

print("oil100", search("OPRS/O", 100))
print("oil-1", search("OPRS/O", -1))
print("gas100", search("OPRS/G", 100))
print("gas-1", search("OPRS/G", -1))
