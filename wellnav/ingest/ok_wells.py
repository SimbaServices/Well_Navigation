"""Oklahoma oil and gas wells from every public OCC GIS inventory.

RBDMS_WELLS is the official OCC well file (~457k). Completeness gaps vs that
layer are filled from other OCC-hosted public FeatureServers:

- RBDMS_WELLS_SEARCH — same wells plus operator numbers
- IHS_AOR_WELLS — OCC Area-of-Review inventory (~574k), including wells
  that never made it into RBDMS (historical / Osage / pre-digital)
- COMP_WELLS — recent completions with operator numbers and bottom-holes
- ALL_UIC_WELLS — injection wells with operator numbers
- HF_LATERALS — hydraulic-fracture laterals (surface + bottom-hole)
- ITD_WELLS — intent-to-drill permits
- FracFocus disclosures — recent stimulated wells / operator names

OWRB groundwater wells are not oil and gas and are skipped. BIA NIOGEMS
Osage records are not public; IHS AOR and FracFocus are the public stand-ins.
"""

from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wellnav.coords import to_wgs84
from wellnav.db import ROOT
from wellnav.ingest.arcgis import iter_features
from wellnav.ingest.classify import utcnow
from wellnav.ingest.neighbors import (
    _api10,
    _coord,
    _text,
    _well_row,
    _xy,
    ok_feature_to_well,
)
from wellnav.ingest.persist import upsert_permits, upsert_wells
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS, OK_COUNTY_NAME, STATE_BBOX, permits_table, wells_table
from wellnav.well_names import normalize_well_identity

OK_RBDMS = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "RBDMS_WELLS_SEARCH/FeatureServer/292/query"
)
OK_IHS = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "IHS_AOR_WELLS/FeatureServer/274/query"
)
OK_COMP = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "COMP_WELLS/FeatureServer/331/query"
)
OK_UIC = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "ALL_UIC_WELLS/FeatureServer/249/query"
)
OK_HF = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "HF_LATERALS/FeatureServer/95/query"
)
OK_ITD = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "ITD_WELLS/FeatureServer/290/query"
)

UNASSIGNED = {"", "otc/occ not assigned", "unknown", "n/a", "none", "null"}
OK_BOX = STATE_BBOX["ok"]
CACHE_DIR = ROOT / "data" / "ok_refresh"
OCC_FILES = "https://oklahoma.gov/content/dam/ok/en/occ/documents/og"
RBDMS_CSV = f"{OCC_FILES}/ogdatafiles/rbdms-wells.csv"
ACTIVE_XLSX = f"{OCC_FILES}/ogdatafiles/online-active-well-list.xlsx"
TRANSFERS_XLSX = f"{OCC_FILES}/ogdatafiles/well-transfers-daily.xlsx"


def _log(message: str) -> None:
    print(message, flush=True)


def _in_oklahoma(lat: float | None, lon: float | None) -> bool:
    if lat is None or lon is None:
        return False
    return (
        OK_BOX["lat_min"] <= lat <= OK_BOX["lat_max"]
        and OK_BOX["lon_min"] <= lon <= OK_BOX["lon_max"]
    )


def _county(api: str, raw: object = None) -> str:
    text = _text(raw)
    if text:
        if "-" in text and text[:3].isdigit():
            text = text.split("-", 1)[-1]
        return text.replace(" County", "").strip().upper()
    return OK_COUNTY_NAME.get(api[2:5], "")


def _unassigned(value: object) -> bool:
    return _text(value).lower() in UNASSIGNED


def _nad27_wgs84(lat: float, lon: float) -> tuple[float, float]:
    point = to_wgs84(lon, lat, "nad27")
    return point.lat, point.lon


