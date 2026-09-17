"""Render a live search page fragment and confirm the Status column."""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from wellnav.db import ROOT
from wellnav.repository import WellRepository

repo = WellRepository()
tx = repo.search(name="ALPHA", state="tx", include_permits=False, page_size=5)
nm = repo.search(q="DUSTIN", state="nm", include_permits=False, page_size=5)
if not tx["wells"]:
    raise SystemExit("FAIL no texas wells")
if not nm["wells"]:
    raise SystemExit("FAIL no nm wells")
print("tx_status", tx["wells"][0]["well_name"], tx["wells"][0]["status_label"])
print("nm_status", nm["wells"][0]["well_name"], nm["wells"][0]["status_label"])

env = Environment(loader=FileSystemLoader(str(ROOT / "templates")), autoescape=True)
env.globals.update(
    filter_href=lambda *args, **kwargs: "/search",
    sort_href=lambda *args, **kwargs: "/search?sort=status",
    well_href=lambda well: "/well",
    filter_query=lambda *args, **kwargs: "",
)
html = env.get_template("partials/wells.html").render(
    wells=tx["wells"],
    filters={},
    start=1,
    end=len(tx["wells"]),
    total=tx["total"],
    page_size=5,
    offset=0,
    mode="name",
    state="tx",
    sort="name",
    dir="asc",
)
if 'data-sort="status"' not in html or "col-status" not in html:
    raise SystemExit("FAIL status column missing from HTML")
if tx["wells"][0]["status_label"] not in html:
    raise SystemExit(f"FAIL label missing {tx['wells'][0]['status_label']!r}")
print("OK column")
repo.close()
