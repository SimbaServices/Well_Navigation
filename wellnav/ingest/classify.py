"""Turn GIS features into wellhead-aware well or permit records."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from wellnav.coords import to_wgs84
from wellnav.gis import _point_from_feature, _serialize_point
from wellnav.parsers import format_api
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, PERMIT_SYMNUMS, TX_COUNTY_NAME


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _attrs(feature: dict) -> dict:
    return feature.get("attributes") or {}


def feature_point(feature: dict) -> dict | None:
    point = _point_from_feature(feature)
    return _serialize_point(point) if point else None


def merge_county_features(
    defaults: list[dict],
    surfaces: list[dict],
    *,
    county_code: str,
    county_name: str,
    lifetime_days: int = DEFAULT_PERMIT_LIFETIME_DAYS,
    now: str | None = None,
) -> tuple[list[dict], list[dict]]:
    now = now or utcnow()
    expires = (
        datetime.fromisoformat(now) + timedelta(days=lifetime_days)
    ).isoformat()
    by_api: dict[str, dict] = {}

    for feat in defaults:
        attrs = _attrs(feat)
        api8 = (attrs.get("API") or "").strip()
        if len(api8) != 8:
            continue
        by_api[api8] = {"default": feat, "surface": None}

    for feat in surfaces:
        attrs = _attrs(feat)
        api8 = (attrs.get("API") or "").strip()
        if len(api8) != 8:
            continue
        by_api.setdefault(api8, {"default": None, "surface": None})["surface"] = feat

    wells: list[dict] = []
    permits: list[dict] = []
    for api8, pair in by_api.items():
        record = build_record(
            api8,
            pair.get("default"),
            pair.get("surface"),
            county_code=county_code,
            county_name=county_name,
            now=now,
            expires_at=expires,
            lifetime_days=lifetime_days,
        )
        if record["bucket"] == "permit":
            permits.append(record)
        else:
            wells.append(record)
    return wells, permits


def build_record(
    api8: str,
    default_feat: dict | None,
    surface_feat: dict | None,
    *,
    county_code: str,
    county_name: str,
    now: str,
    expires_at: str,
    lifetime_days: int,
    identity: dict | None = None,
) -> dict:
    default_attrs = _attrs(default_feat) if default_feat else {}
    surface_attrs = _attrs(surface_feat) if surface_feat else {}
    attrs = default_attrs or surface_attrs
    surface_pt = feature_point(surface_feat) if surface_feat else None
    default_pt = feature_point(default_feat) if default_feat else None

    if surface_pt and default_pt:
        wellhead, toe, kind = surface_pt, default_pt, "horizontal_or_directional"
    elif surface_pt:
        wellhead, toe, kind = surface_pt, None, "surface_only"
    else:
        wellhead, toe, kind = default_pt, default_pt, "vertical"

    symnum = attrs.get("SYMNUM")
    try:
        symnum_i = int(symnum) if symnum is not None else None
    except (TypeError, ValueError):
        symnum_i = None
    symbol = attrs.get("GIS_SYMBOL_DESCRIPTION") or surface_attrs.get("GIS_SYMBOL_DESCRIPTION")
    well_no = attrs.get("GIS_WELL_NUMBER") or ""
    bucket = "permit" if symnum_i in PERMIT_SYMNUMS else "well"
    status = "cancelled" if symnum_i == 9 else "approved" if bucket == "permit" else "as_drilled"

    lat83 = attrs.get("GIS_LAT83") or surface_attrs.get("GIS_LAT83")
    lon83 = attrs.get("GIS_LONG83") or surface_attrs.get("GIS_LONG83")
    if wellhead is None and lat83 not in (None, "") and lon83 not in (None, ""):
        wellhead = _serialize_point(to_wgs84(float(lon83), float(lat83), "nad83"))

    county = county_name or TX_COUNTY_NAME.get(county_code, "")
    ident = identity or {}
    ident_well_no = (ident.get("well_no") or "").strip()
    ident_lease = (ident.get("lease_name") or "").strip()
    well_no = ident_well_no or well_no
    if ident.get("well_name"):
        well_name = ident["well_name"]
    elif ident_lease:
        well_name = f"{ident_lease} #{well_no}".strip(" #") if well_no else ident_lease
    else:
        well_name = f"{county} #{well_no}".strip(" #") if well_no else format_api(api8)

    return {
        "bucket": bucket,
        "api": f"42{api8}",
        "api8": api8,
        "permit_no": f"GIS-{api8}" if bucket == "permit" else None,
        "status": status,
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": ident_lease,
        "lease_no": (ident.get("lease_no") or "").strip(),
        "county": (ident.get("county") or "").strip() or county,
        "county_code": county_code,
        "district": (ident.get("district") or "").strip(),
        "operator": (ident.get("operator") or "").strip(),
        "operator_number": (ident.get("operator_number") or "").strip(),
        "field": (ident.get("field") or "").strip(),
        "well_type": symbol or "",
        "symbol": symbol,
        "symnum": symnum_i,
        "profile": "horizontal" if kind == "horizontal_or_directional" else "vertical",
        "wellhead_lat": wellhead["lat"] if wellhead else None,
        "wellhead_lon": wellhead["lon"] if wellhead else None,
        "wellhead_crs": wellhead["source_label"] if wellhead else None,
        "toe_lat": toe["lat"] if toe and kind != "vertical" else None,
        "toe_lon": toe["lon"] if toe and kind != "vertical" else None,
        "toe_crs": toe["source_label"] if toe and kind != "vertical" else None,
        "location_kind": kind,
        "location_source": surface_attrs.get("GIS_LOCATION_SOURCE")
        or attrs.get("GIS_LOCATION_SOURCE"),
        "gis_lat83": lat83,
        "gis_long83": lon83,
        "gis_lat27": attrs.get("GIS_LAT27") or surface_attrs.get("GIS_LAT27"),
        "gis_long27": attrs.get("GIS_LONG27") or surface_attrs.get("GIS_LONG27"),
        "source": "rrc_gis",
        "approved_at": now if bucket == "permit" else None,
        "submitted_at": None,
        "expires_at": expires_at if bucket == "permit" else None,
        "lifetime_days": lifetime_days,
        "as_drilled_ready": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
