"""Convert RRC-reported coordinates (NAD27/NAD83/state plane/Lambert) to WGS84."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pyproj import CRS, Transformer

TEXAS_BBOX = {"lat_min": 25.7, "lat_max": 36.6, "lon_min": -106.8, "lon_max": -93.4}

# RRC stores mapped wells in a statewide NAD27 Lambert (US survey feet).
RRC_LAMBERT_WKT = (
    'PROJCS["PCS_Lambert_Conformal_Conic",'
    'GEOGCS["GCS_North_American_1927",'
    'DATUM["D_North_American_1927",SPHEROID["Clarke_1866",6378206.4,294.9786982]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],'
    'PROJECTION["Lambert_Conformal_Conic_2SP"],'
    'PARAMETER["False_Easting",2499999.986866666],'
    'PARAMETER["False_Northing",2499999.986866666],'
    'PARAMETER["Central_Meridian",-100.0],'
    'PARAMETER["Standard_Parallel_1",29.0],'
    'PARAMETER["Standard_Parallel_2",33.0],'
    'PARAMETER["Latitude_Of_Origin",31.0],'
    'UNIT["Foot_US",0.3048006096012192]]'
)

CRS_CATALOG: dict[str, dict] = {
    "wgs84": {"crs": "EPSG:4326", "label": "WGS 84 geographic"},
    "web_mercator": {"crs": "EPSG:3857", "label": "Web Mercator"},
    "nad83": {"crs": "EPSG:4269", "label": "NAD83 geographic"},
    "nad27": {"crs": "EPSG:4267", "label": "NAD27 geographic"},
    "rrc_lambert_nad27_ft": {"crs": RRC_LAMBERT_WKT, "label": "RRC statewide Lambert (NAD27, US ft)"},
    "tx_n_nad83_ft": {"crs": "EPSG:2275", "label": "Texas North SPCS NAD83 (US ft)"},
    "tx_nc_nad83_ft": {"crs": "EPSG:2276", "label": "Texas North Central SPCS NAD83 (US ft)"},
    "tx_c_nad83_ft": {"crs": "EPSG:2277", "label": "Texas Central SPCS NAD83 (US ft)"},
    "tx_sc_nad83_ft": {"crs": "EPSG:2278", "label": "Texas South Central SPCS NAD83 (US ft)"},
    "tx_s_nad83_ft": {"crs": "EPSG:2279", "label": "Texas South SPCS NAD83 (US ft)"},
    "tx_n_nad27_ft": {"crs": "EPSG:32037", "label": "Texas North SPCS NAD27 (US ft)"},
    "tx_nc_nad27_ft": {"crs": "EPSG:32038", "label": "Texas North Central SPCS NAD27 (US ft)"},
    "tx_c_nad27_ft": {"crs": "EPSG:32039", "label": "Texas Central SPCS NAD27 (US ft)"},
    "tx_sc_nad27_ft": {"crs": "EPSG:32040", "label": "Texas South Central SPCS NAD27 (US ft)"},
    "tx_s_nad27_ft": {"crs": "EPSG:32041", "label": "Texas South SPCS NAD27 (US ft)"},
}

_TRANSFORMERS: dict[str, Transformer] = {}


@dataclass
class ConvertedPoint:
    lat: float
    lon: float
    source_crs: str
    source_label: str
    raw_x: float
    raw_y: float

    @property
    def in_texas(self) -> bool:
        return (
            TEXAS_BBOX["lat_min"] <= self.lat <= TEXAS_BBOX["lat_max"]
            and TEXAS_BBOX["lon_min"] <= self.lon <= TEXAS_BBOX["lon_max"]
        )


def _transformer(crs_key: str) -> Transformer:
    if crs_key not in _TRANSFORMERS:
        spec = CRS_CATALOG[crs_key]["crs"]
        src = CRS.from_user_input(spec)
        _TRANSFORMERS[crs_key] = Transformer.from_crs(src, "EPSG:4326", always_xy=True)
    return _TRANSFORMERS[crs_key]


def looks_geographic(x: float, y: float) -> bool:
    """True when values look like lon/lat (or lat/lon) rather than easting/northing."""
    a, b = abs(x), abs(y)
    return a <= 180 and b <= 90 or a <= 90 and b <= 180


def looks_projected(x: float, y: float) -> bool:
    return abs(x) > 180 and abs(y) > 180


def to_wgs84(x: float, y: float, crs_key: str) -> ConvertedPoint:
    if crs_key == "wgs84":
        lon, lat = _normalize_lon_lat(x, y)
        return ConvertedPoint(lat, lon, crs_key, CRS_CATALOG[crs_key]["label"], x, y)
    transformer = _transformer(crs_key)
    if crs_key in {"nad83", "nad27"}:
        lon, lat = _normalize_lon_lat(x, y)
        lon, lat = transformer.transform(lon, lat)
    else:
        lon, lat = transformer.transform(x, y)
    return ConvertedPoint(lat, lon, crs_key, CRS_CATALOG[crs_key]["label"], x, y)


def _normalize_lon_lat(x: float, y: float) -> tuple[float, float]:
    """Accept either (lon, lat) or (lat, lon) for Texas geographic values."""
    if 25 <= abs(x) <= 37 and 93 <= abs(y) <= 107:
        lat, lon = x, -abs(y) if y > 0 else y
        return lon, lat
    if 25 <= abs(y) <= 37 and 93 <= abs(x) <= 107:
        lon = -abs(x) if x > 0 else x
        return lon, y
    if abs(x) <= 180 and abs(y) <= 90:
        lon = -abs(x) if 93 <= abs(x) <= 107 and x > 0 else x
        return lon, y
    return x, y


def convert_reported(x: float, y: float, preferred: str | None = None) -> ConvertedPoint:
    """Convert a reported pair to WGS84, auto-detecting geographic vs state plane."""
    if preferred and preferred in CRS_CATALOG:
        point = to_wgs84(x, y, preferred)
        if point.in_texas or preferred in {"wgs84", "nad83", "nad27"}:
            return point

    if looks_geographic(x, y):
        for key in ("nad83", "nad27", "wgs84"):
            point = to_wgs84(x, y, key)
            if point.in_texas:
                return point
        return to_wgs84(x, y, "nad83")

    candidates = [
        "rrc_lambert_nad27_ft",
        "tx_n_nad83_ft",
        "tx_nc_nad83_ft",
        "tx_c_nad83_ft",
        "tx_sc_nad83_ft",
        "tx_s_nad83_ft",
        "tx_n_nad27_ft",
        "tx_nc_nad27_ft",
        "tx_c_nad27_ft",
        "tx_sc_nad27_ft",
        "tx_s_nad27_ft",
    ]
    best: ConvertedPoint | None = None
    best_score = 1e18
    mid_lat = (TEXAS_BBOX["lat_min"] + TEXAS_BBOX["lat_max"]) / 2
    mid_lon = (TEXAS_BBOX["lon_min"] + TEXAS_BBOX["lon_max"]) / 2
    for key in candidates:
        try:
            point = to_wgs84(x, y, key)
        except Exception:
            continue
        if not point.in_texas:
            continue
        score = (point.lat - mid_lat) ** 2 + (point.lon - mid_lon) ** 2
        if score < best_score:
            best, best_score = point, score
    if best:
        return best
    return to_wgs84(x, y, "rrc_lambert_nad27_ft")


def crs_choices() -> list[tuple[str, str]]:
    return [(key, meta["label"]) for key, meta in CRS_CATALOG.items()]


def transform_lonlat_nad27(lons: Sequence[float], lats: Sequence[float]) -> tuple[list[float], list[float]]:
    """Batch-convert NAD27 geographic coordinates to WGS84 lon/lat."""
    transformer = _transformer("nad27")
    xs, ys = transformer.transform(list(lons), list(lats))
    return list(xs), list(ys)


def transform_line_nad27(points: Sequence[Sequence[float]]) -> list[list[float]]:
    """Convert a polyline of NAD27 (lon, lat) vertices to WGS84."""
    if not points:
        return []
    lons = [float(pt[0]) for pt in points]
    lats = [float(pt[1]) for pt in points]
    xs, ys = transform_lonlat_nad27(lons, lats)
    return [[x, y] for x, y in zip(xs, ys)]
