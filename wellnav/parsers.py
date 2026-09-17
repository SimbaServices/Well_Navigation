"""Parse RRC EWA HTML/XML responses."""

from __future__ import annotations

import csv
import io
import re
from html import unescape
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from wellnav.http_client import EWA_BASE
from wellnav.states import PREFIX_TO_STATE, STATE_API_PREFIX

API_HREF = re.compile(r"apiNo=(\d{8})", re.I)
API_NO_PARAM = re.compile(r"api[_-]?no=(\d{8})", re.I)
API_RELATED = re.compile(r"apiRelatedLinks:(\d{8})", re.I)
API_DASHED = re.compile(r"42-(\d{3})-(\d{5})")
API_EIGHT = re.compile(r"\b(\d{8})\b")
LEASE_HREF = re.compile(r"leaseNo=(\d+)", re.I)
LEASE_ID_HREF = re.compile(r"lease[_-]?id=(\d+)", re.I)
DIST_HREF = re.compile(r"(?:distCode|district|districtCode)=([0-9A-Za-z]+)", re.I)
DIST_TEXT = re.compile(r"^[0-9]{1,2}[A-Za-z]?$")
COUNTY_HREF = re.compile(r"""title=["']County #\s*(\d+)["'][^>]*>([^<]+)""", re.I)
OPERATOR_HREF = re.compile(r"""title=["']Operator #\s*(\d+)["'][^>]*>([^<]+)""", re.I)
OPERATOR_NUM = re.compile(r"operator=(\d+)", re.I)
FIELD_HREF = re.compile(r"""title=["']Field #\s*(\d+)["'][^>]*>([^<]+)""", re.I)
PAGER_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)\s+results", re.I)
ORG_NO_HREF = re.compile(r"searchByOperatorNo&(?:amp;)?operatorNo=(\d+)", re.I)
LEASE_NAME_WELL = re.compile(
    r"<td>([^<]{1,80})</td>\s*<td>([^<]{1,12})</td>\s*<td>\s*"
    r'<a href="wellboreByFieldQueryAction',
    re.I | re.S,
)
LEASE_OPTION = re.compile(
    r"^([0-9A-Za-z]{1,2})-([OG])-(\d+)-(.+)$",
    re.I,
)
API_CLEAN = re.compile(r"\D+")
RELATED_NOISE = re.compile(r"\b(?:Links|Images|GIS Viewer|Completion)\b", re.I)
HEADER_KEYS = {
    "api no": "api",
    "api": "api",
    "district": "district",
    "lease no": "lease_no",
    "lease number": "lease_no",
    "lease name": "lease_name",
    "well no": "well_no",
    "well number": "well_no",
    "field name": "field",
    "field": "field",
    "operator name": "operator",
    "operator": "operator",
    "county": "county",
}
DEFAULT_COLUMNS = (
    "api",
    "district",
    "lease_no",
    "lease_name",
    "well_no",
    "field",
    "operator",
    "county",
)
PERMIT_HEADER_KEYS = {
    "api no.": "api",
    "api no": "api",
    "district": "district",
    "lease": "lease_name",
    "well number": "well_no",
    "permitted operator": "operator",
    "county": "county",
    "status date": "status_date",
    "status number": "permit_no",
    "wellbore profiles": "profile",
    "filing purpose": "filing_purpose",
    "amend": "amend",
    "total depth": "total_depth",
    "stacked lateral parent well dp #": "stacked_parent",
    "status": "status",
}
OPERATOR_PAREN = re.compile(r"^(.*?)\((\d+)\)\s*$")
SUBMITTED_RE = re.compile(r"Submitted:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
APPROVED_RE = re.compile(r"Approved:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
UNIVERSAL_DOC_RE = re.compile(r"universalDocNo=(\d+)", re.I)


def soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def extract_form(html: str, form_name: str | None = None) -> tuple[str, dict[str, str]]:
    doc = soup(html)
    form = None
    if form_name:
        form = doc.find("form", attrs={"name": form_name})
        if form is None:
            raise ValueError(f"RRC form {form_name!r} was not in the response")
    else:
        form = doc.find("form")
    if form is None:
        raise ValueError("No HTML form found in RRC response")
    action = form.get("action") or ""
    fields: dict[str, str] = {}
    for tag in form.find_all(["input", "select"]):
        name = tag.get("name")
        if not name:
            continue
        if tag.name == "select":
            selected = tag.find("option", selected=True) or tag.find("option")
            fields[name] = selected.get("value", "") if selected else ""
            continue
        itype = (tag.get("type") or "text").lower()
        if itype in {"button", "reset"}:
            continue
        if itype in {"checkbox", "radio"} and not tag.has_attr("checked"):
            continue
        if itype == "submit" and name in {"return", "unused"}:
            continue
        fields[name] = tag.get("value") or ""
    return urljoin(EWA_BASE + "/EWA/", action), fields


def parse_operators_xml(xml_text: str) -> list[dict]:
    doc = BeautifulSoup(xml_text, "html.parser")
    rows = []
    for node in doc.find_all("operatorvo"):
        number = node.get("oprtrnmbr_ogoprtr") or node.get("OprtrNmbr_OgOprtr") or ""
        name = node.get("orgnztnnm_eworgnztn") or node.get("OrgnztnNm_EwOrgnztn") or ""
        if number and name:
            rows.append({"number": number, "name": unescape(name)})
    return rows


def parse_fields_xml(xml_text: str) -> list[dict]:
    doc = BeautifulSoup(xml_text, "html.parser")
    rows = []
    for node in doc.find_all("fieldvo"):
        number = node.get("fldnmbr_ogfld") or node.get("FldNmbr_OgFld") or ""
        name = node.get("fldnm_ogfld") or node.get("FldNm_OgFld") or ""
        if number and name:
            rows.append({"number": number, "name": unescape(name)})
    return rows


def _norm_header(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text or "")).strip().lower().rstrip(".")


def _clean_text(text: str) -> str:
    text = unescape(text or "").replace("\xa0", " ")
    text = RELATED_NOISE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _first(*values: str) -> str:
    for value in values:
        if value:
            return value
    return ""


def _cell_visible_text(cell) -> str:
    if cell is None:
        return ""
    link = cell.find("a")
    if link:
        text = _clean_text(link.get_text(" ", strip=True))
        if text:
            return text
    return _clean_text(cell.get_text(" ", strip=True))


def _column_map(header_keys: list[str], cells: list) -> dict:
    mapping: dict = {}
    for key, cell in zip(header_keys, cells):
        if key and key not in mapping:
            mapping[key] = cell
    if "api" not in mapping:
        for key, cell in zip(DEFAULT_COLUMNS, cells):
            mapping.setdefault(key, cell)
    return mapping


def _extract_api(row_html: str, api_cell) -> str:
    blobs = [str(api_cell) if api_cell is not None else "", row_html]
    for blob in blobs:
        for pattern in (API_HREF, API_NO_PARAM, API_RELATED):
            match = pattern.search(blob)
            if match:
                return match.group(1)
    text = _cell_visible_text(api_cell)
    dashed = API_DASHED.search(text)
    if dashed:
        return dashed.group(1) + dashed.group(2)
    eight = API_EIGHT.search(text)
    if eight:
        return eight.group(1)
    return ""


def _extract_lease_no(row_html: str, cell, href_match) -> str:
    if href_match:
        return href_match.group(1)
    blob = str(cell) if cell is not None else row_html
    for pattern in (LEASE_HREF, LEASE_ID_HREF):
        match = pattern.search(blob)
        if match:
            return match.group(1)
    text = _cell_visible_text(cell)
    if re.fullmatch(r"\d{1,7}", text):
        return text
    return ""


def _extract_district(row_html: str, cell, href_match) -> str:
    if href_match:
        return href_match.group(1)
    blob = str(cell) if cell is not None else ""
    match = DIST_HREF.search(blob)
    if match:
        return match.group(1)
    text = _cell_visible_text(cell)
    if DIST_TEXT.fullmatch(text):
        return text
    return ""


def _extract_operator(row_html: str, cell, href_match) -> tuple[str, str]:
    if href_match:
        return unescape(href_match.group(2).strip()), href_match.group(1)
    target = cell
    blob = str(cell) if cell is not None else row_html
    match = OPERATOR_HREF.search(blob)
    if match:
        return unescape(match.group(2).strip()), match.group(1)
    name = ""
    number = ""
    if target is not None:
        link = target.find("a", href=re.compile(r"operator=", re.I)) or target.find(
            "a", title=re.compile(r"Operator\s*#", re.I)
        )
        if link:
            name = _clean_text(link.get_text(" ", strip=True))
            href = link.get("href") or ""
            title = link.get("title") or ""
            num_m = OPERATOR_NUM.search(href) or re.search(r"Operator #\s*(\d+)", title, re.I)
            if num_m:
                number = num_m.group(1)
    if not name:
        name = _cell_visible_text(cell)
    if not number:
        num_m = OPERATOR_NUM.search(blob) or OPERATOR_NUM.search(row_html)
        if num_m:
            number = num_m.group(1)
    return name, number


def _extract_county(row_html: str, cell, href_match, api: str) -> tuple[str, str]:
    if href_match:
        return unescape(href_match.group(2).strip()), href_match.group(1)
    blob = str(cell) if cell is not None else row_html
    match = COUNTY_HREF.search(blob)
    if match:
        return unescape(match.group(2).strip()), match.group(1)
    name = _cell_visible_text(cell)
    code = ""
    if cell is not None:
        link = cell.find("a", href=re.compile(r"county=", re.I))
        if link:
            name = name or _clean_text(link.get_text(" ", strip=True))
            href_m = re.search(r"[?&]county=(\d+)", link.get("href") or "", re.I)
            if href_m:
                code = href_m.group(1)
    return name, code or api[:3]


def parse_wellbore_results(html: str) -> dict:
    if "Application Error" in html and "Wellbore Query Results" not in html:
        raise RuntimeError("RRC returned an application error for the wellbore query")
    pager_match = PAGER_RE.search(html)
    pager = bool(pager_match)
    total = int(pager_match.group(3).replace(",", "")) if pager_match else 0
    start = int(pager_match.group(1)) if pager_match else 0
    end = int(pager_match.group(2)) if pager_match else 0

    wells: list[dict] = []
    seen: set[str] = set()
    doc = soup(html)
    table = doc.find("table", class_="DataGrid")
    empty = {"wells": [], "total": total, "start": start, "end": end, "pager": pager}
    if table is None:
        return empty

    header_keys: list[str] = list(DEFAULT_COLUMNS)
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        ths = row.find_all("th")
        if ths:
            header_keys = [HEADER_KEYS.get(_norm_header(th.get_text(" ", strip=True)), "") for th in ths]
            continue
        if row.find(class_="PagerBanner"):
            continue
        cells = row.find_all("td", recursive=False)
        if not cells:
            continue
        row_html = str(row)
        columns = _column_map(header_keys, cells)
        api = _extract_api(row_html, columns.get("api"))
        if not api or api in seen:
            continue
        seen.add(api)

        lease_m = LEASE_HREF.search(row_html)
        dist_m = DIST_HREF.search(row_html)
        county_m = COUNTY_HREF.search(row_html)
        op_m = OPERATOR_HREF.search(row_html)
        field_m = FIELD_HREF.search(row_html)
        name_m = LEASE_NAME_WELL.search(row_html)

        lease_name = unescape(name_m.group(1).strip()) if name_m else ""
        well_no = unescape(name_m.group(2).strip()) if name_m else ""
        lease_name = _first(lease_name, _cell_visible_text(columns.get("lease_name")))
        well_no = _first(well_no, _cell_visible_text(columns.get("well_no")))
        lease_no = _extract_lease_no(row_html, columns.get("lease_no"), lease_m)
        district = _extract_district(row_html, columns.get("district"), dist_m)
        operator, operator_number = _extract_operator(row_html, columns.get("operator"), op_m)
        county, county_code = _extract_county(row_html, columns.get("county"), county_m, api)
        field = _first(
            unescape(field_m.group(2).strip()) if field_m else "",
            _cell_visible_text(columns.get("field")),
        )
        well_name = f"{lease_name} #{well_no}".strip(" #") if lease_name or well_no else api
        wells.append(
            {
                "api": api,
                "api_display": format_api(api),
                "well_name": well_name,
                "well_no": well_no,
                "lease_name": lease_name,
                "lease_no": lease_no,
                "district": district,
                "county": county,
                "county_code": county_code or api[:3],
                "operator": operator,
                "operator_number": operator_number,
                "field": field,
            }
        )
    if not total:
        total = len(wells)
        start, end = (1, len(wells)) if wells else (0, 0)
    return {"wells": wells, "total": total, "start": start, "end": end, "pager": pager}


def parse_organization_results(html: str) -> dict:
    """Parse P-5 organization operator query results."""
    pager_match = PAGER_RE.search(html)
    pager = bool(pager_match)
    total = int(pager_match.group(3).replace(",", "")) if pager_match else 0
    start = int(pager_match.group(1)) if pager_match else 0
    end = int(pager_match.group(2)) if pager_match else 0
    operators: list[dict] = []
    seen: set[str] = set()
    doc = soup(html)
    table = doc.find("table", class_="DataGrid")
    if table is None:
        return {"operators": [], "total": total, "start": start, "end": end, "pager": pager}
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) < 2:
            continue
        match = ORG_NO_HREF.search(str(cells[0]))
        if not match:
            continue
        number = match.group(1)
        if number in seen:
            continue
        seen.add(number)
        texts = [_clean_text(cell.get_text(" ", strip=True)) for cell in cells]
        operators.append(
            {
                "operator_number": number,
                "operator_name": unescape(texts[1]) if len(texts) > 1 else "",
                "org_status": texts[6] if len(texts) > 6 else "",
                "org_type": texts[7] if len(texts) > 7 else "",
                "phone": texts[10] if len(texts) > 10 else "",
            }
        )
    if not total:
        total = len(operators)
        start, end = (1, len(operators)) if operators else (0, 0)
    return {
        "operators": operators,
        "total": total,
        "start": start,
        "end": end,
        "pager": pager,
    }


