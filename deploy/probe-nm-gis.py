"""Probe official OCD Wells_Public from the current host."""

from __future__ import annotations

import sys

import requests

URL = (
    "https://gis.emnrd.nm.gov/arcgis/rest/services/OCDView/"
    "Wells_Public/FeatureServer/0/query"
)
AGENTS = [
    "Mozilla/5.0 (compatible; WellNavigation/1.0; +https://github.com/sparker113/Well_Navigation)",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "ArcGIS",
    "python-requests",
]
params = {
    "where": "1=1",
    "outFields": "id,name,status",
    "returnGeometry": "false",
    "resultRecordCount": "1",
    "f": "json",
}
for agent in AGENTS:
    try:
        resp = requests.get(URL, params=params, headers={"User-Agent": agent}, timeout=45)
        print(agent[:48], resp.status_code, resp.headers.get("server"), resp.text[:180].replace("\n", " "))
    except Exception as exc:
        print(agent[:48], "ERR", exc)
    sys.stdout.flush()
