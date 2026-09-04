"""County-wide EWA wellbore identity, refined when RRC caps the query."""

from __future__ import annotations

import time

from wellnav.http_client import BlockedRequest
from wellnav.rrc import RESULT_CAP, RrcClient

PAGE_SIZE = 100
SCHEDULES_YN = ("Y", "N")
LEASE_TYPES = ("O", "G")
DISTRICTS = ("01", "02", "03", "04", "05", "06", "6E", "7B", "7C", "08", "8A", "09", "10")
WELL_TYPES = (
    "PR", "SH", "IN", "NP", "TA", "AB", "HI", "OB", "PP", "SD",
    "BM", "DW", "GJ", "GL", "GT", "GW", "LP", "LU", "OS", "PF",
    "RT", "SM", "TR", "WS", "ZZ",
)
IDENTITY_FIELDS = (
    "lease_name", "lease_no", "district", "operator", "operator_number",
    "field", "well_name", "well_no",
)


def initial_spec() -> dict:
    return {
        "schedule": "Both",
        "lease_type": "",
        "well_type": "",
        "district": "",
        "offset": 0,
        "done": False,
    }


def spec_key(spec: dict) -> tuple[str, str, str, str]:
    return (
        spec.get("schedule") or "",
        spec.get("lease_type") or "",
        spec.get("well_type") or "",
        spec.get("district") or "",
    )


def _child(spec: dict, **overrides) -> dict:
    item = {
        "schedule": spec.get("schedule") or "",
        "lease_type": spec.get("lease_type") or "",
        "well_type": spec.get("well_type") or "",
        "district": spec.get("district") or "",
        "offset": 0,
        "done": False,
    }
    item.update(overrides)
    return item


def refine(spec: dict) -> list[dict]:
    """Next split for an oversized query.

    Oversized counties jump straight to Y/N × O/G (4 splits). Districts come
    later, and only for splits that still exceed the cap. 4-digit api_prefix
    is not used — EWA ignores it.
    """
    schedule = spec.get("schedule") or ""
    lease_type = spec.get("lease_type") or ""
    well_type = spec.get("well_type") or ""
    district = spec.get("district") or ""

    if schedule in {"", "Both"} and not lease_type:
        return [
            _child(spec, schedule=sch, lease_type=lt)
            for sch in SCHEDULES_YN
            for lt in LEASE_TYPES
        ]
    if not lease_type:
        return [_child(spec, lease_type=lt) for lt in LEASE_TYPES]
    # Off-schedule wells have no well-type codes; split those by district first.
    if schedule != "N" and not well_type:
        return [_child(spec, well_type=wt) for wt in WELL_TYPES]
    if not district:
        return [_child(spec, district=code) for code in DISTRICTS]
    if not well_type:
        return [_child(spec, well_type=wt) for wt in WELL_TYPES]
    return []


def fetch_page(
    client: RrcClient,
    county_code: str,
    spec: dict,
    *,
    page_size: int = PAGE_SIZE,
    offset: int = 0,
) -> dict:
    page = client.search_wellbores(
        county_code=county_code,
        schedule=spec.get("schedule") or "Both",
        lease_type=spec.get("lease_type") or "",
        district=spec.get("district") or "",
        well_type=spec.get("well_type") or "",
        page_size=page_size,
        offset=offset,
    )
    wells = list(page.get("wells") or [])
    total = int(page.get("total") or 0)
    return {
        "wells": wells,
        "total": total,
        "start": int(page.get("start") or 0),
        "end": int(page.get("end") or 0),
        "over_limit": bool(page.get("over_limit")) or total >= RESULT_CAP,
        "no_results": bool(page.get("no_results")),
    }


