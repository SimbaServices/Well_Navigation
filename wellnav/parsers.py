"""Parse RRC EWA HTML/XML responses."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from wellnav.http_client import EWA_BASE

API_HREF = re.compile(r"apiNo=(\d{8})", re.I)
LEASE_HREF = re.compile(r"leaseNo=(\d+)", re.I)
DIST_HREF = re.compile(r"(?:distCode|district)=([0-9A-Za-z]+)", re.I)
COUNTY_HREF = re.compile(r'title="County #\s*(\d+)">([^<]+)', re.I)
OPERATOR_HREF = re.compile(r'title="Operator #\s*(\d+)">([^<]+)', re.I)
FIELD_HREF = re.compile(r'title="Field #\s*(\d+)">([^<]+)', re.I)
PAGER_RE = re.compile(r"(\d+)\s*-\s*(\d+)\s+of\s+([\d,]+)\s+results", re.I)
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


def parse_wellbore_results(html: str) -> dict:
    if "Application Error" in html and "Wellbore Query Results" not in html:
        raise RuntimeError("RRC returned an application error for the wellbore query")
    pager = PAGER_RE.search(html)
    total = int(pager.group(3).replace(",", "")) if pager else 0
    start = int(pager.group(1)) if pager else 0
    end = int(pager.group(2)) if pager else 0

    wells: list[dict] = []
    seen: set[str] = set()
    doc = soup(html)
    table = doc.find("table", class_="DataGrid")
    if table is None:
        return {"wells": [], "total": total, "start": start, "end": end}

    for row in table.find_all("tr"):
        row_html = str(row)
        api_m = API_HREF.search(row_html)
        if not api_m:
            continue
        api = api_m.group(1)
        if api in seen:
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
        well_name = f"{lease_name} #{well_no}".strip(" #") if lease_name or well_no else api
        wells.append(
            {
                "api": api,
                "api_display": format_api(api),
                "well_name": well_name,
                "well_no": well_no,
                "lease_name": lease_name,
                "lease_no": lease_m.group(1) if lease_m else "",
                "district": dist_m.group(1) if dist_m else "",
                "county": unescape(county_m.group(2).strip()) if county_m else "",
                "county_code": county_m.group(1) if county_m else api[:3],
                "operator": unescape(op_m.group(2).strip()) if op_m else "",
                "operator_number": op_m.group(1) if op_m else "",
                "field": unescape(field_m.group(2).strip()) if field_m else "",
            }
        )
    if not total:
        total = len(wells)
        start, end = (1, len(wells)) if wells else (0, 0)
    return {"wells": wells, "total": total, "start": start, "end": end}


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


def normalize_api(value: str) -> tuple[str, str, str]:
    """Return (prefix, suffix, eight_digit) from user input."""
    digits = API_CLEAN.sub("", value or "")
    if digits.startswith("42") and len(digits) >= 10:
        digits = digits[2:10]
    elif len(digits) > 8:
        digits = digits[-8:]
    if len(digits) < 8:
        raise ValueError("Enter a full API number, e.g. 42-003-00290 or 00300290")
    eight = digits[:8]
    return eight[:3], eight[3:], eight


def format_api(eight: str) -> str:
    eight = API_CLEAN.sub("", eight)
    if len(eight) == 8:
        return f"42-{eight[:3]}-{eight[3:]}"
    return eight
