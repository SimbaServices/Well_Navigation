"""County-partitioned RRC wellbore identity (lease, district, operator)."""

from __future__ import annotations

from wellnav.http_client import BlockedRequest
from wellnav.ingest.limiter import acquire
from wellnav.rrc import RESULT_CAP, RrcClient
from wellnav.states import TX_DISTRICTS

PAGE_SIZE = 100
VIEW_ALL = -1
WELL_TYPES = (
    "PR", "SH", "IN", "NP", "TA", "AB", "HI", "OB", "PP", "SD",
    "BM", "DW", "GJ", "GL", "GT", "GW", "LP", "LU", "OS", "PF",
    "RT", "SM", "TR", "WS", "ZZ",
)
IDENTITY_KEYS = (
    "lease_name",
    "lease_no",
    "district",
    "operator",
    "operator_number",
    "well_name",
    "well_no",
    "field",
)
IDENTITY_FIELDS = IDENTITY_KEYS


class TooBroad(Exception):
    """EWA refused the query or hit the result cap; caller should refine."""


def apply_identity(record: dict, ident: dict | None) -> dict:
    if not ident:
        return record
    for key in IDENTITY_KEYS:
        value = (ident.get(key) or "").strip()
        if value:
            record[key] = value
    lease = (record.get("lease_name") or "").strip()
    well_no = (record.get("well_no") or "").strip()
    if lease or (well_no and ident.get("lease_name")):
        record["well_name"] = f"{lease} #{well_no}".strip(" #") or record.get("well_name")
    return record


def apply_identities(records: list[dict], by_api: dict[str, dict]) -> int:
    matched = 0
    for record in records:
        ident = by_api.get(record.get("api8") or "")
        if ident:
            apply_identity(record, ident)
            matched += 1
    return matched


def fetch_county_identities(
    county_code: str,
    *,
    delay: float = 0.15,
    client: RrcClient | None = None,
    scratch: dict | None = None,
) -> dict:
    """Page EWA wellbore results for one county, refining when the query is too wide."""
    client = client or RrcClient()
    code = (county_code or "").zfill(3)
    state = dict(scratch or {})
    identities: dict[str, dict] = dict(state.get("identities") or {})
    done = set(state.get("identity_done") or [])
    pending = list(state.get("identity_pending") or _initial_splits(code))

    try:
        while pending:
            spec = pending.pop(0)
            key = _spec_key(spec)
            if key in done:
                continue
            try:
                added = _page_split(client, code, spec, identities, delay=delay)
                if added >= RESULT_CAP:
                    raise TooBroad(f"{added} results at cap for {key}")
                done.add(key)
            except TooBroad:
                refined = _refine(spec)
                if not refined:
                    done.add(key)
                    continue
                pending = refined + pending
        return {
            "ok": True,
            "blocked": False,
            "identities": identities,
            "error": None,
            "retry_after": None,
            "scratch": {"identities": identities, "identity_done": sorted(done), "identity_pending": []},
        }
    except BlockedRequest as exc:
        return {
            "ok": False,
            "blocked": True,
            "identities": identities,
            "error": str(exc),
            "retry_after": exc.retry_after,
            "scratch": {
                "identities": identities,
                "identity_done": sorted(done),
                "identity_pending": pending,
            },
        }


def _initial_splits(county_code: str) -> list[dict]:
    # One Both query: small counties page in a single split; large counties
    # come back over_limit / Ewa_123 and jump to Y/N × O/G.
    return [
        {
            "county_code": county_code,
            "schedule": "Both",
            "lease_type": "",
            "well_type": "",
            "district": "",
            "api_prefix": "",
        }
    ]


def initial_spec() -> dict:
    return _initial_splits("")[0]


def _spec_key(spec: dict) -> str:
    return "|".join(
        (
            spec.get("schedule") or "",
            spec.get("lease_type") or "",
            spec.get("well_type") or "",
            spec.get("district") or "",
            spec.get("api_prefix") or "",
        )
    )