def _epoch_iso(value: object) -> str | None:
    raw = _text(value)
    if not raw or raw.startswith("1900"):
        return None
    try:
        number = float(raw)
    except ValueError:
        try:
            return datetime.strptime(raw[:10], "%Y-%m-%d").date().isoformat()
        except ValueError:
            return None
    if number <= 0:
        return None
    if number > 10_000_000_000:
        number /= 1000.0
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def ihs_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api_10") or attrs.get("api") or attrs.get("API"), "ok")
    lat = _coord(attrs.get("latitude"), attrs.get("LATITUDE"))
    lon = _coord(attrs.get("longitude"), attrs.get("LONGITUDE"))
    if lat is None or lon is None:
        lat, lon = _xy(feature)
    if not api or not _in_oklahoma(lat, lon):
        return None
    name = _text(attrs.get("well_name"), attrs.get("WELL_NAME"))
    return _well_row(
        "ok",
        api,
        well_name=name,
        operator="" if _unassigned(attrs.get("current_operator")) else attrs.get("current_operator"),
        county=_county(api),
        well_type=attrs.get("final_status") or attrs.get("form_at_td_name"),
        symbol=attrs.get("final_status") or attrs.get("plot_sym"),
        lat=lat,
        lon=lon,
        source="ok_occ_ihs",
    )


def comp_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api_number") or attrs.get("api"), "ok")
    lat = _coord(attrs.get("surf_lat_y"), attrs.get("sh_lat"))
    lon = _coord(attrs.get("surf_long_x"), attrs.get("sh_lon"))
    if lat is None or lon is None:
        lat, lon = _xy(feature)
    if not api or not _in_oklahoma(lat, lon):
        return None
    name = _text(attrs.get("well_name"))
    number = _text(attrs.get("well_number"), attrs.get("well_num"))
    toe_lat = _coord(attrs.get("bottom_hole_lat_y"))
    toe_lon = _coord(attrs.get("bottom_hole_long_x"))
    well = _well_row(
        "ok",
        api,
        well_name=name,
        well_no=number,
        operator=""
        if _unassigned(attrs.get("operator_name") or attrs.get("operator"))
        else (attrs.get("operator_name") or attrs.get("operator")),
        operator_number=attrs.get("operator_number"),
        county=_county(api, attrs.get("county")),
        field=attrs.get("formation_name"),
        well_type=attrs.get("well_type") or attrs.get("class_type"),
        symbol=attrs.get("well_status") or attrs.get("class_type"),
        lat=lat,
        lon=lon,
        source="ok_occ_comp",
    )
    if toe_lat is not None and toe_lon is not None:
        well["toe_lat"] = toe_lat
        well["toe_lon"] = toe_lon
        well["toe_crs"] = "EPSG:4326"
        well["profile"] = "horizontal"
        well["location_kind"] = "horizontal_or_directional"
    return well


def uic_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api") or attrs.get("API"), "ok")
    lat, lon = _xy(feature)
    if lat is None or lon is None:
        lat = _coord(attrs.get("lat"))
        lon = _coord(attrs.get("lon"))
    if not api or not _in_oklahoma(lat, lon):
        return None
    name = _text(attrs.get("well_name"))
    number = _text(attrs.get("well_num"))
    return _well_row(
        "ok",
        api,
        well_name=name,
        well_no=number,
        operator=""
        if _unassigned(attrs.get("op") or attrs.get("operator"))
        else (attrs.get("op") or attrs.get("operator")),
        operator_number=attrs.get("op_num") or attrs.get("operator_number"),
        county=_county(api, attrs.get("county")),
        field=attrs.get("formation_name"),
        well_type=attrs.get("well_type") or "UIC",
        symbol=attrs.get("status") or attrs.get("well_type"),
        lat=lat,
        lon=lon,
        source="ok_occ_uic",
    )


def hf_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api"), "ok")
    lat = _coord(attrs.get("shl_lat"))
    lon = _coord(attrs.get("shl_long"))
    toe_lat = _coord(attrs.get("bhl_lat"))
    toe_lon = _coord(attrs.get("bhl_long"))
    datum = _text(attrs.get("datum")).upper()
    if "27" in datum:
        if lat is not None and lon is not None:
            lat, lon = _nad27_wgs84(lat, lon)
        if toe_lat is not None and toe_lon is not None:
            toe_lat, toe_lon = _nad27_wgs84(toe_lat, toe_lon)
    if not api or not _in_oklahoma(lat, lon):
        return None
    name = _text(attrs.get("wellname"), attrs.get("well_name"))
    well = _well_row(
        "ok",
        api,
        well_name=name,
        operator="" if _unassigned(attrs.get("opname")) else attrs.get("opname"),
        operator_number=attrs.get("opnum"),
        county=_county(api, attrs.get("county")),
        field=attrs.get("fm"),
        well_type="horizontal",
        symbol="HF",
        lat=lat,
        lon=lon,
        source="ok_occ_hf",
    )
    if toe_lat is not None and toe_lon is not None:
        well["toe_lat"] = toe_lat
        well["toe_lon"] = toe_lon
        well["toe_crs"] = "EPSG:4326"
        well["profile"] = "horizontal"
        well["location_kind"] = "horizontal_or_directional"
    return well


