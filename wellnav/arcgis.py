"""Paced, pageable ArcGIS REST queries for GIS ingest workers."""

from __future__ import annotations

from wellnav.http_client import BLOCKED_STATUS, BlockedRequest, gis_get_json
from wellnav.ingest.limiter import acquire, is_active


def _transient(err: object) -> bool:
    if isinstance(err, dict):
        code = err.get("code")
        msg = f"{err.get('message') or ''} {err.get('details') or ''}"
    else:
        code = None
        msg = str(err)
    if code in BLOCKED_STATUS:
        return True
    lower = msg.lower()
    return any(
        token in lower
        for token in (
            "timeout",
            "timed out",
            "too many",
            "unavailable",
            "rate limit",
            "throttl",
            "busy",
            "try again",
            "temporarily",
            "service error",
            "invalid sql",
        )
    )


def query_page(
    url: str,
    where: str,
    *,
    offset: int = 0,
    page_size: int = 1000,
    order_by: str = "OBJECTID",
    out_fields: str = "*",
    return_geometry: bool = True,
    out_sr: int = 4326,
    geometry: str | None = None,
    geometry_type: str = "esriGeometryEnvelope",
    in_sr: int = 4326,
) -> dict:
    acquire()
    params = {
        "where": where or "1=1",
        "outFields": out_fields,
        "returnGeometry": "true" if return_geometry else "false",
        "outSR": str(out_sr),
        "orderByFields": order_by,
        "resultOffset": str(int(offset)),
        "resultRecordCount": str(int(page_size)),
        "f": "json",
    }
    if geometry:
        params["geometry"] = geometry
        params["geometryType"] = geometry_type
        params["inSR"] = str(in_sr)
        params["spatialRel"] = "esriSpatialRelIntersects"
    payload = gis_get_json(url, params)
    err = payload.get("error")
    if err:
        if _transient(err):
            raise BlockedRequest(str(err), retry_after=8)
        raise RuntimeError(err)
    return payload


def count_features(url: str, where: str) -> int:
    acquire()
    payload = gis_get_json(
        url,
        {
            "where": where or "1=1",
            "returnCountOnly": "true",
            "f": "json",
        },
    )
    err = payload.get("error")
    if err:
        if _transient(err):
            raise BlockedRequest(str(err), retry_after=8)
        raise RuntimeError(err)
    return int(payload.get("count") or 0)


def fetch_pages(
    url: str,
    where: str,
    *,
    start_offset: int = 0,
    page_size: int = 1000,
    order_by: str = "OBJECTID",
    out_fields: str = "*",
    return_geometry: bool = True,
    delay: float = 0.0,
    max_features: int | None = None,
) -> dict:
    """Pull pages until complete. On a block, return what we have plus the resume offset."""
    import time

    offset = int(start_offset or 0)
    features: list[dict] = []
    while True:
        if max_features is not None and len(features) >= max_features:
            return {
                "features": features[:max_features],
                "complete": False,
                "blocked": False,
                "offset": offset,
                "error": None,
                "retry_after": None,
            }
        try:
            payload = query_page(
                url,
                where,
                offset=offset,
                page_size=page_size,
                order_by=order_by,
                out_fields=out_fields,
                return_geometry=return_geometry,
            )
        except BlockedRequest as exc:
            return {
                "features": features,
                "complete": False,
                "blocked": True,
                "offset": offset,
                "error": str(exc),
                "retry_after": exc.retry_after,
            }
        page = payload.get("features") or []
        features.extend(page)
        if not page or not payload.get("exceededTransferLimit"):
            return {
                "features": features,
                "complete": True,
                "blocked": False,
                "offset": offset + len(page),
                "error": None,
                "retry_after": None,
            }
        offset += len(page)
        if delay and not is_active():
            time.sleep(delay)


def iter_pages(
    url: str,
    where: str,
    *,
    start_offset: int = 0,
    page_size: int = 1000,
    order_by: str = "OBJECTID",
    out_fields: str = "*",
    return_geometry: bool = True,
):
    """Yield one page at a time so workers can persist without holding the county in RAM."""
    offset = int(start_offset or 0)
    while True:
        payload = query_page(
            url,
            where,
            offset=offset,
            page_size=page_size,
            order_by=order_by,
            out_fields=out_fields,
            return_geometry=return_geometry,
        )
        page = payload.get("features") or []
        yield offset, page, bool(payload.get("exceededTransferLimit"))
        if not page or not payload.get("exceededTransferLimit"):
            return
        offset += len(page)