def parse_lease_results(html: str) -> list[dict]:
    if "Application Error" in html and "Lease" not in html:
        raise RuntimeError("RRC returned an application error for the lease query")
    doc = soup(html)
    leases: list[dict] = []
    select = doc.find("select", attrs={"name": "selectedLease"})
    if select:
        for option in select.find_all("option"):
            raw = unescape((option.get("value") or option.get_text() or "").strip())
            parsed = LEASE_OPTION.match(raw)
            if not parsed:
                continue
            district, kind, lease_no, name = parsed.groups()
            leases.append(
                {
                    "lease_no": lease_no,
                    "district": district,
                    "name": name,
                    "kind": "Oil" if kind.upper() == "O" else "Gas",
                    "token": raw,
                }
            )
        return leases

    table = doc.find("table", class_="DataGrid")
    if table is None:
        return leases
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all("td")]
        if len(cells) < 3:
            continue
        joined = " ".join(cells)
        if "Lease" in joined and "District" in joined:
            continue
        lease_no = ""
        district = ""
        name = ""
        for cell in cells:
            if re.fullmatch(r"\d{3,7}", cell) and not lease_no:
                lease_no = cell
            elif re.fullmatch(r"[0-9]{1,2}[A-Za-z]?", cell) and not district:
                district = cell
        if cells:
            name = max(cells, key=len)
        if lease_no and name and name != lease_no:
            leases.append({"lease_no": lease_no, "district": district, "name": name})
    return leases