def page_split(
    client: RrcClient,
    county_code: str,
    spec: dict,
    first_page: dict,
    *,
    delay: float = 0.0,
) -> list[dict]:
    """Page every row of a split whose reported total is under the cap."""
    wells = list(first_page.get("wells") or [])
    total = int(first_page.get("total") or 0)
    if first_page.get("over_limit") or total >= RESULT_CAP:
        return wells
    offset = int(first_page.get("end") or 0) or len(wells)
    while offset < total:
        if delay:
            time.sleep(delay)
        page = fetch_page(client, county_code, spec, page_size=PAGE_SIZE, offset=offset)
        wells.extend(page["wells"])
        if page["over_limit"]:
            break
        nxt = int(page.get("end") or 0)
        if not page["wells"] or nxt <= offset:
            break
        offset = nxt
    return wells


def _remember(collected: dict[str, dict], wells: list[dict]) -> None:
    for well in wells:
        api = well.get("api") or well.get("api8")
        if api:
            collected[api[-8:]] = well


def _snapshot(collected: dict[str, dict], queue: list[dict], seen: set[tuple]) -> dict:
    return {
        "identity_wells": list(collected.values()),
        "identity_queue": queue,
        "identity_seen_specs": [list(key) for key in seen],
        "identity_complete": False,
    }


def fetch_county_identity(
    county_code: str,
    *,
    client: RrcClient | None = None,
    delay: float = 0.0,
    scratch: dict | None = None,
) -> dict:
    """Pull lease/operator/district identity for one county.

    If the first request is over the cap or returns Application Error / Ewa_123,
    refine immediately. The parent spec is not marked done with 0 rows.
    First-page wells from a rejected split are kept.
    """
    client = client or RrcClient()
    state = scratch if scratch is not None else {}
    if state.get("identity_complete") and not state.get("identity_queue"):
        wells = list(state.get("identity_wells") or [])
        return {
            "wells": wells,
            "count": len(wells),
            "identity_complete": True,
        }

    collected = {
        (well.get("api") or well.get("api8") or "")[-8:]: well
        for well in (state.get("identity_wells") or [])
        if well.get("api") or well.get("api8")
    }
    queue = list(state.get("identity_queue") or [])
    seen = {tuple(key) for key in (state.get("identity_seen_specs") or [])}
    if not queue and not seen and not collected:
        queue = [initial_spec()]

    while queue:
        spec = queue.pop(0)
        if spec.get("done"):
            continue
        key = spec_key(spec)
        if key in seen:
            continue
        offset = int(spec.get("offset") or 0)
        try:
            page = fetch_page(
                client, county_code, spec, page_size=PAGE_SIZE, offset=offset
            )
        except BlockedRequest:
            spec["offset"] = offset
            queue.insert(0, spec)
            state.clear()
            state.update(_snapshot(collected, queue, seen))
            raise
        _remember(collected, page["wells"])
        if page["over_limit"]:
            for child in refine(spec):
                if spec_key(child) not in seen:
                    queue.append(child)
            seen.add(key)
            continue
        if page["no_results"] or (not page["wells"] and not page["total"]):
            spec["done"] = True
            seen.add(key)
            continue
        try:
            paged = page_split(client, county_code, spec, page, delay=delay)
        except BlockedRequest:
            spec["offset"] = int(page.get("end") or offset)
            queue.insert(0, spec)
            state.clear()
            state.update(_snapshot(collected, queue, seen))
            raise
        _remember(collected, paged)
        spec["done"] = True
        seen.add(key)

    wells = list(collected.values())
    state.clear()
    state.update(
        {
            "identity_wells": wells,
            "identity_queue": [],
            "identity_seen_specs": [list(key) for key in seen],
            "identity_complete": True,
        }
    )
    return {
        "wells": wells,
        "count": len(wells),
        "identity_complete": True,
    }


def merge_identity(rows: list[dict], identities: list[dict]) -> int:
    by_api = {}
    for ident in identities:
        api = ident.get("api") or ident.get("api8") or ""
        if api:
            by_api[api[-8:]] = ident
    updated = 0
    for row in rows:
        api8 = (row.get("api8") or row.get("api") or "")[-8:]
        ident = by_api.get(api8)
        if not ident:
            continue
        changed = False
        for name in IDENTITY_FIELDS:
            value = ident.get(name) or ""
            if value:
                row[name] = value
                changed = True
        if changed:
            updated += 1
    return updated
