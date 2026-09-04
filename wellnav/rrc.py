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
    parse_fields_xml,
    parse_lease_results,
    parse_operators_xml,
    parse_wellbore_results,
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
LEASE_FORM_URL = (
    f"{EWA_BASE}/EWA/specificLeaseQueryAction.do"
    "?methodToCall=toLeaseQuery&searchType=specificLease"
)


class RrcClient:
    def __init__(self) -> None:
        self.http = CurlSession()
        self._lock = Lock()
        self._ready = False

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
    if results_page:
        try:
            parsed = parse_wellbore_results(html)
        except RuntimeError:
            parsed = {"wells": [], "total": 0, "start": 0, "end": 0}
        wells = list(parsed.get("wells") or [])
        total = int(parsed.get("total") or 0)
        start = int(parsed.get("start") or 0)
        end = int(parsed.get("end") or 0)
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
        "page_size": page_size,
        "offset": offset,
        "over_limit": over_limit,
        "no_results": no_results,
    }


CLIENT = RrcClient()