def parse_permit_operator(text: str) -> tuple[str, str]:
    raw = _clean_text(text)
    match = OPERATOR_PAREN.match(raw)
    if match:
        return match.group(1).strip().rstrip(","), match.group(2)
    return raw, ""


def parse_permit_dates(text: str) -> tuple[str, str]:
    blob = unescape(text or "")
    submitted = SUBMITTED_RE.search(blob)
    approved = APPROVED_RE.search(blob)
    return (
        submitted.group(1) if submitted else "",
        approved.group(1) if approved else "",
    )


def rewrite_permit_page_url(href: str, *, page_size: int, offset: int = 0) -> str:
    """Build the full-results GET from a pager link, with pageSize=total and offset=0."""
    absolute = urljoin(EWA_BASE + "/", (href or "").replace("&amp;", "&"))
    parsed = urlparse(absolute)
    qs = parse_qs(parsed.query, keep_blank_values=True)
    qs["pager.pageSize"] = [str(page_size)]
    qs["pager.offset"] = [str(offset)]
    qs["methodToCall"] = ["search"]

    def _quote(value: str, safe: str, encoding: str | None = None, errors: str | None = None) -> str:
        return quote(value, safe="|=", encoding=encoding, errors=errors)

    return urlunparse(
        parsed._replace(
            scheme="https",
            netloc="webapps2.rrc.texas.gov",
            query=urlencode(qs, doseq=True, quote_via=_quote),
        )
    )


