"""Stream rows from an .xlsx worksheet without adding a spreadsheet dependency."""

from __future__ import annotations

import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _col_index(ref: str) -> int:
    letters = "".join(ch for ch in (ref or "") if ch.isalpha())
    number = 0
    for ch in letters:
        number = number * 26 + (ord(ch.upper()) - 64)
    return max(number - 1, 0)


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    out: list[str] = []
    with archive.open("xl/sharedStrings.xml") as handle:
        for _event, el in ET.iterparse(handle, events=("end",)):
            if el.tag != f"{{{NS}}}si":
                continue
            out.append("".join(node.text or "" for node in el.iter(f"{{{NS}}}t")))
            el.clear()
    return out


def _sheet_name(archive: zipfile.ZipFile) -> str:
    names = [name for name in archive.namelist() if name.startswith("xl/worksheets/sheet")]
    if not names:
        raise FileNotFoundError("xlsx has no worksheet")
    return sorted(names)[0]


def iter_xlsx_rows(path: Path | str):
    """Yield lists of cell strings, preserving empty columns via cell refs."""
    with zipfile.ZipFile(path) as archive:
        shared = _shared_strings(archive)
        with archive.open(_sheet_name(archive)) as sheet:
            yield from _iter_sheet_rows(sheet, shared)


def _iter_sheet_rows(sheet, shared: list[str]):
    for _event, el in ET.iterparse(sheet, events=("end",)):
        if el.tag != f"{{{NS}}}row":
            continue
        values: list[str] = []
        for cell in el:
            if cell.tag != f"{{{NS}}}c":
                continue
            idx = _col_index(cell.get("r") or "")
            while len(values) <= idx:
                values.append("")
            raw = ""
            is_text = False
            for child in cell:
                if child.tag == f"{{{NS}}}v" and child.text:
                    raw = child.text
                elif child.tag == f"{{{NS}}}is":
                    raw = "".join(node.text or "" for node in child.iter(f"{{{NS}}}t"))
                    is_text = True
            if not is_text and cell.get("t") == "s" and raw.isdigit():
                raw = shared[int(raw)]
            elif cell.get("t") == "inlineStr" and not raw:
                raw = "".join(node.text or "" for node in cell.iter(f"{{{NS}}}t"))
            values[idx] = raw
        el.clear()
        yield values


def iter_xlsx_dicts(path: Path | str):
    header: list[str] | None = None
    for row in iter_xlsx_rows(path):
        if header is None:
            header = [(cell or "").strip() for cell in row]
            continue
        yield {key: (row[i] if i < len(row) else "") for i, key in enumerate(header) if key}