def itd_feature_to_permit(feature: dict, *, now: str, lifetime_days: int) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api_number") or attrs.get("api"), "ok")
    lat = _coord(attrs.get("surf_lat_y"))
    lon = _coord(attrs.get("surf_long_x"))
    if not api or not _in_oklahoma(lat, lon):
        return None
    approved = _epoch_iso(attrs.get("approval_date")) or now[:10]
    expires = _epoch_iso(attrs.get("expire_date"))
    if not expires:
        try:
            expires = (
                datetime.fromisoformat(approved) + timedelta(days=lifetime_days)
            ).date().isoformat()
        except ValueError:
            expires = (
                datetime.now(timezone.utc) + timedelta(days=lifetime_days)
            ).date().isoformat()
    status_raw = _text(attrs.get("permit_status") or attrs.get("well_status")).upper()
    status = {
        "APPROVED": "approved",
        "ACCEPTED": "approved",
        "ACTIVE": "approved",
        "EXPIRED": "expired",
        "CANCELLED": "cancelled",
        "CANCELED": "cancelled",
        "REJECTED": "cancelled",
    }.get(status_raw, status_raw.lower() or "approved")
    name = _text(attrs.get("well_name"))
    number = _text(attrs.get("well_number"))
    well_name, well_no, lease_name = normalize_well_identity(name, number, name)
    permit_no = _text(attrs.get("receipt_number"), attrs.get("batch_id_number")) or f"ITD-{api[2:]}"
    return {
        "api": api,
        "api8": api[2:10],
        "permit_no": permit_no,
        "status": status,
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease_name,
        "lease_no": "",
        "county": _county(api, attrs.get("county")),
        "county_code": api[2:5],
        "district": "",
        "operator": ""
        if _unassigned(attrs.get("entity_name"))
        else attrs.get("entity_name"),
        "operator_number": attrs.get("operator_number"),
        "profile": _text(attrs.get("drill_type")).lower(),
        "symbol": attrs.get("well_class") or attrs.get("well_type"),
        "symnum": None,
        "wellhead_lat": lat,
        "wellhead_lon": lon,
        "wellhead_crs": "EPSG:4326",
        "approved_at": approved,
        "submitted_at": _epoch_iso(attrs.get("submit_date")),
        "expires_at": expires,
        "lifetime_days": lifetime_days,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "ok_occ_itd",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def rbdms_csv_to_well(row: dict) -> dict | None:
    api = _api10(row.get("API") or row.get("api"), "ok")
    lat = _coord(row.get("SH_LAT"), row.get("sh_lat"))
    lon = _coord(row.get("SH_LON"), row.get("sh_lon"))
    if not api or not _in_oklahoma(lat, lon):
        return None
    name = _text(row.get("WELL_NAME"), row.get("well_name"))
    number = _text(row.get("WELL_NUM"), row.get("well_num"))
    operator = row.get("OPERATOR") or row.get("operator")
    return _well_row(
        "ok",
        api,
        well_name=name,
        well_no=number,
        operator="" if _unassigned(operator) else operator,
        county=_county(api, row.get("COUNTY") or row.get("county")),
        well_type=row.get("WELLTYPE") or row.get("well_type"),
        symbol=row.get("WELLSTATUS") or row.get("SYMBOL_CLASS") or row.get("wellstatus"),
        lat=lat,
        lon=lon,
        source="ok_occ",
    )


def active_list_to_well(row: dict) -> dict | None:
    api = _api10(row.get("API#") or row.get("API") or row.get("api"), "ok")
    lat = _coord(row.get("LAT"), row.get("lat"))
    lon = _coord(row.get("LONG"), row.get("lon"))
    if not api:
        return None
    if lat is not None and lon is not None and not _in_oklahoma(lat, lon):
        return None
    name = _text(row.get("WellName"), row.get("well_name"))
    number = _text(row.get("WellNumber"), row.get("well_no"))
    operator = row.get("Operator") or row.get("operator")
    well = _well_row(
        "ok",
        api,
        well_name=name,
        well_no=number,
        operator="" if _unassigned(operator) else operator,
        operator_number=row.get("Op. No.") or row.get("operator_number"),
        county=_county(api, row.get("County") or row.get("county")),
        well_type=row.get("WellType") or row.get("well_type"),
        symbol=row.get("WellType") or "ACTIVE",
        lat=lat,
        lon=lon,
        source="ok_occ_active",
    )
    if lat is None:
        well["wellhead_lat"] = None
        well["wellhead_lon"] = None
        well["gis_lat83"] = None
        well["gis_long83"] = None
    return well


def transfer_to_identity(row: dict) -> dict | None:
    api = _api10(row.get("API Number") or row.get("API") or row.get("api"), "ok")
    if not api:
        return None
    operator = row.get("ToOperatorName") or row.get("to_operator")
    number = _text(row.get("ToOperatorNumber") or row.get("to_operator_number"))
    if _unassigned(operator) and not number:
        return None
    return {
        "api": api,
        "operator": "" if _unassigned(operator) else operator,
        "operator_number": number,
        "well_name": _text(row.get("WellName"), row.get("well_name")),
        "well_no": _text(row.get("WellNum"), row.get("well_no")),
    }


def search_identity(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api") or attrs.get("API"), "ok")
    if not api:
        return None
    operator = attrs.get("operator") or attrs.get("op")
    return {
        "api": api,
        "operator": "" if _unassigned(operator) else operator,
        "operator_number": _text(
            attrs.get("operator_num"), attrs.get("op_num"), attrs.get("operator_number")
        ),
        "well_name": _text(attrs.get("well_name"), attrs.get("name")),
        "well_no": _text(attrs.get("well_num"), attrs.get("well_no")),
        "county": _text(attrs.get("county")),
    }


def _flush(conn, state: str, batch: list[dict], total: int, label: str) -> tuple[list[dict], int]:
    if not batch:
        return batch, total
    upsert_wells(conn, state, batch)
    conn.commit()
    total += len(batch)
    _log(f"{label} {total}")
    return [], total


def _existing_apis(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT api FROM wells_ok")}


def _load_mapped(
    conn,
    url: str,
    mapper,
    *,
    existing: set[str],
    delay: float,
    limit: int,
    label: str,
) -> int:
    batch: list[dict] = []
    added = 0
    for feature in iter_features(url, delay=delay, page_size=2000):
        row = mapper(feature)
        if not row or row["api"] in existing:
            continue
        existing.add(row["api"])
        batch.append(row)
        if len(batch) >= 1000:
            batch, added = _flush(conn, "ok", batch, added, label)
        if limit and added + len(batch) >= limit:
            break
    batch, added = _flush(conn, "ok", batch, added, label)
    return added


def _enrich_identity(conn, row: dict) -> int:
    api = row["api"]
    current = conn.execute(
        """
        SELECT operator, operator_number, field, well_name, well_no, lease_name, county,
               toe_lat, toe_lon, profile, location_kind
        FROM wells_ok WHERE api = ?
        """,
        (api,),
    ).fetchone()
    if current is None:
        return 0
    operator = row.get("operator") or ""
    if _unassigned(operator):
        operator = current["operator"] or ""
    elif not _unassigned(current["operator"]):
        operator = current["operator"] or operator
    operator_number = _text(row.get("operator_number")) or current["operator_number"] or ""
    field = _text(row.get("field")) or current["field"] or ""
    well_name, well_no, lease_name = normalize_well_identity(
        _text(row.get("well_name")) or current["well_name"] or "",
        _text(row.get("well_no")) or current["well_no"] or "",
        _text(row.get("lease_name")) or current["lease_name"] or "",
    )
    county = _text(row.get("county")) or current["county"] or ""
    toe_lat = row.get("toe_lat") if row.get("toe_lat") is not None else current["toe_lat"]
    toe_lon = row.get("toe_lon") if row.get("toe_lon") is not None else current["toe_lon"]
    profile = _text(row.get("profile")) or current["profile"] or ""
    location_kind = _text(row.get("location_kind")) or current["location_kind"] or ""
    if (
        operator == (current["operator"] or "")
        and operator_number == (current["operator_number"] or "")
        and field == (current["field"] or "")
        and well_name == (current["well_name"] or "")
        and well_no == (current["well_no"] or "")
        and lease_name == (current["lease_name"] or "")
        and toe_lat == current["toe_lat"]
        and toe_lon == current["toe_lon"]
    ):
        return 0
    conn.execute(
        """
        UPDATE wells_ok
        SET operator = ?, operator_number = ?, field = ?, well_name = ?,
            well_no = ?, lease_name = ?, county = ?, toe_lat = ?, toe_lon = ?, toe_crs = ?,
            profile = ?, location_kind = ?, updated_at = ?
        WHERE api = ?
        """,
        (
            operator,
            operator_number,
            field,
            well_name,
            well_no,
            lease_name,
            county,
            toe_lat,
            toe_lon,
            "EPSG:4326" if toe_lat is not None else None,
            profile,
            location_kind,
            utcnow(),
            api,
        ),
    )
    return 1


def _enrich_layer(conn, url: str, mapper, *, existing: set[str], delay: float, label: str) -> dict:
    added = 0
    patched = 0
    batch: list[dict] = []
    for feature in iter_features(url, delay=delay, page_size=2000):
        row = mapper(feature)
        if not row:
            continue
        if row["api"] in existing:
            patched += _enrich_identity(conn, row)
            continue
        existing.add(row["api"])
        batch.append(row)
        if len(batch) >= 1000:
            batch, added = _flush(conn, "ok", batch, added, label)
    batch, added = _flush(conn, "ok", batch, added, label)
    conn.commit()
    _log(f"{label} added {added} patched {patched}")
    return {"added": added, "patched": patched}


def _load_fracfocus(conn, existing: set[str]) -> dict:
    try:
        from wellnav.ingest.la_refresh import CACHE_DIR as LA_CACHE
        from wellnav.ingest.la_refresh import FRACFOCUS_ZIP, download_file
    except Exception as exc:
        _log(f"ok fracfocus skipped: {exc}")
        return {"added": 0, "patched": 0}
    try:
        zip_path = download_file(FRACFOCUS_ZIP, LA_CACHE / "fracfocuscsv.zip")
    except Exception as exc:
        _log(f"ok fracfocus download skipped: {exc}")
        return {"added": 0, "patched": 0}

    import csv
    import io
    import zipfile

    from wellnav.ingest.la_refresh import _col, _date_key

    latest: dict[str, tuple[str, dict]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
            and Path(name).name.lower().startswith(("disclosurelist", "registryupload"))
        ]
        for name in names:
            with archive.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                for row in csv.DictReader(text):
                    api_digits = "".join(ch for ch in _col(row, "APINumber", "api") if ch.isdigit())
                    state = _col(row, "StateName", "StateNumber").lower()
                    if not api_digits.startswith("35") and not state.startswith("okla"):
                        continue
                    api = _api10(api_digits, "ok")
                    if not api:
                        continue
                    try:
                        lat = float(_col(row, "Latitude", "lat"))
                        lon = float(_col(row, "Longitude", "lon"))
                    except (TypeError, ValueError):
                        continue
                    lon = -abs(lon) if lon > 0 else lon
                    proj = _col(row, "Projection").lower()
                    try:
                        if "27" in proj:
                            lat, lon = _nad27_wgs84(lat, lon)
                        elif "83" in proj:
                            point = to_wgs84(lon, lat, "nad83")
                            lat, lon = point.lat, point.lon
                    except Exception:
                        pass
                    if not _in_oklahoma(lat, lon):
                        continue
                    well = _well_row(
                        "ok",
                        api,
                        well_name=_col(row, "WellName", "well_name"),
                        operator=_col(row, "OperatorName", "operator"),
                        county=_county(api, _col(row, "CountyName", "county")),
                        well_type=_col(row, "ProductionType") or "stimulated",
                        symbol="FracFocus",
                        lat=lat,
                        lon=lon,
                        source="ok_fracfocus",
                    )
                    stamp = _date_key(_col(row, "JobEndDate", "JobStartDate"))
                    prev = latest.get(api)
                    if prev is None or stamp >= prev[0]:
                        latest[api] = (stamp, well)
    added = 0
    patched = 0
    batch: list[dict] = []
    for _, well in latest.values():
        if well["api"] in existing:
            patched += _enrich_identity(conn, well)
            continue
        existing.add(well["api"])
        batch.append(well)
        if len(batch) >= 500:
            batch, added = _flush(conn, "ok", batch, added, "ok fracfocus")
    batch, added = _flush(conn, "ok", batch, added, "ok fracfocus")
    conn.commit()
    _log(f"ok fracfocus added {added} patched {patched}")
    return {"added": added, "patched": patched}


def _load_rbdms_csv(conn, *, limit: int = 0) -> int:
    from wellnav.ingest.ok_operators import download_occ_file

    path = download_occ_file(RBDMS_CSV, CACHE_DIR / "rbdms-wells.csv", timeout=300)
    existing = _existing_apis(conn)
    batch: list[dict] = []
    added = 0
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        for row in csv.DictReader(handle):
            well = rbdms_csv_to_well(row)
            if not well or well["api"] in existing:
                continue
            existing.add(well["api"])
            batch.append(well)
            if len(batch) >= 1000:
                batch, added = _flush(conn, "ok", batch, added, "ok rbdms csv")
            if limit and added + len(batch) >= limit:
                break
    batch, added = _flush(conn, "ok", batch, added, "ok rbdms csv")
    return added


def _enrich_active_list(conn, existing: set[str]) -> dict:
    from wellnav.ingest.ok_operators import download_occ_file
    from wellnav.ingest.xlsx import iter_xlsx_dicts

    path = download_occ_file(ACTIVE_XLSX, CACHE_DIR / "online-active-well-list.xlsx")
    added = 0
    patched = 0
    batch: list[dict] = []
    for row in iter_xlsx_dicts(path):
        well = active_list_to_well(row)
        if not well:
            continue
        if well["api"] in existing:
            patched += _enrich_identity(conn, well)
            continue
        if well.get("wellhead_lat") is None:
            continue
        existing.add(well["api"])
        batch.append(well)
        if len(batch) >= 500:
            batch, added = _flush(conn, "ok", batch, added, "ok active list")
    batch, added = _flush(conn, "ok", batch, added, "ok active list")
    conn.commit()
    _log(f"ok active list added {added} patched {patched}")
    return {"added": added, "patched": patched}


def _enrich_transfers(conn) -> int:
    from wellnav.ingest.ok_operators import download_occ_file
    from wellnav.ingest.xlsx import iter_xlsx_dicts

    path = download_occ_file(TRANSFERS_XLSX, CACHE_DIR / "well-transfers-daily.xlsx")
    patched = 0
    for row in iter_xlsx_dicts(path):
        ident = transfer_to_identity(row)
        if ident:
            patched += _enrich_identity(conn, ident)
    conn.commit()
    _log(f"ok transfers patched {patched}")
    return patched


def _enrich_search_operators(conn, *, delay: float) -> int:
    patched = 0
    seen = 0
    for feature in iter_features(
        OK_RBDMS,
        delay=delay,
        page_size=2000,
        out_fields="api,operator,operator_num,well_name,well_num,county",
        return_geometry=False,
    ):
        ident = search_identity(feature)
        if ident:
            patched += _enrich_identity(conn, ident)
        seen += 1
        if seen % 50000 == 0:
            conn.commit()
            _log(f"ok search operators scanned {seen} patched {patched}")
    conn.commit()
    _log(f"ok search operators patched {patched}")
    return patched


def _load_itd_permits(conn, *, delay: float, limit: int = 0) -> int:
    now = utcnow()
    permits: list[dict] = []
    added = 0
    _log("ok intent-to-drill permits")
    for feature in iter_features(OK_ITD, delay=delay, page_size=2000):
        row = itd_feature_to_permit(feature, now=now, lifetime_days=DEFAULT_PERMIT_LIFETIME_DAYS)
        if not row:
            continue
        permits.append(row)
        if len(permits) >= 1000:
            upsert_permits(conn, "ok", permits)
            conn.commit()
            added += len(permits)
            permits = []
        if limit and added + len(permits) >= limit:
            break
    if permits:
        upsert_permits(conn, "ok", permits)
        conn.commit()
        added += len(permits)
    return added


def migrate_ok_permits(conn) -> int:
    """Mark Oklahoma permits as migrated once the well appears in wells_ok."""
    now = utcnow()
    cur = conn.execute(
        f"""
        UPDATE {permits_table("ok")}
        SET status = 'migrated', migrated_at = ?, as_drilled_ready = 1, updated_at = ?
        WHERE status NOT IN ('migrated', 'expired', 'cancelled')
          AND api8 IN (SELECT api8 FROM {wells_table("ok")})
        """,
        (now, now),
    )
    conn.commit()
    return cur.rowcount


def refresh_ok_permits(*, delay: float = 0.12, limit: int = 0, db_path=None) -> dict:
    """Refresh permits_ok from the public OCC intent-to-drill inventory."""
    from wellnav.db import connect, init_schema, set_cursor

    conn = connect(db_path)
    init_schema(conn)
    added = _load_itd_permits(conn, delay=delay, limit=limit)
    migrated = migrate_ok_permits(conn)
    now = utcnow()
    expired = conn.execute(
        f"""
        UPDATE {permits_table("ok")}
        SET status = 'expired', updated_at = ?
        WHERE status IN ('approved', 'validated')
          AND expires_at IS NOT NULL
          AND expires_at < ?
        """,
        (now, now),
    ).rowcount
    set_cursor(conn, "ok_permits_refreshed_at", now, now)
    conn.commit()
    conn.close()
    stats = {"permits": added, "migrated": migrated, "expired": expired, "status": "ok"}
    _log(f"ok permits refresh {stats}")
    return stats


def load_ok_wells(conn, *, delay: float = 0.12, limit: int = 0) -> dict:
    """Load public OCC well, permit, and operator inventories into Oklahoma tables."""
    stats = {
        "rbdms": 0,
        "ihs": 0,
        "active": {"added": 0, "patched": 0},
        "comp": {"added": 0, "patched": 0},
        "uic": {"added": 0, "patched": 0},
        "hf": {"added": 0, "patched": 0},
        "fracfocus": {"added": 0, "patched": 0},
        "search_ops": 0,
        "transfers": 0,
        "permits": 0,
        "migrated": 0,
        "operators": {},
        "wells": 0,
    }
    _log("ok rbdms nightly csv")
    stats["rbdms"] = _load_rbdms_csv(conn, limit=limit)
    existing = _existing_apis(conn)
    extra_limit = 0 if not limit else max(0, limit - stats["rbdms"])
    if not limit or extra_limit:
        _log("ok ihs aor wells")
        stats["ihs"] = _load_mapped(
            conn,
            OK_IHS,
            ihs_feature_to_well,
            existing=existing,
            delay=delay,
            limit=extra_limit,
            label="ok ihs",
        )
    if limit:
        from wellnav.ingest.ok_operators import load_operators_ok_into

        stats["operators"] = load_operators_ok_into(conn)
        stats["wells"] = stats["rbdms"] + stats["ihs"]
        _log(f"ok wells limited {stats}")
        return stats

    stats["active"] = _enrich_active_list(conn, existing)
    stats["comp"] = _enrich_layer(
        conn, OK_COMP, comp_feature_to_well, existing=existing, delay=delay, label="ok comp"
    )
    stats["uic"] = _enrich_layer(
        conn, OK_UIC, uic_feature_to_well, existing=existing, delay=delay, label="ok uic wells"
    )
    stats["hf"] = _enrich_layer(
        conn, OK_HF, hf_feature_to_well, existing=existing, delay=delay, label="ok laterals"
    )
    stats["fracfocus"] = _load_fracfocus(conn, existing)
    stats["search_ops"] = _enrich_search_operators(conn, delay=delay)
    stats["transfers"] = _enrich_transfers(conn)
    stats["permits"] = _load_itd_permits(conn, delay=delay)
    stats["migrated"] = migrate_ok_permits(conn)

    from wellnav.ingest.ok_operators import load_operators_ok_into

    stats["operators"] = load_operators_ok_into(conn)
    stats["wells"] = (
        stats["rbdms"]
        + stats["ihs"]
        + stats["active"]["added"]
        + stats["comp"]["added"]
        + stats["uic"]["added"]
        + stats["hf"]["added"]
        + stats["fracfocus"]["added"]
    )
    _log(f"ok wells done {stats}")
    return stats
