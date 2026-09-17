"""Texas Railroad Commission EWA queries: operators, leases, wellbores."""

from __future__ import annotations

import re
import time
from threading import Lock
from urllib.parse import quote

from wellnav.http_client import EWA_BASE, CurlSession
from wellnav.parsers import (
    extract_form,
    normalize_api,
    parse_drilling_permit_results,
    parse_fields_xml,
    parse_lease_results,
    parse_organization_results,
    parse_operators_xml,
    parse_wellbore_csv,
    parse_wellbore_results,
    rewrite_permit_page_url,
)

RESULT_CAP = 10000
OVER_LIMIT_RE = re.compile(r"(\d+)\s+records found which exceeds the maximum", re.I)

OPERATOR_SEARCH = (
    f"{EWA_BASE}/EWA/operatorQueryAction.do"
    "?methodToCall=searchByName&name={name}&wildcard={wildcard}"
    "&searchType=&uniqIdx={uniq}&ajaxRef=OperatorQueryFunctions/SearchByName"
)
FIELD_SEARCH = (
    f"{EWA_BASE}/EWA/fieldQueryAction.do"
    "?methodToCall=searchByName&name={name}&wildcard={wildcard}"
    "&searchType=&uniqIdx={uniq}&ajaxRef=FieldQueryFunctions/SearchByName"
)
WELLBORE_URL = f"{EWA_BASE}/EWA/wellboreQueryAction.do"
ORG_URL = f"{EWA_BASE}/EWA/organizationQueryAction.do"
PERMITS_URL = f"{EWA_BASE}/EWA/drillingPermitsQueryAction.do"
LEASE_FORM_URL = (
    f"{EWA_BASE}/EWA/specificLeaseQueryAction.do"
    "?methodToCall=toLeaseQuery&searchType=specificLease"
)


