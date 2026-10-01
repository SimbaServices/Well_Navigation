"""Louisiana SONRIS Office of Conservation permitted locations.

Current map service (layer Oil/Gas Wells, not the 2018 DOTD copy):
https://sonris-gis.dnr.la.gov/arcgis/rest/services/DNRSvc/OC/MapServer/0/query

Fields read from that layer (confirmed on the layer metadata, 2026-10-01):
  operator: ORG_OPER_NAME
  operator number: ORGANIZATION_ID
  parish: PARISH_NAME
  status: WELL_STATUS_CODE, with LEGEND_DESC for the legend text
  coordinates: geometry (outSR 4326) or SURFACE_LAT_DEC_DEG / SURFACE_LONG_DEC_DEG
  permit date: PERMIT_DATE
  spud: SPUD_DATE
  serial: WELL_SERIAL_NUM
  api: API_NUM

WELL_STATUS_CODE on this layer lines up with LEGEND_DESC. Codes kept, and only
when SPUD_DATE is empty or a sentinel (year 9999 or any year >= 2100):

  01 PERMITTED -> approved
  02 APPROVAL TO CONSTRUCT INJECTION WELL -> approved
     (undrilled approval; active injectors are code 09)
  03 PERMIT EXPIRED -> expired
  LEGEND_DESC containing CANCEL -> cancelled
     (no cancel code is published on this layer)

Every other WELL_STATUS_CODE is a drilled, completed, producing, injection,
shut-in, plugged, or other wellbore status and is dropped. A real spud date
drops the row even when the code is 01, 02, or 03. A where-clause that
mentions SPUD_DATE is rejected by the server (HTTP 403), so the spud check
runs in Python after the PERMIT_DATE filter.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from wellnav.ingest.arcgis import iter_features
from wellnav.ingest.classify import utcnow
from wellnav.ingest.la_refresh import LA_LAT_RANGE, LA_LON_RANGE, LA_PARISH
from wellnav.ingest.neighbors import la_serial_api
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.operators import normalize_operator_name
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS
from wellnav.well_names import normalize_well_identity

SONRIS_OC_WELLS = (
    "https://sonris-gis.dnr.la.gov/arcgis/rest/services/DNRSvc/OC/MapServer/0/query"
)
PAGE_SIZE = 1000
SOURCE = "la_sonris"

# Printed by the live extract. Do not guess names that are absent from the layer.
OPERATOR_FIELD = "ORG_OPER_NAME"
OPERATOR_NUMBER_FIELD = "ORGANIZATION_ID"
PARISH_FIELD = "PARISH_NAME"
STATUS_FIELD = "WELL_STATUS_CODE"
LEGEND_FIELD = "LEGEND_DESC"
LAT_FIELD = "SURFACE_LAT_DEC_DEG"
LON_FIELD = "SURFACE_LONG_DEC_DEG"

OUT_FIELDS = ",".join(
    (
        "WELL_SERIAL_NUM",
        "API_NUM",
        "PERMIT_DATE",
        "SPUD_DATE",
        STATUS_FIELD,
        LEGEND_FIELD,
        LAT_FIELD,
        LON_FIELD,
        PARISH_FIELD,
        "PARISH_CODE",
        OPERATOR_FIELD,
        OPERATOR_NUMBER_FIELD,
        "WELL_NAME",
        "WELL_NUM",
        "LEASE_NUM",
        "LUW_NAME",
        "DISTRICT_CODE",
    )
)

# Still a permit. Active injection, producing, and plugged codes are not in here.
KEEP_STATUS = {
    "01": "approved",
    "02": "approved",
    "03": "expired",
}

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_PARISH_FIPS = {
    "".join(ch for ch in name.upper() if ch.isalnum()): code
    for code, name in LA_PARISH.items()
}


def permit_date_where(start: date, end: date) -> str:
    """Server filter for an inclusive permit-date window.

    SONRIS stores PERMIT_DATE at Central midnight (05:00 or 06:00 UTC). On
    this service ``DATE 'YYYY-MM-DD'`` matches that calendar day, so
    ``<= DATE 'end'`` includes the end date. Checked 2026-10-01: the smoke
    window ending ``<= DATE '2026-10-01'`` returned the same 14 features as
    ``< DATE '2026-10-02'``.
    """
    return (
        f"PERMIT_DATE >= DATE '{start.isoformat()}' "
        f"AND PERMIT_DATE <= DATE '{end.isoformat()}'"
    )


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _status_code(value: object) -> str:
    text = _text(value).upper()
    if text.isdigit():
        return text.zfill(2)
    return text


def _esri_day(value: object) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            try:
                number = float(text)
            except ValueError:
                return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if abs(number) > 10_000_000_000:
        number /= 1000.0
    try:
        parsed = _EPOCH + timedelta(seconds=number)
    except OverflowError:
        return None
    return parsed.date()


def _real_spud(value: object) -> bool:
    """True when SPUD_DATE is a real drill date, not null and not a sentinel."""
    day = _esri_day(value)
    if day is None:
        return False
    if day.year >= 9999 or day.year >= 2100:
        return False
    return True


def _permit_status(code: str, legend: str) -> str | None:
    blob = legend.upper()
    if "CANCEL" in blob:
        return "cancelled"
    return KEEP_STATUS.get(code)


def _official_api(api_num: object) -> str | None:
    digits = "".join(ch for ch in _text(api_num) if ch.isdigit())
    if len(digits) < 10 or not digits.startswith("17"):
        return None
    api = digits[:10]
    if api[2:] == "00000000" or set(api[2:]) == {"0"}:
        return None
    return api


def _pair(lat: object, lon: object) -> tuple[float, float] | None:
    if lat in (None, "") or lon in (None, ""):
        return None
    try:
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None


def _in_louisiana(lat: float, lon: float) -> bool:
    return LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1] and LA_LON_RANGE[0] <= lon <= LA_LON_RANGE[1]


def _coordinates(feature: dict) -> tuple[float, float] | None | bool:
    """Return (lat, lon), None to keep the row with no location, or False to drop it.

    0,0 is left off the location fields. A non-zero point outside Louisiana
    drops the row. Geometry in 4326 wins when it falls inside the state.
    """
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    candidates: list[tuple[float, float]] = []
    geom_pair = _pair(geom.get("y"), geom.get("x"))
    if geom_pair is not None:
        candidates.append(geom_pair)
    surface = _pair(attrs.get(LAT_FIELD), attrs.get(LON_FIELD))
    if surface is not None:
        candidates.append(surface)
    saw_outside = False
    for lat, lon in candidates:
        if lat == 0 and lon == 0:
            continue
        if _in_louisiana(lat, lon):
            return lat, lon
        saw_outside = True
    if saw_outside:
        return False
    return None


def _county_code(parish_name: str, api: str, *, from_api_num: bool) -> str:
    code = _PARISH_FIPS.get("".join(ch for ch in parish_name.upper() if ch.isalnum()), "")
    if code:
        return code
    if from_api_num and len(api) >= 5:
        return api[2:5]
    return ""


def _serial_text(value: object) -> str:
    if value in (None, ""):
        return ""
    try:
        number = int(float(value))
    except (TypeError, ValueError):
        return _text(value)
    if number <= 0:
        return ""
    return str(number)


def feature_to_permit(feature: dict, *, start: date, end: date, now: str) -> dict | None:
    """Map one Oil/Gas Wells feature onto PERMIT_FIELDS, or drop it."""
    attrs = feature.get("attributes") or {}
    approved = _esri_day(attrs.get("PERMIT_DATE"))
    if approved is None or approved < start or approved > end:
        return None
    if _real_spud(attrs.get("SPUD_DATE")):
        return None
    code = _status_code(attrs.get(STATUS_FIELD))
    legend = _text(attrs.get(LEGEND_FIELD))
    status = _permit_status(code, legend)
    if not status:
        return None
    serial = _serial_text(attrs.get("WELL_SERIAL_NUM"))
    official = _official_api(attrs.get("API_NUM"))
    api = official or la_serial_api(serial)
    if not api:
        return None
    point = _coordinates(feature)
    if point is False:
        return None
    lat, lon = point if point else (None, None)
    api8 = api[2:10]
    well_name, well_no, lease_name = normalize_well_identity(
        _text(attrs.get("WELL_NAME")),
        _text(attrs.get("WELL_NUM")),
        _text(attrs.get("LUW_NAME")),
    )
    parish = _text(attrs.get(PARISH_FIELD))
    expires = (approved + timedelta(days=DEFAULT_PERMIT_LIFETIME_DAYS)).isoformat()
    record = {
        "api": api,
        "api8": api8,
        "permit_no": serial or f"SONRIS-{api8}",
        "status": status,
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease_name,
        "lease_no": _text(attrs.get("LEASE_NUM")),
        "county": parish,
        "county_code": _county_code(parish, api, from_api_num=official is not None),
        "district": _text(attrs.get("DISTRICT_CODE")),
        "operator": normalize_operator_name(_text(attrs.get(OPERATOR_FIELD))),
        "operator_number": _text(attrs.get(OPERATOR_NUMBER_FIELD)),
        "profile": "",
        "symbol": legend,
        "symnum": None,
        "wellhead_lat": lat,
        "wellhead_lon": lon,
        "wellhead_crs": "EPSG:4326" if lat is not None and lon is not None else None,
        "approved_at": approved.isoformat(),
        "submitted_at": None,
        "expires_at": expires,
        "lifetime_days": DEFAULT_PERMIT_LIFETIME_DAYS,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": SOURCE,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
    return {name: record.get(name) for name in PERMIT_FIELDS}


def _prefer(new: dict, old: dict) -> dict:
    if (new.get("approved_at") or "") > (old.get("approved_at") or ""):
        return new
    if new.get("approved_at") == old.get("approved_at") and new.get("wellhead_lat") is not None:
        if old.get("wellhead_lat") is None:
            return new
    return old


def pull_louisiana_permits(
    start: date, end: date, *, delay: float = 0.12
) -> tuple[list[dict], dict]:
    """Page the permit-date window and return kept rows plus filter stats."""
    where = permit_date_where(start, end)
    now = utcnow()
    # Primary key on permits is (api8, permit_no). The same 10-digit API can
    # have an expired serial and a later approved serial; both stay.
    kept: dict[tuple[str, str], dict] = {}
    raw = 0
    kept_codes: Counter[str] = Counter()
    dropped_codes: Counter[str] = Counter()
    for feature in iter_features(
        SONRIS_OC_WELLS,
        where=where,
        page_size=PAGE_SIZE,
        delay=delay,
        out_fields=OUT_FIELDS,
        return_geometry=True,
    ):
        raw += 1
        attrs = feature.get("attributes") or {}
        code = _status_code(attrs.get(STATUS_FIELD))
        row = feature_to_permit(feature, start=start, end=end, now=now)
        if row is None:
            dropped_codes[code] += 1
            continue
        kept_codes[code] += 1
        key = (row["api8"], row["permit_no"])
        previous = kept.get(key)
        kept[key] = row if previous is None else _prefer(row, previous)
    rows = sorted(kept.values(), key=lambda item: (item.get("approved_at") or "", item["api"]))
    kept_set = set(kept_codes)
    info = {
        "raw": raw,
        "where": where,
        "kept_codes": sorted(kept_set),
        "dropped_codes": sorted(set(dropped_codes) - kept_set),
        "kept_code_counts": dict(kept_codes),
        "dropped_code_counts": dict(dropped_codes),
    }
    return rows, info


def fetch_louisiana_permits(start: date, end: date, *, delay: float = 0.12) -> list[dict]:
    rows, _info = pull_louisiana_permits(start, end, delay=delay)
    return rows


def _refresh_paths() -> tuple[Path, Path]:
    folder = Path(__file__).resolve().parents[2] / "data" / "permit_refresh"
    return folder / "la.jsonl", folder / "la.meta.json"


def write_louisiana_permit_files(rows: list[dict], info: dict) -> tuple[Path, Path]:
    """Write the refresh extract. Caller must already have a successful full pull."""
    jsonl_path, meta_path = _refresh_paths()
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    jsonl_path.write_text(body, encoding="utf-8")
    approved = [row["approved_at"] for row in rows if row.get("approved_at")]
    statuses = Counter(row.get("status") or "" for row in rows)
    with_coordinates = sum(1 for row in rows if row.get("wellhead_lat") is not None)
    meta = {
        "complete": True,
        "count": len(rows),
        "with_coordinates": with_coordinates,
        "missing_coordinates": len(rows) - with_coordinates,
        "min_approved_at": min(approved) if approved else None,
        "max_approved_at": max(approved) if approved else None,
        "statuses": dict(sorted(statuses.items())),
        "status_codes_kept": info.get("kept_codes") or [],
        "status_codes_dropped": info.get("dropped_codes") or [],
        "where": info.get("where") or "",
        "fields": {
            "operator": OPERATOR_FIELD,
            "parish": PARISH_FIELD,
            "status": STATUS_FIELD,
            "legend": LEGEND_FIELD,
            "coordinates": ["geometry", LAT_FIELD, LON_FIELD],
        },
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return jsonl_path, meta_path