def parse_drilling_permit_results(html: str) -> dict:
    """Parse a Drilling Permit (W-1) Query results page."""
    if "Application Error" in html and "Drilling Permit" not in html:
        raise RuntimeError("RRC returned an application error for the drilling permit query")
    pager_match = PAGER_RE.search(html)
    pager = bool(pager_match)
    total = int(pager_match.group(3).replace(",", "")) if pager_match else 0
    start = int(pager_match.group(1)) if pager_match else 0
    end = int(pager_match.group(2)) if pager_match else 0

    doc = soup(html)
    pager_href = ""
    for link in doc.find_all("a", href=True):
        href = link.get("href") or ""
        if (
            "pager.pageSize=" in href
            and "rrcActionMan=" in href
            and "drillingPermitsQueryAction" in href
        ):
            pager_href = href
            break

    permits: list[dict] = []
    seen: set[tuple[str, str]] = set()
    table = doc.find("table", class_="DataGrid")
    empty = {
        "permits": [],
        "total": total,
        "start": start,
        "end": end,
        "pager": pager,
        "pager_href": pager_href,
    }
    if table is None:
        return empty

    header_keys: list[str] = []
    for row in table.find_all("tr"):
        if row.find_parent("table") is not table:
            continue
        ths = row.find_all("th")
        if ths:
            header_keys = [
                PERMIT_HEADER_KEYS.get(_norm_header(th.get_text(" ", strip=True)), "")
                for th in ths
            ]
            continue
        if row.find(class_="PagerBanner"):
            continue
        cells = row.find_all("td", recursive=False)
        if not cells or not header_keys:
            continue
        row_html = str(row)
        columns = {key: cell for key, cell in zip(header_keys, cells) if key}
        api = _extract_api(row_html, columns.get("api"))
        if not api:
            continue
        permit_no = _cell_visible_text(columns.get("permit_no"))
        key = (api, permit_no)
        if key in seen:
            continue
        seen.add(key)
        submitted, approved = parse_permit_dates(
            columns["status_date"].get_text(" ", strip=True) if columns.get("status_date") else ""
        )
        operator, operator_number = parse_permit_operator(
            _cell_visible_text(columns.get("operator"))
        )
        doc_m = UNIVERSAL_DOC_RE.search(row_html)
        permits.append(
            {
                "api": api,
                "permit_no": permit_no,
                "universal_doc_no": doc_m.group(1) if doc_m else "",
                "district": _cell_visible_text(columns.get("district")),
                "lease_name": _cell_visible_text(columns.get("lease_name")),
                "well_no": _cell_visible_text(columns.get("well_no")),
                "operator": operator,
                "operator_number": operator_number,
                "county": _cell_visible_text(columns.get("county")),
                "county_code": api[:3],
                "submitted_at": submitted,
                "approved_at": approved,
                "profile": _cell_visible_text(columns.get("profile")),
                "filing_purpose": _cell_visible_text(columns.get("filing_purpose")),
                "status": _cell_visible_text(columns.get("status")),
            }
        )
    if not total:
        total = len(permits)
        start, end = (1, len(permits)) if permits else (0, 0)
    return {
        "permits": permits,
        "total": total,
        "start": start,
        "end": end,
        "pager": pager,
        "pager_href": pager_href,
    }