class RrcClient:
    def __init__(self) -> None:
        self.http = CurlSession()
        self._lock = Lock()
        self._ready = False
        self._org_ready = False

    def _ensure_session(self) -> None:
        if self._ready:
            return
        self.http.get(WELLBORE_URL)
        self._ready = True

    def search_operators(self, name: str, wildcard: str = "beginsWith") -> list[dict]:
        q = (name or "").strip()
        if len(q) < 2:
            return []
        with self._lock:
            self._ensure_session()
            url = OPERATOR_SEARCH.format(
                name=quote(q), wildcard=wildcard, uniq=int(time.time() * 1000)
            )
            xml = self.http.request("POST", url)
        return parse_operators_xml(xml)

    def search_fields(self, name: str, wildcard: str = "beginsWith") -> list[dict]:
        q = (name or "").strip()
        if len(q) < 2:
            return []
        with self._lock:
            self._ensure_session()
            url = FIELD_SEARCH.format(
                name=quote(q), wildcard=wildcard, uniq=int(time.time() * 1000)
            )
            xml = self.http.request("POST", url)
        return parse_fields_xml(xml)

    def search_leases(self, name: str, rule: str = "contains") -> list[dict]:
        q = (name or "").strip()
        if len(q) < 3:
            return []
        with self._lock:
            self._ensure_session()
            form_html = self.http.get(LEASE_FORM_URL)
            action, fields = extract_form(form_html, "searchForLeaseQueryActionForm")
            fields["methodToCall"] = "search"
            fields["searchType"] = "specificLease"
            fields["searchArgs.leaseNameArg"] = q[:20]
            fields["searchRule"] = rule
            fields["searchArgs.districtCodeArg"] = "None Selected"
            fields["searchArgs.onShoreCountyCodeArg"] = "None Selected"
            fields["searchArgs.offShoreCountyCodeArg"] = "None Selected"
            fields["submit"] = "Submit"
            html = self.http.post(action, data=fields)
        if "Application Error" in html:
            raise RuntimeError("RRC lease name search failed. Try API or operator search.")
        return parse_lease_results(html)

    def search_organizations(
        self,
        *,
        specialty: str,
        page_size: int = 100,
        offset: int = 0,
    ) -> dict:
        data = {
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
            "searchArgs.activitySpecialityCodes": specialty,
            "pager.pageSize": str(page_size),
            "pager.offset": str(offset),
        }
        with self._lock:
            self._ensure_session()
            if not self._org_ready:
                self.http.get(ORG_URL)
                self._org_ready = True
            html = self.http.post(ORG_URL, data=data)
        parsed = parse_organization_results(html)
        parsed["page_size"] = page_size
        parsed["offset"] = offset
        parsed["specialty"] = specialty
        return parsed

    def search_wellbores(
        self,
        *,
        api: str | None = None,
        operator_numbers: list[str] | None = None,
        operator_names: str = "",
        lease_no: str = "",
        district: str = "",
        lease_type: str = "",
        well_type: str = "",
        county_code: str = "",
        field_numbers: list[str] | None = None,
        schedule: str = "Y",
        page_size: int = 50,
        offset: int = 0,
        api_prefix: str = "",
        api_suffix: str = "",
    ) -> dict:
        # lease_type radios are O / G / empty — not "Oil" / "Gas".
        data = {
            "methodToCall": "search",
            "searchArgs.fieldNumbersArg": ",".join(field_numbers or []),
            "searchArgs.operatorNumbersArg": ",".join(operator_numbers or []),
            "searchArgs.leaseTypeArg": lease_type,
            "searchArgs.districtCodeArg": district or "None Selected",
            "searchArgs.leaseNumberArg": lease_no,
            "searchArgs.wellTypeArg": well_type or "None Selected",
            "searchArgs.countyCodeArg": county_code or "None Selected",
            "operatorNames": operator_names,
            "searchArgs.drillingPermitArg": "",
            "searchArgs.apiNoPrefixArg": api_prefix,
            "searchArgs.apiNoSuffixArg": api_suffix,
            "searchArgs.scheduleTypeArg": schedule,
            "pager.pageSize": str(page_size),
            "pager.offset": str(offset),
        }
        if api:
            prefix, suffix, _ = normalize_api(api)
            data["searchArgs.apiNoPrefixArg"] = prefix
            data["searchArgs.apiNoSuffixArg"] = suffix
        with self._lock:
            self._ensure_session()
            html = self.http.post(WELLBORE_URL, data=data)
        return annotate_wellbore_page(html, page_size=page_size, offset=offset)

    def download_wellbore_csv(
        self,
        *,
        county_code: str,
        schedule: str,
        lease_type: str = "",
        well_type: str = "",
        district: str = "",
        api_prefix: str = "",
    ) -> dict:
        """One-shot wellbore dump. Bypasses the 10k HTML result cap."""
        data = {
            "methodToCall": "generateWellboreCriteriaReportCsv",
            "searchArgs.fieldNumbersArg": "",
            "searchArgs.operatorNumbersArg": "",
            "searchArgs.leaseTypeArg": lease_type,
            "searchArgs.districtCodeArg": district or "None Selected",
            "searchArgs.leaseNumberArg": "",
            "searchArgs.wellTypeArg": well_type or "None Selected",
            "searchArgs.countyCodeArg": county_code or "None Selected",
            "operatorNames": "",
            "searchArgs.drillingPermitArg": "",
            "searchArgs.apiNoPrefixArg": api_prefix,
            "searchArgs.apiNoSuffixArg": "",
            "searchArgs.scheduleTypeArg": schedule,
            "pager.pageSize": "-1",
            "pager.offset": "0",
        }
        with self._lock:
            self._ensure_session()
            text = self.http.post(WELLBORE_URL, data=data, timeout=180)
        parsed = parse_wellbore_csv(text)
        parsed["schedule"] = schedule
        parsed["county_code"] = county_code
        return parsed

    def search_drilling_permits(self, *, approved_from: str, approved_to: str) -> dict:
        """Two-request W-1 pull: search by approved-date window, then fetch all rows.

        Request 1 POSTs the dated search (RRC returns a 10-row page plus
        "1 - 10 of N results"). Request 2 GETs the same search with
        pager.pageSize=N and pager.offset=0 so every row comes back once.
        """
        http = CurlSession()
        first = http.post(PERMITS_URL, data=drilling_permit_search_data(approved_from, approved_to))
        parsed = parse_drilling_permit_results(first)
        total = int(parsed.get("total") or 0)
        end = int(parsed.get("end") or 0)
        if total <= 0:
            parsed["page_size"] = 0
            parsed["offset"] = 0
            parsed["approved_from"] = approved_from
            parsed["approved_to"] = approved_to
            return parsed
        if end >= total:
            parsed["page_size"] = max(end, len(parsed.get("permits") or []))
            parsed["offset"] = 0
            parsed["approved_from"] = approved_from
            parsed["approved_to"] = approved_to
            return parsed
        if not parsed.get("pager_href"):
            raise RuntimeError(
                f"RRC permit search reported {total} results but no pager link to fetch them"
            )
        url = rewrite_permit_page_url(parsed["pager_href"], page_size=total, offset=0)
        second = http.get(url, timeout=180)
        parsed = parse_drilling_permit_results(second)
        parsed["page_size"] = total
        parsed["offset"] = 0
        parsed["approved_from"] = approved_from
        parsed["approved_to"] = approved_to
        return parsed


