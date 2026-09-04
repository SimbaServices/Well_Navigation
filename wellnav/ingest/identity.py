"""Fetch RRC EWA wellbore identity (lease/operator/district/field) by county.

EWA caps a query at ~10,000 rows and returns Application Error when a
county-wide search is too wide. Oversized queries refine immediately into
Y/N × O/G, then district, then 4-digit API prefix. First-page rows are
kept when a page errors or hits the cap.
"""

from __future__ import annotations

from wellnav.http_client import BlockedRequest
from wellnav.rrc import RrcClient

EWA_RESULT_CAP = 10000
PAGE_SIZE = 100
DISTRICTS = (
    "01", "02", "03", "04", "05", "06", "6E", "7B", "7C", "08", "8A", "09", "10",
)
SCHEDULES = ("Y", "N")
LEASE_TYPES = ("O", "G")


def _api8(row: dict) -> str:
    raw = row.get("api8") or row.get("api") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    if digits.startswith("42") and len(digits) >= 10:
        digits = digits[2:]
    return digits[:8]


def _spec(
    base: dict | None = None,
    *,
    schedule: str = "",
    lease_type: str = "",
    district: str = "",
    api_prefix: str = "",
    offset: int = 0,
) -> dict:
    src = dict(base or {})
    if schedule:
        src["schedule"] = schedule
    if lease_type:
        src["lease_type"] = lease_type
    if district:
        src["district"] = district
    if api_prefix:
        src["api_prefix"] = api_prefix
    src["offset"] = int(offset)
    src.setdefault("schedule", "Both")
    src.setdefault("lease_type", "")
    src.setdefault("district", "")
    src.setdefault("api_prefix", "")
    return src


def _refine(spec: dict, county_code: str) -> list[dict]:
    """Narrow an oversized query. Huge counties start at Y/N × O/G."""
    schedule = spec.get("schedule") or "Both"
    lease_type = spec.get("lease_type") or ""
    district = spec.get("district") or ""
    api_prefix = spec.get("api_prefix") or ""
    if schedule in {"", "Both"}:
        return [
            _spec(spec, schedule=sched, lease_type=kind, offset=0)
            for sched in SCHEDULES
            for kind in LEASE_TYPES
        ]
    if not lease_type:
        return [_spec(spec, lease_type=kind, offset=0) for kind in LEASE_TYPES]
    if not district:
        return [_spec(spec, district=code, offset=0) for code in DISTRICTS]
    if not api_prefix:
        prefix = (county_code or "").zfill(3)
        return [_spec(spec, api_prefix=f"{prefix}{digit}", offset=0) for digit in "0123456789"]
    return []


def _fetch_page(client: RrcClient, county_code: str, spec: dict) -> dict:
    from wellnav.ingest.limiter import acquire

    acquire()
    offset = int(spec.get("offset") or 0)
    try:
        page = client.search_wellbores(
            county_code=county_code,
            schedule=spec.get("schedule") or "Both",
            lease_type=spec.get("lease_type") or "",
            district=spec.get("district") or "",
            api_prefix=spec.get("api_prefix") or "",
            page_size=PAGE_SIZE,
            offset=offset,
        )
    except BlockedRequest as exc:
        return {
            "wells": [],
            "total": 0,
            "end": offset,
            "oversized": False,
            "blocked": True,
            "error": str(exc),
            "retry_after": exc.retry_after,
        }
    except RuntimeError as exc:
        if "application error" in str(exc).lower():
            return {
                "wells": [],
                "total": EWA_RESULT_CAP,
                "end": offset,
                "oversized": True,
                "blocked": False,
                "error": str(exc),
                "retry_after": None,
            }
        raise
    total = int(page.get("total") or 0)
    wells = list(page.get("wells") or [])
    end = int(page.get("end") or 0) or (offset + len(wells))
    return {
        "wells": wells,
        "total": total,
        "end": end,
        "oversized": total >= EWA_RESULT_CAP,
        "blocked": False,
        "error": None,
        "retry_after": None,
    }


def _merge_identity(store: dict[str, dict], well: dict) -> None:
    api8 = _api8(well)
    if len(api8) != 8:
        return
    item = dict(well)
    item["api8"] = api8
    item.setdefault("api", api8)
    store[api8] = item


def fetch_county_identities(
    county_code: str,
    *,
    scratch: dict | None = None,
    delay: float = 0.12,
    client: RrcClient | None = None,
) -> dict:
    """Page EWA wellbore identity for one county, refining when the query is too wide.

    Returns identities plus scratch so a blocked request can resume later.
    ``delay`` is accepted for call-site compatibility; pacing uses the shared limiter.
    """
    del delay
    client = client or RrcClient()
    state = dict(scratch or {})
    store: dict[str, dict] = {}
    for row in state.get("identities") or []:
        _merge_identity(store, row)
    queue = list(state.get("identity_queue") or [])
    if not queue and not state.get("identity_complete"):
        queue = [_spec(schedule="Both")]

    while queue:
        spec = queue.pop(0)
        page = _fetch_page(client, county_code, spec)
        for well in page.get("wells") or []:
            _merge_identity(store, well)
        if page.get("blocked"):
            queue.insert(0, spec)
            return {
                "identities": list(store.values()),
                "complete": False,
                "blocked": True,
                "error": page.get("error") or "EWA wellbore query blocked",
                "retry_after": page.get("retry_after"),
                "scratch": {
                    "identities": list(store.values()),
                    "identity_queue": queue,
                    "identity_complete": False,
                },
            }
        if page.get("oversized"):
            refined = _refine(spec, county_code)
            if refined:
                queue = refined + queue
                continue
        total = int(page.get("total") or 0)
        end = int(page.get("end") or 0)
        offset = int(spec.get("offset") or 0)
        if total and end and end < total and end > offset and not page.get("oversized"):
            queue.insert(0, _spec(spec, offset=end))
            continue

    return {
        "identities": list(store.values()),
        "complete": True,
        "blocked": False,
        "error": None,
        "retry_after": None,
        "scratch": {
            "identities": list(store.values()),
            "identity_queue": [],
            "identity_complete": True,
        },
    }


# Alias used by a sibling identity-only / refine implementation.
fetch_identities = fetch_county_identities