def normalize_api(value: str) -> tuple[str, str, str]:
    """Return (prefix, suffix, eight_digit) from user input."""
    digits = API_CLEAN.sub("", value or "")
    if len(digits) >= 10 and digits[:2] in PREFIX_TO_STATE:
        digits = digits[2:10]
    elif digits.startswith("42") and len(digits) >= 10:
        digits = digits[2:10]
    elif len(digits) > 8:
        digits = digits[-8:]
    if len(digits) < 8:
        raise ValueError("Enter a full API number, e.g. 42-003-00290 or 00300290")
    eight = digits[:8]
    return eight[:3], eight[3:], eight


def format_api(value: str, state: str | None = None) -> str:
    digits = API_CLEAN.sub("", value or "")
    if len(digits) >= 10:
        return f"{digits[:2]}-{digits[2:5]}-{digits[5:10]}"
    if len(digits) == 8:
        prefix = STATE_API_PREFIX.get((state or "tx").lower(), "42")
        return f"{prefix}-{digits[:3]}-{digits[3:]}"
    return value or ""


def parse_wellbore_csv(text: str) -> dict:
    """Parse the EWA wellbore criteria CSV download."""
    if any(token in text for token in ("Ewa_123", "Application Error", "exceeds the maximum")):
        if "API No." not in text:
            return {"wells": [], "over_limit": True, "no_results": False}
    start = None
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "API No." in line and "Operator Name" in line:
            start = i
            break
    if start is None:
        return {"wells": [], "over_limit": False, "no_results": True}
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    wells: list[dict] = []
    for row in reader:
        raw_api = (row.get("API No.") or "").strip()
        digits = API_CLEAN.sub("", raw_api)
        if digits.startswith("42") and len(digits) >= 10:
            digits = digits[2:10]
        elif len(digits) > 8:
            digits = digits[-8:]
        if len(digits) != 8:
            continue
        lease_name = (row.get("Lease Name") or "").strip()
        well_no = (row.get("Well No.") or "").strip()
        wells.append(
            {
                "api": digits,
                "district": (row.get("District") or "").strip(),
                "lease_no": (row.get("Lease No.") or "").strip(),
                "lease_name": lease_name,
                "well_no": well_no,
                "field": (row.get("Field Name") or "").strip(),
                "operator": (row.get("Operator Name") or "").strip(),
                "county": (row.get("County") or "").strip(),
                "well_name": f"{lease_name} #{well_no}".strip(" #") if lease_name or well_no else "",
            }
        )
    return {"wells": wells, "over_limit": False, "no_results": not wells}
