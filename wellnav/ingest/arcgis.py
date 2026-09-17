"""Paginated public ArcGIS Feature/Map Server queries."""

from __future__ import annotations

import time

import requests

USER_AGENT = (
    "Mozilla/5.0 (compatible; WellNavigation/1.0; +https://github.com/sparker113/Well_Navigation)"
)


def query_page(
    url: str,
    *,
    where: str = "1=1",
    offset: int = 0,
    page_size: int = 2000,
    out_fields: str = "*",
    return_geometry: bool = True,
) -> dict:
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
        "f": "json",
    }
    last_error: Exception | None = None
    for attempt in range(5):
        try:
            resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=90)
            if resp.status_code in {403, 408, 429, 500, 502, 503, 504}:
                raise RuntimeError(f"HTTP {resp.status_code}")
            resp.raise_for_status()
            payload = resp.json()
            if payload.get("error"):
                raise RuntimeError(payload["error"])
            return payload
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(last_error)


def iter_features(
    url: str,
    *,
    where: str = "1=1",
    page_size: int = 2000,
    delay: float = 0.12,
    limit: int = 0,
    out_fields: str = "*",
    return_geometry: bool = True,
):
    offset = 0
    seen = 0
    while True:
        payload = query_page(
            url,
            where=where,
            offset=offset,
            page_size=page_size,
            out_fields=out_fields,
            return_geometry=return_geometry,
        )
        features = payload.get("features") or []
        if not features:
            break
        for feature in features:
            yield feature
            seen += 1
            if limit and seen >= limit:
                return
        if not payload.get("exceededTransferLimit") and len(features) < page_size:
            break
        offset += len(features)
        if delay:
            time.sleep(delay)


def iter_envelope(
    url: str,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    page_size: int = 1000,
    delay: float = 0.12,
    limit: int = 0,
):
    offset = 0
    seen = 0
    while True:
        payload = query_envelope(
            url,
            west=west,
            south=south,
            east=east,
            north=north,
            offset=offset,
            page_size=page_size,
        )
        features = payload.get("features") or []
        if not features:
            break
        for feature in features:
            yield feature
            seen += 1
            if limit and seen >= limit:
                return
        if not payload.get("exceededTransferLimit") and len(features) < page_size:
            break
        offset += len(features)
        if delay:
            time.sleep(delay)


def query_envelope(
    url: str,
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    offset: int = 0,
    page_size: int = 1000,
) -> dict:
    params = {
        "where": "1=1",
        "geometry": f"{west},{south},{east},{north}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
        "f": "json",
    }
    resp = requests.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=90)
    resp.raise_for_status()
    payload = resp.json()
    if payload.get("error"):
        raise RuntimeError(payload["error"])
    return payload
