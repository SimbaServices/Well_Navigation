"""Pull New Mexico OCD drilling permits with an approval date and surface location.

The current OCD wells service does not publish apr_date. Its effective_date is
the date the permitting record shows as Latest APD Approval, which is also the
Initial APD Approval when the well has only one approval. Spud date is never
copied into approved_at. New and Never Drilled map to approved; Cancelled maps
to cancelled. A real spud date drops the row: OCD often leaves a drilled well
marked New. The year-9999 spud sentinel does not.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from wellnav.ingest.arcgis import iter_features
from wellnav.ingest.classify import utcnow
from wellnav.operators import normalize_operator_name
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS

NM_PERMIT_QUERY = (
    "https://gis.emnrd.nm.gov/arcgis/rest/services/OCDView/"
    "Wells/FeatureServer/0/query"
)
DATE_FIELD = "effective_date"
# ArcGIS sentinel stored on undrilled wells (year 9999). Not an approval date.
SPUD_SENTINEL = 253402239600000

_PERMIT_STATUS = {
    "new": "approved",
    "never drilled": "approved",
    "cancelled": "cancelled",
    "canceled": "cancelled",
}


def _where(start: date, end: date) -> str:
    """Inclusive [start, end] as an ArcGIS date range (end exclusive next day)."""
    end_exclusive = end + timedelta(days=1)
    return (
        "status IN ('New','Never Drilled','Cancelled') AND "
        f"{DATE_FIELD} >= DATE '{start.isoformat()}' AND "
        f"{DATE_FIELD} < DATE '{end_exclusive.isoformat()}' AND "
        "(year_spudded = '9999' OR year_spudded IS NULL OR year_spudded = '')"
    )


def _text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "nan"}:
            return text
    return ""


def _api10(value: object) -> str | None:
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if len(digits) >= 10 and digits.startswith("30"):
        api = digits[:10]
    elif len(digits) == 8:
        api = "30" + digits
    else:
        return None
    if api[2:] == "00000000":
        return None
    return api


def _approval_date(value: object) -> str | None:
    """YYYY-MM-DD from an ArcGIS epoch or an ISO date. Sentinels become None."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            try:
                parsed = date.fromisoformat(text[:10])
            except ValueError:
                return None
            if parsed.year < 1901 or parsed.year > 2100:
                return None
            return parsed.isoformat()
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == SPUD_SENTINEL:
        return None
    seconds = number / 1000.0 if abs(number) > 10**11 else number
    try:
        parsed = datetime.fromtimestamp(seconds, timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None
    if parsed.year < 1901 or parsed.year > 2100:
        return None
    return parsed.isoformat()


def _coord(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number == 0:
        return None
    return number


def _crs(projection: object, *, from_geometry: bool) -> str:
    text = _text(projection).upper().replace(" ", "")
    if "NAD83" in text:
        return "EPSG:4269"
    if "NAD27" in text:
        return "EPSG:4267"
    if "WGS84" in text or "WGS1984" in text:
        return "EPSG:4326"
    if text:
        return _text(projection)
    # Attribute latitude/longitude on this layer are NAD83. Geometry from
    # iter_features is requested in WGS84 (outSR 4326).
    return "EPSG:4326" if from_geometry else "EPSG:4269"


def _split_name(name: str) -> tuple[str, str, str]:
    text = (name or "").strip()
    if " #" in text:
        lease, number = text.rsplit(" #", 1)
        return text, lease.strip(), number.strip()
    return text, text, ""


def _profile(raw: object) -> str:
    return {"H": "horizontal", "V": "vertical", "D": "directional"}.get(_text(raw).upper(), "")


def _real_spud(value: object) -> bool:
    """True when spud_date is a real drill date, not the year-9999 sentinel."""
    return _approval_date(value) is not None


def _in_window(approved: str, start: date, end: date) -> bool:
    return start.isoformat() <= approved <= end.isoformat()


def _feature_to_permit(feature: dict, *, now: str, start: date, end: date) -> dict | None:
    attrs = feature.get("attributes") or {}
    status = _PERMIT_STATUS.get(_text(attrs.get("status")).lower())
    if not status:
        return None
    api = _api10(attrs.get("id") or attrs.get("api") or attrs.get("API"))
    approved = _approval_date(attrs.get(DATE_FIELD))
    if not api or not approved or not _in_window(approved, start, end):
        return None
    if _real_spud(attrs.get("spud_date")):
        return None
    lat = _coord(attrs.get("latitude"))
    lon = _coord(attrs.get("longitude"))
    from_geometry = False
    if lat is None or lon is None:
        geom = feature.get("geometry") or {}
        lat = _coord(geom.get("y"))
        lon = _coord(geom.get("x"))
        from_geometry = lat is not None and lon is not None
    well_name, lease_name, well_no = _split_name(_text(attrs.get("name"), attrs.get("well_name")))
    permit_no = _text(attrs.get("id")) or api
    return {
        "api": api,
        "api8": api[2:10],
        "permit_no": permit_no,
        "status": status,
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease_name,
        "lease_no": "",
        "county": _text(attrs.get("county")),
        "county_code": api[2:5],
        "district": _text(attrs.get("district")),
        "operator": normalize_operator_name(_text(attrs.get("ogrid_name"), attrs.get("operator"))),
        "operator_number": _text(attrs.get("ogrid"), attrs.get("ogrid_no")),
        "profile": _profile(attrs.get("directional_status")),
        "symbol": _text(attrs.get("status")),
        "symnum": None,
        "wellhead_lat": lat,
        "wellhead_lon": lon,
        "wellhead_crs": _crs(attrs.get("projection"), from_geometry=from_geometry) if lat is not None else "",
        "approved_at": approved,
        "submitted_at": None,
        "expires_at": None,
        "lifetime_days": DEFAULT_PERMIT_LIFETIME_DAYS,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "nm_ocd",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def fetch_new_mexico_permits(start: date, end: date, *, delay: float = 0.12) -> list[dict]:
    """Permits whose OCD effective/approval date falls in [start, end], inclusive."""
    if end < start:
        return []
    now = utcnow()
    where = _where(start, end)
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for feature in iter_features(NM_PERMIT_QUERY, where=where, delay=delay, page_size=2000):
        row = _feature_to_permit(feature, now=now, start=start, end=end)
        if not row:
            continue
        key = (row["api8"], row["permit_no"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows
