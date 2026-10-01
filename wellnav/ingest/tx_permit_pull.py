"""Texas RRC EWA drilling-permit (W-1) pull for a closed approved-date window.

Each request is a calendar month (or a smaller half if that month fails or the
HTML is truncated). Coordinates are filled afterwards from RRC Public GIS for
those 8-digit APIs only: layer 9 surface location when it has a point, else
layer 1. Approval dates are taken from the EWA row and never invented.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta

from wellnav.coords import convert_reported, to_wgs84
from wellnav.gis import LAYER_SURFACE, LAYER_WELL_LOCATIONS, _serialize_point, fetch_layer
from wellnav.ingest.classify import utcnow
from wellnav.ingest.permits import ewa_row_to_permit
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.rrc import CLIENT
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS

GIS_API_CHUNK = 40
_GIS_ATTEMPTS = 4


def fetch_texas_permits(start: date, end: date, *, delay: float = 0.12) -> list[dict]:
    """Return permit dicts approved on dates from ``start`` through ``end`` inclusive."""
    if start > end:
        raise ValueError(f"start {start.isoformat()} is after end {end.isoformat()}")
    now = utcnow()
    records: list[dict] = []
    for row in _pull_months(start, end, delay=delay):
        record = _record_from_ewa(row, start=start, end=end, now=now)
        if record:
            records.append(record)
    records = _dedupe(records)
    if records:
        _attach_wellheads(records, delay=delay)
    return records


def _pull_months(start: date, end: date, *, delay: float) -> list[dict]:
    rows: list[dict] = []
    for chunk_start, chunk_end in _month_ranges(start, end):
        rows.extend(_pull_range(chunk_start, chunk_end, delay=delay))
    return rows


def _month_ranges(start: date, end: date) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        if cursor.month == 12:
            month_end = date(cursor.year, 12, 31)
        else:
            month_end = date(cursor.year, cursor.month + 1, 1) - timedelta(days=1)
        chunk_end = min(end, month_end)
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def _pull_range(start: date, end: date, *, delay: float) -> list[dict]:
    _pause(delay)
    try:
        return _chunk_permits(start, end)
    except Exception as exc:
        halves = _halves(start, end)
        if halves is None:
            raise
        left, right = halves
        print(
            f"tx ewa split {start.isoformat()}..{end.isoformat()} "
            f"-> {left[0].isoformat()}..{left[1].isoformat()} "
            f"+ {right[0].isoformat()}..{right[1].isoformat()} ({exc})",
            flush=True,
        )
        return _pull_range(left[0], left[1], delay=delay) + _pull_range(
            right[0], right[1], delay=delay
        )


def _halves(start: date, end: date) -> tuple[tuple[date, date], tuple[date, date]] | None:
    if start >= end:
        return None
    mid = start + timedelta(days=(end - start).days // 2)
    right_start = mid + timedelta(days=1)
    if right_start > end:
        return None
    return (start, mid), (right_start, end)


def _chunk_permits(start: date, end: date) -> list[dict]:
    approved_from = start.strftime("%m/%d/%Y")
    approved_to = end.strftime("%m/%d/%Y")
    parsed = CLIENT.search_drilling_permits(
        approved_from=approved_from,
        approved_to=approved_to,
    )
    permits = list(parsed.get("permits") or [])
    reported = _as_count(parsed.get("total"))
    # `total` is the pager count. On a full page the banner is "N results"
    # and the parser sets total to the rows it kept. `page_size` is the first
    # response's hit count, which includes duplicate permit rows EWA prints
    # twice; those repeats are already collapsed and are not a short page.
    if reported is None or len(permits) != reported:
        raise RuntimeError(
            f"RRC permit HTML truncated for {approved_from}..{approved_to}: "
            f"parsed {len(permits)} of {parsed.get('total')}"
        )
    print(f"tx ewa {approved_from} .. {approved_to}: {len(permits)}", flush=True)
    return permits


def _as_count(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _parse_mdy(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%m/%d/%Y").date()
    except ValueError:
        return None


def _record_from_ewa(row: dict, *, start: date, end: date, now: str) -> dict | None:
    approved = _parse_mdy(row.get("approved_at"))
    if approved is None or approved < start or approved > end:
        return None
    mapped = ewa_row_to_permit(row, now=now, lifetime_days=DEFAULT_PERMIT_LIFETIME_DAYS)
    if not mapped or mapped.get("approved_at") != approved.isoformat():
        return None
    return {field: mapped.get(field) for field in PERMIT_FIELDS}


def _dedupe(records: list[dict]) -> list[dict]:
    seen: set[tuple[str, str]] = set()
    unique: list[dict] = []
    for record in records:
        key = (str(record.get("api") or ""), str(record.get("permit_no") or ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    unique.sort(
        key=lambda rec: (
            rec.get("approved_at") or "",
            rec.get("api") or "",
            rec.get("permit_no") or "",
        )
    )
    return unique


def _attach_wellheads(records: list[dict], *, delay: float) -> None:
    apis: list[str] = []
    seen: set[str] = set()
    for record in records:
        api8 = str(record.get("api8") or "")
        if len(api8) == 8 and api8 not in seen:
            seen.add(api8)
            apis.append(api8)
    surface: dict[str, dict] = {}
    wells: dict[str, dict] = {}
    groups = _chunks(apis, GIS_API_CHUNK)
    print(f"tx gis {len(apis)} apis in {len(groups)} chunks", flush=True)
    for index, group in enumerate(groups, start=1):
        where = _api_in_clause(group)
        surface.update(_index_features(_gis_features(LAYER_SURFACE, where, delay=delay)))
        wells.update(_index_features(_gis_features(LAYER_WELL_LOCATIONS, where, delay=delay)))
        if index == 1 or index == len(groups) or index % 25 == 0:
            print(f"tx gis chunk {index}/{len(groups)}", flush=True)
    for record in records:
        api8 = str(record.get("api8") or "")
        point = _wellhead_point(surface.get(api8)) or _wellhead_point(wells.get(api8))
        if point is None:
            record["wellhead_lat"] = None
            record["wellhead_lon"] = None
            record["wellhead_crs"] = None
            continue
        record["wellhead_lat"] = point["lat"]
        record["wellhead_lon"] = point["lon"]
        record["wellhead_crs"] = point["source_label"]


def _chunks(items: list[str], size: int) -> list[list[str]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _api_in_clause(apis: list[str]) -> str:
    quoted = ",".join("'" + api.replace("'", "''") + "'" for api in apis)
    return f"API IN ({quoted})"


def _gis_features(layer_id: int, where: str, *, delay: float) -> list[dict]:
    last: Exception | None = None
    for attempt in range(_GIS_ATTEMPTS):
        _pause(delay if attempt == 0 else max(delay, 1.0))
        try:
            result = fetch_layer(layer_id, where, delay=delay)
        except Exception as exc:
            last = exc
            continue
        if result.get("blocked") or not result.get("complete", False):
            last = RuntimeError(result.get("error") or f"GIS layer {layer_id} incomplete")
            continue
        return list(result.get("features") or [])
    raise RuntimeError(f"GIS layer {layer_id} failed for {where}: {last}")


def _index_features(features: list[dict]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for feature in features:
        api8 = _feature_api8(feature)
        if not api8:
            continue
        current = found.get(api8)
        if current is None or (
            _wellhead_point(current) is None and _wellhead_point(feature) is not None
        ):
            found[api8] = feature
    return found


def _feature_api8(feature: dict) -> str:
    raw = (feature.get("attributes") or {}).get("API")
    if raw is None:
        return ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if not digits:
        return ""
    if len(digits) > 8:
        if digits.startswith("42") and len(digits) >= 10:
            digits = digits[2:10]
        else:
            digits = digits[-8:]
    if len(digits) > 8:
        return ""
    return digits.zfill(8)


def _wellhead_point(feature: dict | None) -> dict | None:
    """Layer point as WGS84, matching build_record's converters.

    Geometry from an outSR 4326 query is WGS84. When that point is missing,
    GIS_LAT83/GIS_LONG83 are converted from NAD83 the same way build_record does.
    """
    if not feature:
        return None
    attrs = feature.get("attributes") or {}
    geom = feature.get("geometry") or {}
    gx, gy = geom.get("x"), geom.get("y")
    if gx not in (None, "") and gy not in (None, ""):
        x, y = float(gx), float(gy)
        if abs(x) <= 180 and abs(y) <= 90:
            return _serialize_point(convert_reported(x, y, preferred="wgs84"))
    lat83 = attrs.get("GIS_LAT83")
    lon83 = attrs.get("GIS_LONG83")
    if lat83 not in (None, "") and lon83 not in (None, ""):
        return _serialize_point(to_wgs84(float(lon83), float(lat83), "nad83"))
    return None


def _pause(delay: float) -> None:
    if delay and delay > 0:
        time.sleep(delay)
