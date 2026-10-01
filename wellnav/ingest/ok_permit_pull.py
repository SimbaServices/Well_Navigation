"""Oklahoma Corporation Commission intent-to-drill permit locations.

The ITD FeatureServer is already about the last two years. Queries still send
a server-side date window. A permit is in range when approval_date, cancel_date,
or expire_date falls in that window.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from wellnav.ingest.arcgis import iter_features
from wellnav.ingest.classify import utcnow
from wellnav.ingest.neighbors import _text
from wellnav.ingest.ok_wells import (
    OK_ITD,
    _epoch_iso,
    _in_oklahoma,
    itd_feature_to_permit,
)
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS

_WINDOW_FIELDS = ("approval_date", "cancel_date", "expire_date")
# OCC well-status codes. ND/EX/CA are still permits. AC/PA/SP/TA have been drilled.
_LOCATION_STATUS = {"ND": "approved", "EX": "expired", "CA": "cancelled", "C": "cancelled"}
_DRILLED_STATUS = {"AC", "PA", "SP", "TA", "OR", "NE"}
_CODE_APPROVED = {"WRA", "EXT", "GRP"}


def _as_date(value: date, name: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if not isinstance(value, date):
        raise TypeError(f"{name} must be a date")
    return value


def oklahoma_permit_where(start: date, end: date) -> str:
    """Inclusive [start, end] window, exclusive upper bound on the next day."""
    start = _as_date(start, "start")
    end = _as_date(end, "end")
    if end < start:
        raise ValueError(f"end {end.isoformat()} is before start {start.isoformat()}")
    start_text = start.isoformat()
    end_text = (end + timedelta(days=1)).isoformat()
    parts = [
        f"({field} >= DATE '{start_text}' AND {field} < DATE '{end_text}')"
        for field in _WINDOW_FIELDS
    ]
    return " OR ".join(parts)


def _day(value: object) -> date | None:
    iso = _epoch_iso(value)
    if not iso:
        return None
    try:
        return date.fromisoformat(iso[:10])
    except ValueError:
        return None


def _in_window(day: date | None, start: date, end: date) -> bool:
    return day is not None and start <= day <= end


def _location_status(attrs: dict, mapped_status: str) -> str | None:
    """Return approved/cancelled/expired for an undrilled ITD, or None if it was drilled."""
    well_status = _text(attrs.get("well_status")).upper()
    if well_status in _DRILLED_STATUS:
        return None
    if well_status in _LOCATION_STATUS:
        return _LOCATION_STATUS[well_status]
    if well_status:
        return None
    status = (mapped_status or "").strip().lower()
    if status in {"approved", "cancelled", "expired"}:
        return status
    if status.upper() in _CODE_APPROVED:
        return "approved"
    return None


def fetch_oklahoma_permits(start: date, end: date, *, delay: float = 0.12) -> list[dict]:
    """Return PERMIT_FIELDS rows approved, cancelled, or expiring in [start, end]."""
    start = _as_date(start, "start")
    end = _as_date(end, "end")
    where = oklahoma_permit_where(start, end)
    now = utcnow()
    rows: list[dict] = []
    for feature in iter_features(OK_ITD, where=where, delay=delay):
        attrs = feature.get("attributes") or {}
        approved = _day(attrs.get("approval_date"))
        if approved is None:
            continue
        if not any(_in_window(_day(attrs.get(field)), start, end) for field in _WINDOW_FIELDS):
            continue
        mapped = itd_feature_to_permit(
            feature,
            now=now,
            lifetime_days=DEFAULT_PERMIT_LIFETIME_DAYS,
        )
        if not mapped:
            continue
        lat = mapped.get("wellhead_lat")
        lon = mapped.get("wellhead_lon")
        if not _in_oklahoma(lat if isinstance(lat, float) else None, lon if isinstance(lon, float) else None):
            continue
        if mapped.get("approved_at") != approved.isoformat():
            continue
        api = str(mapped.get("api") or "")
        if not api.startswith("35"):
            continue
        if mapped.get("source") != "ok_occ_itd":
            continue
        location_status = _location_status(attrs, str(mapped.get("status") or ""))
        if location_status is None:
            continue
        mapped["status"] = location_status
        if location_status == "approved":
            mapped["symbol"] = "Permitted"
        rows.append({field: mapped.get(field) for field in PERMIT_FIELDS})
    return _dedupe_permits(rows)


def _dedupe_permits(rows: list[dict]) -> list[dict]:
    """The ITD layer repeats features. Keep one row per API and permit number."""
    kept: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (str(row.get("api8") or ""), str(row.get("permit_no") or ""))
        previous = kept.get(key)
        if previous is None or str(row.get("approved_at") or "") >= str(previous.get("approved_at") or ""):
            kept[key] = row
    return list(kept.values())
