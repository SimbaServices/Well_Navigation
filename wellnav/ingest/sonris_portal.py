"""Load a SONRIS Data Portal CSV export into wells_la and permits_la.

The live well-history report is captcha-gated and the portal forbids automated
access. Official use is: pass the captcha in a browser, Actions > Download > CSV,
then run ``python -m wellnav.ingest load-sonris --file <export.csv>``.
"""

from __future__ import annotations

import csv
from pathlib import Path

from wellnav.coords import to_wgs84
from wellnav.ingest.classify import utcnow
from wellnav.ingest.la_permits import is_la_permit_only, parse_sonris_date, permit_from_well
from wellnav.ingest.la_refresh import LA_LAT_RANGE, LA_LON_RANGE, LA_PARISH
from wellnav.ingest.neighbors import _api10, _text, _well_row, la_serial_api
from wellnav.ingest.persist import upsert_permits, upsert_wells

ALIASES = {
    "wellserialnum": "serial",
    "wellserialnumber": "serial",
    "wellserial": "serial",
    "serialnum": "serial",
    "serialnumber": "serial",
    "wsn": "serial",
    "apinum": "api",
    "apinumber": "api",
    "api": "api",
    "wellname": "well_name",
    "wellnam": "well_name",
    "wellnum": "well_no",
    "wellnumber": "well_no",
    "wellno": "well_no",
    "organizationname": "operator",
    "orgopern": "operator",
    "orgopername": "operator",
    "operatorname": "operator",
    "operator": "operator",
    "organization": "operator",
    "organizati": "operator_number",
    "organizationid": "operator_number",
    "operatorid": "operator_number",
    "operatornumber": "operator_number",
    "parishname": "county",
    "parishnam": "county",
    "parish": "county",
    "parishcode": "county_code",
    "parishcod": "county_code",
    "fieldname": "field",
    "fieldnam": "field",
    "field": "field",
    "fieldid": "field",
    "luwname": "lease_name",
    "leasename": "lease_name",
    "leasenum": "lease_no",
    "leasenumber": "lease_no",
    "district": "district",
    "districtc": "district",
    "districtname": "district",
    "wellstatus": "symbol",
    "wellstatu": "symbol",
    "wellstatuscodedescription": "symbol",
    "status": "symbol",
    "wellclass": "well_type",
    "wellclasstypecodedescription": "well_type",
    "classification": "well_type",
    "producttype": "well_type",
    "producttypecodedescription": "well_type",
    "productty": "well_type",
    "welltype": "well_type",
    "stateleasenum": "lease_no",
    "surfacelatitude": "lat",
    "surfacelat": "lat",
    "latitude": "lat",
    "lat": "lat",
    "surfacelongitude": "lon",
    "surfacelon": "lon",
    "longitude": "lon",
    "long": "lon",
    "lon": "lon",
    "effectivedate": "as_of",
    "statusdate": "as_of",
    "historydate": "as_of",
    "permitdate": "as_of",
    "spuddate": "as_of",
}


def _norm_header(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


def _mapped(row: dict) -> dict:
    out: dict[str, str] = {}
    for key, value in row.items():
        alias = ALIASES.get(_norm_header(str(key or "")))
        if not alias:
            continue
        text = _text(value)
        if text and alias not in out:
            out[alias] = text
    return out


def _date_key(value: str) -> str:
    parsed = parse_sonris_date(value)
    if parsed:
        return parsed.replace("-", "")
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else value


def _raw_date(row: dict, *headers: str) -> str:
    wanted = {_norm_header(name) for name in headers}
    for key, value in row.items():
        if _norm_header(str(key or "")) in wanted:
            parsed = parse_sonris_date(_text(value))
            if parsed:
                return parsed
    return ""


def _coords(lat_raw: str, lon_raw: str) -> tuple[float, float] | None:
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    if lon > 0 and LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1]:
        lon = -lon
    try:
        point = to_wgs84(lon, lat, "nad27")
        lat, lon = point.lat, point.lon
    except Exception:
        pass
    if not (LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1] and LA_LON_RANGE[0] <= lon <= LA_LON_RANGE[1]):
        return None
    return lat, lon