def _refine(spec: dict) -> list[dict]:
    schedule = spec.get("schedule") or ""
    if schedule in {"", "Both"} and not spec.get("lease_type"):
        return [
            {**spec, "schedule": sch, "lease_type": kind, "well_type": "", "district": "", "api_prefix": ""}
            for sch in ("Y", "N")
            for kind in ("O", "G")
        ]
    if not spec.get("lease_type"):
        return [{**spec, "lease_type": kind} for kind in ("O", "G")]
    # On-schedule oil/gas splits by well type; off-schedule has no well-type codes.
    if schedule != "N" and not spec.get("well_type"):
        return [{**spec, "well_type": well_type} for well_type in WELL_TYPES]
    if not spec.get("district"):
        return [{**spec, "district": district} for district in TX_DISTRICTS]
    if not spec.get("well_type"):
        return [{**spec, "well_type": well_type} for well_type in WELL_TYPES]
    if not spec.get("api_prefix"):
        county = spec.get("county_code") or ""
        return [{**spec, "api_prefix": f"{county}{digit}"} for digit in "0123456789"]
    return []


refine = _refine


def _store(identities: dict[str, dict], wells: list[dict]) -> int:
    added = 0
    for well in wells:
        api8 = (well.get("api") or "").strip()
        if len(api8) == 8:
            identities[api8] = well
            added += 1
    return added


def _fetch_page(
    client: RrcClient,
    county_code: str,
    spec: dict,
    *,
    delay: float,
    page_size: int,
    offset: int,
) -> dict | None:
    acquire(delay)
    try:
        page = client.search_wellbores(
            county_code=county_code,
            schedule=spec.get("schedule") or "Both",
            lease_type=spec.get("lease_type") or "",
            well_type=spec.get("well_type") or "",
            district=spec.get("district") or "",
            api_prefix=spec.get("api_prefix") or "",
            page_size=page_size,
            offset=offset,
        )
    except RuntimeError as exc:
        if _looks_too_broad(str(exc), None):
            return {"wells": [], "total": RESULT_CAP, "over_limit": True, "no_results": False}
        raise
    return page


def _page_split(
    client: RrcClient,
    county_code: str,
    spec: dict,
    identities: dict[str, dict],
    *,
    delay: float,
) -> int:
    first = _fetch_page(client, county_code, spec, delay=delay, page_size=PAGE_SIZE, offset=0)
    if first is None:
        raise TooBroad(f"too broad for {county_code} {_spec_key(spec)}")
    total = int(first.get("total") or 0)
    added = _store(identities, first.get("wells") or [])
    if first.get("over_limit") or total >= RESULT_CAP:
        raise TooBroad(f"over limit for {county_code} {_spec_key(spec)} total={total}")
    if first.get("no_results"):
        return added
    if added == 0 and total == 0 and _coarse_enough_to_refine(spec) and _refine(spec):
        raise TooBroad(f"empty coarse query for {county_code} {_spec_key(spec)}")
    if first.get("pager") and total and int(first.get("end") or 0) >= total:
        return added
    if not first.get("wells"):
        return added
    offset = int(first.get("end") or added)
    while True:
        page = _fetch_page(
            client, county_code, spec, delay=delay, page_size=PAGE_SIZE, offset=offset
        )
        if page is None:
            raise TooBroad(f"page failed for {county_code} {_spec_key(spec)} offset={offset}")
        wells = page.get("wells") or []
        added += _store(identities, wells)
        if page.get("over_limit") or int(page.get("total") or 0) >= RESULT_CAP:
            raise TooBroad(f"page over limit for {county_code} {_spec_key(spec)} offset={offset}")
        if page.get("pager"):
            end = int(page.get("end") or 0)
            page_total = int(page.get("total") or total)
            if page_total and end >= page_total:
                return added
            if not wells:
                return added
            offset = end
            continue
        if not wells or len(wells) < PAGE_SIZE:
            return added
        offset += len(wells)


def _coarse_enough_to_refine(spec: dict) -> bool:
    """Empty results on these queries are usually EWA hiding an oversize set."""
    if spec.get("schedule") == "Both":
        return True
    if not spec.get("lease_type"):
        return True
    # Oil is the slice that EWA blanks out when the set is too large.
    if spec.get("lease_type") == "O" and _refine(spec):
        return True
    return False


def _looks_too_broad(message: str, page: dict | None) -> bool:
    lower = (message or "").lower()
    if any(token in lower for token in ("application error", "too many", "refine", "narrow", "ewa_123")):
        return True
    if page is not None and int(page.get("total") or 0) >= RESULT_CAP:
        return True
    return False
