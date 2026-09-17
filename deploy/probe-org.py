from wellnav.http_client import CurlSession
from wellnav.parsers import PAGER_RE

ORG_URL = "https://webapps2.rrc.texas.gov/EWA/organizationQueryAction.do"
http = CurlSession()
form = http.get(ORG_URL)
print("form_len", len(form), "title", "Organization" in form or "P-5" in form or "organization" in form.lower())
print("form_has_activity", "activitySpecialityCodes" in form)
print("form_snippet", form[form.lower().find("activity") : form.lower().find("activity") + 400] if "activity" in form.lower() else "no activity")

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
    "searchArgs.activitySpecialityCodes": "OPRS/O",
}
html = http.post(ORG_URL, data=body)
print("oil_len", len(html))
print("oil_app_err", "Application Error" in html)
print("oil_results", "Organization Query Results" in html or "Query Results" in html)
pager = PAGER_RE.search(html)
print("oil_pager", pager.group(0) if pager else None)
low = html.lower()
for token in ("operator", "organization", "p-5", "oprtr", "datagrid"):
    print("has", token, token in low)
# save a slice for inspection
open(r"c:\Users\Sam Parker\Simba\Well_Navigation\deploy\org-oil-sample.html", "w", encoding="utf-8", errors="replace").write(html[:80000])
print("wrote sample")
