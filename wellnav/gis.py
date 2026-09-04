"""RRC Public GIS well locations: explicit wellhead vs default toe/bottom-hole.

Official service:
  https://gis.rrc.texas.gov/server/rest/services/rrc_public/RRC_Public_Viewer_Srvs/MapServer

Layer 1  Well Locations — RRC's default mapped point. For horizontal/directional
          wells this is the bottom-hole / toe, not the wellhead.
Layer 9  Horiz/Dir Surface Locations — surface hole / wellhead for hz/dir wells.

Source data is NAD27 Lambert (US ft). The service also publishes GIS_LAT27/LONG27
and GIS_LAT83/LONG83. Everything is converted to WGS84 before mapping.
"""

from __future__ import annotations

from wellnav.coords import ConvertedPoint, convert_reported, to_wgs84
from wellnav.http_client import BLOCKED_STATUS, BlockedRequest, gis_get_json

GIS_MAPSERVER = (
    "https://gis.rrc.texas.gov/server/rest/services/rrc_public/"
    "RRC_Public_Viewer_Srvs/MapServer"
)
LAYER_WELL_LOCATIONS = 1
LAYER_SURFACE = 9


PAGE_SIZE = 1000


def _transient_gis_error(err: object) -> bool:
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
        )
    )


def query_features(layer_id: int, where: str, offset: int = 0, page_size: int = PAGE_SIZE) -> dict:
    from wellnav.ingest.limiter import acquire

    acquire()
    params = {
        "where": where,
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "orderByFields": "OBJECTID",
        "resultOffset": str(offset),
        "resultRecordCount": str(page_size),
        "f": "json",
    }
    return gis_get_json(f"{GIS_MAPSERVER}/{layer_id}/query", params)


def fetch_layer(
    layer_id: int,
    where: str,
    *,
    start_offset: int = 0,
    page_size: int = PAGE_SIZE,
    delay: float = 0.0,
) -> dict:
    """Page a GIS layer. On a blocked request, return partial features and the next offset."""
    import time

    from wellnav.ingest.limiter import is_active

    offset = int(start_offset or 0)
    features: list[dict] = []
    while True:
        try:
            payload = query_features(layer_id, where, offset=offset, page_size=page_size)
        except BlockedRequest as exc:
            return {
                "features": features,
                "complete": False,
                "blocked": True,
                "offset": offset,
                "error": str(exc),
                "retry_after": exc.retry_after,
            }
        err = payload.get("error")
        if err:
            if _transient_gis_error(err):
                return {
                    "features": features,
                    "complete": False,
                    "blocked": True,
                    "offset": offset,
                    "error": str(err),
                    "retry_after": 8,
                }
            raise RuntimeError(err)
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


def iter_features(layer_id: int, where: str, page_size: int = PAGE_SIZE, delay: float = 0.0):
    start = 0
    while True:
        result = fetch_layer(layer_id, where, start_offset=start, page_size=page_size, delay=delay)
        for feature in result["features"]:
            yield feature
        if result.get("blocked"):
            raise BlockedRequest(result.get("error") or "GIS request blocked", retry_after=result.get("retry_after"))
        if result.get("complete"):
            break
        start = result["offset"]


def _query_layer(layer_id: int, api8: str) -> dict | None:
    params = {
        "where": f"API='{api8}'",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": "4326",
        "f": "json",
    }
    payload = gis_get_json(f"{GIS_MAPSERVER}/{layer_id}/query", params)
    features = payload.get("features") or []
    if not features:
        return None
    return features[0]


def _point_from_feature(feature: dict, fallback_crs: str | None = None) -> ConvertedPoint | None:
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}

    lat83, lon83 = attrs.get("GIS_LAT83"), attrs.get("GIS_LONG83")
    if lat83 not in (None, "") and lon83 not in (None, ""):
        return to_wgs84(float(lon83), float(lat83), "nad83")

    gx, gy = geom.get("x"), geom.get("y")
    if gx not in (None, "") and gy not in (None, ""):
        return convert_reported(float(gx), float(gy), preferred="wgs84")

    lat27, lon27 = attrs.get("GIS_LAT27"), attrs.get("GIS_LONG27")
    if lat27 not in (None, "") and lon27 not in (None, ""):
        return to_wgs84(float(lon27), float(lat27), "nad27")

    if fallback_crs and gx not in (None, "") and gy not in (None, ""):
        return convert_reported(float(gx), float(gy), preferred=fallback_crs)
    return None


def _serialize_point(point: ConvertedPoint | None) -> dict | None:
    if point is None:
        return None
    return {
        "lat": round(point.lat, 8),
        "lon": round(point.lon, 8),
        "source_crs": point.source_crs,
        "source_label": point.source_label,
        "raw_x": point.raw_x,
        "raw_y": point.raw_y,
    }


def lookup_well_location(api8: str) -> dict:
    """Return wellhead (pin target) and optional toe, with CRS provenance."""
    surface_feat = _query_layer(LAYER_SURFACE, api8)
    default_feat = _query_layer(LAYER_WELL_LOCATIONS, api8)

    surface_pt = _point_from_feature(surface_feat) if surface_feat else None
    default_pt = _point_from_feature(default_feat) if default_feat else None

    default_attrs = (default_feat or {}).get("attributes") or {}
    surface_attrs = (surface_feat or {}).get("attributes") or {}

    if surface_pt and default_pt:
        wellhead = surface_pt
        toe = default_pt
        kind = "horizontal_or_directional"
        wellhead_note = (
            "Wellhead taken from RRC 'Horiz/Dir Surface Locations' (layer 9). "
            "RRC's default Well Locations layer (layer 1) is the bottom-hole / toe."
        )
    elif surface_pt:
        wellhead = surface_pt
        toe = None
        kind = "surface_only"
        wellhead_note = "Wellhead from RRC horizontal/directional surface layer."
    elif default_pt:
        wellhead = default_pt
        toe = default_pt
        kind = "vertical"
        wellhead_note = (
            "No separate surface-hole feature. Treated as a vertical well where "
            "wellhead and bottom-hole coincide on RRC Well Locations (layer 1)."
        )
    else:
        return {
            "api": api8,
            "found": False,
            "kind": "missing",
            "wellhead": None,
            "toe": None,
            "note": "No GIS feature found for this API on the RRC public well layers.",
            "attributes": {},
        }

    return {
        "api": api8,
        "found": True,
        "kind": kind,
        "wellhead": _serialize_point(wellhead),
        "toe": _serialize_point(toe) if toe and kind != "vertical" else None,
        "vertical_bottom": _serialize_point(toe) if kind == "vertical" else None,
        "note": wellhead_note,
        "symbol": surface_attrs.get("GIS_SYMBOL_DESCRIPTION")
        or default_attrs.get("GIS_SYMBOL_DESCRIPTION"),
        "location_source": surface_attrs.get("GIS_LOCATION_SOURCE")
        or default_attrs.get("GIS_LOCATION_SOURCE"),
        "well_number": default_attrs.get("GIS_WELL_NUMBER") or "",
        "attributes": {
            "gis_lat83": default_attrs.get("GIS_LAT83") or surface_attrs.get("GIS_LAT83"),
            "gis_long83": default_attrs.get("GIS_LONG83") or surface_attrs.get("GIS_LONG83"),
            "gis_lat27": default_attrs.get("GIS_LAT27") or surface_attrs.get("GIS_LAT27"),
            "gis_long27": default_attrs.get("GIS_LONG27") or surface_attrs.get("GIS_LONG27"),
        },
    }