def drilling_permit_search_data(approved_from: str, approved_to: str) -> dict[str, str]:
    """Form body for the initial W-1 search. Dates are MM/DD/YYYY."""
    return {
        "methodToCall": "search",
        "searchArgs.permitStatusNoHndlr.inputValue": "",
        "searchArgs.apiNoHndlr.inputValue": "",
        "searchArgs.npzFlagHndlr.inputValue": "",
        "searchArgs.offLeaseSurfLocFlagHndlr.inputValue": "",
        "searchArgs.offLeasePntrnPtFlagHndlr.inputValue": "",
        "searchArgs.operatorNameWildcardHndlr.inputValue": "beginsWith",
        "searchArgs.operatorNameHndlr.inputValue": "",
        "searchArgs.operatorNoHndlr.inputValue": "",
        "searchArgs.leaseNameWildcardHndlr.inputValue": "beginsWith",
        "searchArgs.leaseNameHndlr.inputValue": "",
        "searchArgs.leaseNoHndlr.inputValue": "",
        "searchArgs.wellNoHndlr.inputValue": "",
        "searchArgs.fieldNameWildcardHndlr.inputValue": "beginsWith",
        "searchArgs.fieldNameHndlr.inputValue": "",
        "searchArgs.fieldNoHndlr.inputValue": "",
        "searchArgs.surveyNameWildcardHndlr.inputValue": "beginsWith",
        "searchArgs.surveyNameHndlr.inputValue": "",
        "searchArgs.wellTypeCodeHndlr.inputValue": "",
        "searchArgs.totalDepthHndlr.inputValue": "",
        "searchArgs.filingPurposeCodeHndlr.inputValue": "",
        "searchArgs.ammendmentFlagHndlr.inputValue": "",
        "searchArgs.statusCodeHndlr.inputValue": "",
        "searchArgs.wellboreProfileCodeHndlr.inputValue": "",
        "searchArgs.wellLocationCodeHndlr.inputValue": "",
        "searchArgs.completionStatusCodeHndlr.inputValue": "",
        "searchArgs.psaFlagHndlr.inputValue": "",
        "searchArgs.allocationFlagHndlr.inputValue": "",
        "searchArgs.stackedLateralFlagHndlr.inputValue": "",
        "searchArgs.approvedDtFromHndlr.inputValue": approved_from,
        "searchArgs.approvedDtToHndlr.inputValue": approved_to,
        "searchArgs.submittedDtFromHndlr.inputValue": "",
        "searchArgs.submittedDtToHndlr.inputValue": "",
    }


def annotate_wellbore_page(html: str, *, page_size: int, offset: int) -> dict:
    """Parse a wellbore response and flag cap / empty-form outcomes.

    Oversized searches return the query form with Ewa_123 (or Application Error)
    instead of a results table. Those must not be treated as 0-row success.
    """
    exceed = OVER_LIMIT_RE.search(html)
    app_err = "Application Error" in html and "Wellbore Query Results" not in html
    results_page = "Wellbore Query Results" in html
    wells: list[dict] = []
    total = 0
    start = 0
    end = 0
    pager = False
    if results_page:
        try:
            parsed = parse_wellbore_results(html)
        except RuntimeError:
            parsed = {"wells": [], "total": 0, "start": 0, "end": 0, "pager": False}
        wells = list(parsed.get("wells") or [])
        total = int(parsed.get("total") or 0)
        start = int(parsed.get("start") or 0)
        end = int(parsed.get("end") or 0)
        pager = bool(parsed.get("pager"))
    over_limit = bool(app_err or exceed or total >= RESULT_CAP)
    if exceed:
        total = max(total, int(exceed.group(1)))
    elif app_err and total < RESULT_CAP:
        total = RESULT_CAP
    no_results = (not over_limit) and (
        "Ewa_117" in html or (not results_page and not wells)
    )
    return {
        "wells": wells,
        "total": total,
        "start": start,
        "end": end,
        "pager": pager,
        "page_size": page_size,
        "offset": offset,
        "over_limit": over_limit,
        "no_results": no_results,
    }


CLIENT = RrcClient()