def sonris_export_row_to_well(row: dict, used_apis: set[str] | None = None) -> dict | None:
    fields = _mapped(row)
    official = _api10(fields.get("api"), "la") if fields.get("api") else None
    serial = fields.get("serial")
    api = official
    if not api or (used_apis is not None and api in used_apis):
        api = la_serial_api(serial)
    if not api or (used_apis is not None and api in used_apis):
        return None
    coords = _coords(fields.get("lat") or "", fields.get("lon") or "")
    lat, lon = coords if coords else (None, None)
    name = fields.get("well_name") or ""
    number = fields.get("well_no") or ""
    if name and number and number not in name:
        name = f"{name} #{number}".strip(" #")
    county = fields.get("county") or ""
    if county.lower().endswith(" parish"):
        county = county[: -len(" parish")]
    parish_digits = "".join(ch for ch in fields.get("county_code") or "" if ch.isdigit())
    parish = parish_digits.zfill(3)[-3:] if len(parish_digits) >= 3 else ""
    if not county and parish in LA_PARISH:
        county = LA_PARISH[parish]
    well = _well_row(
        "la",
        api,
        well_name=name,
        well_no=number,
        lease_name=fields.get("lease_name") or serial,
        lease_no=fields.get("lease_no") or serial,
        operator=fields.get("operator"),
        operator_number=fields.get("operator_number"),
        county=county,
        district=fields.get("district"),
        field=fields.get("field"),
        well_type=fields.get("well_type"),
        symbol=fields.get("symbol") or fields.get("well_type"),
        lat=lat,
        lon=lon,
        source="la_sonris_portal",
    )
    if parish in LA_PARISH:
        well["county_code"] = parish
    if serial:
        well["location_source"] = f"la_serial:{serial}"
    well["_as_of"] = _date_key(fields.get("as_of") or "")
    well["_serial"] = serial or ""
    well["_approved_at"] = _raw_date(row, "Permit Date", "Date Permitted")
    well["_expires_at"] = _raw_date(
        row, "Expiration Date", "Expire Date", "Permit Expiration Date"
    )
    well["_spud_at"] = _raw_date(row, "Spud Date")
    well["_submitted_at"] = _raw_date(row, "Submitted Date", "Application Date")
    return well


def _choose(current: dict | None, incoming: dict) -> dict:
    if current is None:
        return incoming
    if incoming.get("_as_of") and incoming["_as_of"] >= (current.get("_as_of") or ""):
        return incoming
    if not current.get("operator") and incoming.get("operator"):
        return incoming
    return current


def iter_sonris_export(path: Path):
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t|")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        chosen: dict[str, dict] = {}
        for raw in reader:
            well = sonris_export_row_to_well(raw)
            if not well:
                continue
            key = well.get("_serial") or well["api"]
            chosen[key] = _choose(chosen.get(key), well)
    used: set[str] = set()
    for well in chosen.values():
        serial = well.pop("_serial", "")
        well.pop("_as_of", None)
        if well["api"] in used:
            alt = la_serial_api(serial)
            if not alt or alt in used:
                continue
            well["api"] = alt
            well["api8"] = alt[2:10]
        used.add(well["api"])
        yield well


def _flush_permits(conn, rows: list[dict]) -> None:
    if rows:
        upsert_permits(conn, "la", rows)
        rows.clear()


def load_sonris_export(conn, path: Path) -> dict:
    existing = {
        row[0]: row[1]
        for row in conn.execute("SELECT api, source FROM wells_la")
    }
    now = utcnow()
    added = 0
    patched = 0
    permits = 0
    skipped_bsee = 0
    batch: list[dict] = []
    permit_batch: list[dict] = []
    for well in iter_sonris_export(Path(path)):
        if is_la_permit_only(well):
            if existing.get(well["api"]) == "la_bsee":
                skipped_bsee += 1
                continue
            if well["api"] in existing:
                conn.execute("DELETE FROM wells_la WHERE api = ?", (well["api"],))
                existing.pop(well["api"], None)
            permit_batch.append(permit_from_well(well, now=now))
            permits += 1
            if len(permit_batch) >= 500:
                _flush_permits(conn, permit_batch)
            continue
        source = existing.get(well["api"])
        if source == "la_bsee":
            skipped_bsee += 1
            continue
        if source:
            conn.execute(
                """
                UPDATE wells_la
                SET well_name = COALESCE(NULLIF(?, ''), well_name),
                    operator = COALESCE(NULLIF(?, ''), operator),
                    operator_number = COALESCE(NULLIF(?, ''), operator_number),
                    lease_name = COALESCE(NULLIF(?, ''), lease_name),
                    lease_no = COALESCE(NULLIF(?, ''), lease_no),
                    county = COALESCE(NULLIF(?, ''), county),
                    field = COALESCE(NULLIF(?, ''), field),
                    well_type = COALESCE(NULLIF(?, ''), well_type),
                    symbol = COALESCE(NULLIF(?, ''), symbol),
                    wellhead_lat = COALESCE(?, wellhead_lat),
                    wellhead_lon = COALESCE(?, wellhead_lon),
                    source = CASE WHEN source IN ('la_fracfocus', 'la_sonris') THEN 'la_sonris_portal' ELSE source END,
                    updated_at = ?
                WHERE api = ?
                """,
                (
                    well["well_name"],
                    well["operator"],
                    well["operator_number"],
                    well["lease_name"],
                    well["lease_no"],
                    well["county"],
                    well["field"],
                    well["well_type"],
                    well["symbol"],
                    well["wellhead_lat"],
                    well["wellhead_lon"],
                    now,
                    well["api"],
                ),
            )
            patched += 1
            continue
        batch.append(well)
        existing[well["api"]] = well["source"]
        if len(batch) >= 500:
            upsert_wells(conn, "la", batch)
            added += len(batch)
            batch = []
    if batch:
        upsert_wells(conn, "la", batch)
        added += len(batch)
    _flush_permits(conn, permit_batch)
    from wellnav.ingest.la_operators import sync_louisiana_catalog

    catalog = sync_louisiana_catalog(conn)
    return {
        "added": added,
        "patched": patched,
        "permits": permits,
        "skipped_bsee": skipped_bsee,
        "catalog": catalog,
    }
