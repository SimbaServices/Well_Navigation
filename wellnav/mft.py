"""GoAnywhere public share client for mft.rrc.texas.gov."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup

from wellnav.http_client import USER_AGENT

DATASETS_URL = "https://www.rrc.texas.gov/resource-center/research/data-sets-available-for-download/"
MFT_ORIGIN = "https://mft.rrc.texas.gov"
PIPELINE_SHARE_FALLBACK = "https://mft.rrc.texas.gov/link/c7cbab0c-afe2-4f6f-91ae-e6ed7d3a7ab6"
DOWNLOAD_PATH = "/link/godrivedownload"


@dataclass(frozen=True)
class ShareFile:
    name: str
    index: int
    link_id: str
    row_key: str
    size_label: str
    size_bytes: int | None


def parse_size_label(label: str) -> int | None:
    match = re.match(r"^\s*([\d.]+)\s*(B|KB|MB|GB)\s*$", label or "", re.I)
    if not match:
        return None
    amount = float(match.group(1))
    unit = match.group(2).upper()
    factor = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}[unit]
    return int(amount * factor)


def parse_share_listing(html: str) -> tuple[list[ShareFile], dict]:
    """Parse a Public GoDrive HTML page into files plus form bookkeeping."""
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id="fileList")
    if form is None:
        raise RuntimeError("GoAnywhere listing is missing the fileList form")
    action = form.get("action") or "/webclient/godrive/PublicGoDrive.xhtml"
    viewstate_el = form.find("input", attrs={"name": "javax.faces.ViewState"})
    viewstate = viewstate_el.get("value") if viewstate_el else ""
    files: list[ShareFile] = []
    for row in soup.select("tr.FileItem"):
        name_el = row.select_one(".NameColumn a")
        size_el = row.select_one(".SizeColumn")
        if name_el is None:
            continue
        name = name_el.get_text(strip=True)
        link_id = name_el.get("id") or ""
        if not name or not link_id:
            continue
        size_label = size_el.get_text(strip=True) if size_el else ""
        files.append(
            ShareFile(
                name=name,
                index=int(row.get("data-ri") or len(files)),
                link_id=link_id,
                row_key=str(row.get("data-rk") or ""),
                size_label=size_label,
                size_bytes=parse_size_label(size_label),
            )
        )
    total = _listing_total(html, default=len(files))
    return files, {"action": action, "viewstate": viewstate, "total": total}


def _listing_total(html: str, default: int) -> int:
    match = re.search(r"rowCount:(\d+)", html)
    if match:
        return int(match.group(1))
    match = re.search(r"Showing\s+\d+\s+-\s+\d+\s+of\s+(\d+)", html)
    if match:
        return int(match.group(1))
    return default


def discover_pipeline_share_url(timeout: int = 45) -> str:
    """Read the current Pipeline Layers by County GoAnywhere share from RRC."""
    try:
        response = requests.get(
            DATASETS_URL,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.RequestException:
        return PIPELINE_SHARE_FALLBACK
    soup = BeautifulSoup(response.text, "html.parser")
    for row in soup.find_all("tr"):
        heading = row.find(["th", "td"])
        if heading is None or "Pipeline Layers by County" not in heading.get_text(" ", strip=True):
            continue
        for link in row.find_all("a", href=True):
            href = link["href"].strip()
            if "/link/" in href:
                return href
    match = re.search(
        r"Pipeline Layers by County.{0,400}?(https://mft\.rrc\.texas\.gov/link/[a-f0-9-]+)",
        response.text,
        re.I | re.S,
    )
    return match.group(1) if match else PIPELINE_SHARE_FALLBACK


class GoAnywhereShare:
    """Session against a public GoAnywhere GoDrive folder."""

    def __init__(self, share_url: str) -> None:
        self.share_url = share_url
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            }
        )
        self._action = urljoin(MFT_ORIGIN, "/webclient/godrive/PublicGoDrive.xhtml")
        self._viewstate = ""
        self._files: list[ShareFile] = []
        self._last_html = ""

    def open(self, rows: int | None = 1000) -> list[ShareFile]:
        response = self.session.get(self.share_url, timeout=60)
        response.raise_for_status()
        files = self._ingest_html(response.text)
        if rows:
            total = max(len(files), _listing_total(self._last_html, default=len(files)))
            want = max(int(rows), total)
            if len(files) < want:
                paged = self._request_page(first=0, rows=want)
                if paged:
                    files = paged
                    self._files = files
        return files

    def list_all(self) -> list[ShareFile]:
        files = self.open(rows=1000)
        total = max(len(files), _listing_total(self._last_html, default=len(files)))
        if len(files) < total:
            seen = {item.name: item for item in files}
            first = len(files)
            while first < total:
                page = self._request_page(first=first, rows=250)
                if not page:
                    break
                for item in page:
                    seen[item.name] = item
                if len(page) < 250:
                    break
                first += len(page)
            files = sorted(seen.values(), key=lambda item: item.name)
        self._files = files
        return files

    def file_named(self, name: str) -> ShareFile | None:
        for item in self._files:
            if item.name == name:
                return item
        return None

    def download(self, item: ShareFile, dest: Path, timeout: int = 180) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not self._viewstate:
            self.open()
        data = {
            "fileTable_selection": "",
            "fileList_SUBMIT": "1",
            "javax.faces.ViewState": self._viewstate,
            item.link_id: item.link_id,
        }
        posted = self.session.post(
            self._action,
            data=data,
            timeout=timeout,
            allow_redirects=False,
            headers={"Referer": self.share_url, "Origin": MFT_ORIGIN},
        )
        if posted.status_code not in {301, 302, 303, 307, 308}:
            raise RuntimeError(
                f"Unexpected download status {posted.status_code} for {item.name}"
            )
        location = posted.headers.get("Location") or DOWNLOAD_PATH
        payload = self.session.get(
            urljoin(MFT_ORIGIN, location),
            timeout=timeout,
            stream=True,
            headers={"Referer": self.share_url},
        )
        payload.raise_for_status()
        ctype = payload.headers.get("content-type", "")
        if "html" in ctype.lower():
            raise RuntimeError(f"Download of {item.name} returned HTML instead of a zip")
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as handle:
            for chunk in payload.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
        tmp.replace(dest)
        return dest

    def _ingest_html(self, html: str) -> list[ShareFile]:
        self._last_html = html
        files, meta = parse_share_listing(html)
        self._action = urljoin(MFT_ORIGIN, meta["action"])
        self._viewstate = meta["viewstate"]
        self._files = files
        return files

    def _request_page(self, first: int, rows: int) -> list[ShareFile]:
        data = {
            "javax.faces.partial.ajax": "true",
            "javax.faces.source": "fileTable",
            "javax.faces.partial.execute": "fileTable",
            "javax.faces.partial.render": "fileTable",
            "javax.faces.behavior.event": "page",
            "fileTable_pagination": "true",
            "fileTable_skipChildren": "true",
            "fileTable_encodeFeature": "true",
            "fileTable_first": str(first),
            "fileTable_rows": str(rows),
            "fileTable_rppDD": str(rows),
            "fileTable_selection": "",
            "fileList_SUBMIT": "1",
            "javax.faces.ViewState": self._viewstate,
        }
        response = self.session.post(
            self._action,
            data=data,
            timeout=60,
            headers={
                "Faces-Request": "partial/ajax",
                "Referer": self.share_url,
                "Origin": MFT_ORIGIN,
            },
        )
        response.raise_for_status()
        html, viewstate = _partial_table_html(response.text)
        if viewstate:
            self._viewstate = viewstate
        if not html:
            return []
        files, _ = parse_share_listing(_wrap_table_html(html, self._viewstate))
        return files


def _partial_table_html(xml_text: str) -> tuple[str, str]:
    html = ""
    viewstate = ""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return html, viewstate
    for update in root.findall(".//{*}update"):
        ident = update.get("id") or ""
        text = "".join(update.itertext())
        if ident == "fileTable" or ident.endswith("fileTable"):
            html = text
        if "javax.faces.ViewState" in ident:
            viewstate = text.strip()
    if not html:
        match = re.search(
            r'<update id="[^"]*fileTable[^"]*"><!\[CDATA\[(.*?)\]\]></update>',
            xml_text,
            re.S,
        )
        if match:
            html = match.group(1)
    return html, viewstate


def _wrap_table_html(table_html: str, viewstate: str) -> str:
    if "<form" in table_html:
        return table_html
    return (
        '<form id="fileList" action="/webclient/godrive/PublicGoDrive.xhtml" method="post">'
        f'{table_html}'
        '<input name="fileList_SUBMIT" type="hidden" value="1"/>'
        f'<input name="javax.faces.ViewState" type="hidden" value="{viewstate}"/>'
        "</form>"
    )
