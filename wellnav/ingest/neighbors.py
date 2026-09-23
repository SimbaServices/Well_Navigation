"""Load New Mexico, Oklahoma, and Louisiana wells, waste sites, and pipelines.

Oklahoma wells come from every public OCC inventory (RBDMS search, IHS AOR,
completions, UIC, laterals, intent-to-drill, FracFocus). See ok_wells.py.
"""

from __future__ import annotations

from pathlib import Path

from wellnav.db import connect, init_schema, set_meta
from wellnav.disposal import connect as disposal_connect
from wellnav.disposal import init_schema as init_disposal
from wellnav.ingest.arcgis import iter_features
from wellnav.ingest.classify import utcnow
from wellnav.ingest.neighbor_pipelines import load_neighbor_pipelines
from wellnav.ingest.persist import upsert_wells
from wellnav.pipelines import connect as pipe_connect
from wellnav.pipelines import init_schema as init_pipelines
from wellnav.operators import normalize_operator_name
from wellnav.states import APP_STATES, api_prefix
from wellnav.well_names import normalize_well_identity

NM_WELLS = (
    "https://gis.emnrd.nm.gov/arcgis/rest/services/OCDView/"
    "Wells_Public/FeatureServer/0/query"
)
NM_WELLS_FALLBACK = (
    "https://services5.arcgis.com/f4lpEvI6fkgVYigk/ArcGIS/rest/services/"
    "New_Mexico_Oil_and_Gas_Wells__Nov2024/FeatureServer/30/query"
)
OK_WELLS = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/"
    "RBDMS_WELLS_SEARCH/FeatureServer/292/query"
)
LA_WELLS = (
    "https://maps.dotd.la.gov/ltrcserver/rest/services/LTRC_18_3GT/SONRIS/MapServer/0/query"
)
OK_UIC = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/COMMERCIAL_UIC_WELLS/"
    "FeatureServer/254/query"
)
OK_PITS = (
    "https://gis.occ.ok.gov/server/rest/services/Hosted/Comm_Recycling_Pit_Facilities/"
    "FeatureServer/330/query"
)
DISPOSAL_ID_BASE = {"nm": 2_000_000_000, "ok": 2_100_000_000, "la": 2_200_000_000}
NM_WASTE_TYPES = {"salt water disposal", "swd", "disposal"}


_UNASSIGNED = {"", "otc/occ not assigned", "unknown", "n/a", "none", "null"}


def _assigned_operator(*values: object) -> str:
    text = _text(*values)
    return "" if text.lower() in _UNASSIGNED else text


def _text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "nan"}:
            return text
    return ""


def _digits(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _coord(*values: object) -> float | None:
    for value in values:
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number == 0:
            continue
        return number
    return None


def _xy(feature: dict) -> tuple[float | None, float | None]:
    geom = feature.get("geometry") or {}
    attrs = feature.get("attributes") or {}
    lat = _coord(
        geom.get("y"),
        attrs.get("latitude"),
        attrs.get("LATITUDE"),
        attrs.get("sh_lat"),
        attrs.get("lat"),
        attrs.get("SURFACE__2"),
        attrs.get("SURFACE_LA"),
    )
    lon = _coord(
        geom.get("x"),
        attrs.get("longitude"),
        attrs.get("LONGITUDE"),
        attrs.get("sh_lon"),
        attrs.get("lon"),
        attrs.get("SURFACE_LO"),
        attrs.get("SURFACE__1"),
    )
    return lat, lon


def la_serial_api(serial: object) -> str | None:
    """Stable 10-digit key for SONRIS wells that have no official API."""
    try:
        number = int(float(serial))
    except (TypeError, ValueError):
        return None
    if number <= 0 or number >= 10_000_000:
        return None
    return f"17{90000000 + number:08d}"


def _api10(value: object, state: str) -> str | None:
    digits = _digits(value)
    prefix = api_prefix(state)
    if len(digits) >= 10 and digits[:2] == prefix:
        api = digits[:10]
    elif len(digits) >= 8:
        eight = digits[-8:] if len(digits) > 8 else digits.zfill(8)
        api = prefix + eight
    else:
        return None
    if api[2:] == "00000000" or set(api[2:]) == {"0"}:
        return None
    return api


def _well_row(state: str, api: str, **fields: object) -> dict:
    now = utcnow()
    well_name, well_no, lease_name = normalize_well_identity(
        _text(fields.get("well_name")),
        _text(fields.get("well_no")),
        _text(fields.get("lease_name")),
    )
    return {
        "api": api,
        "api8": api[2:10],
        "well_name": well_name,
        "well_no": well_no,
        "lease_name": lease_name,
        "lease_no": _text(fields.get("lease_no")),
        "county": _text(fields.get("county")),
        "county_code": api[2:5],
        "district": _text(fields.get("district")),
        "operator": normalize_operator_name(_text(fields.get("operator"))),
        "operator_number": _text(fields.get("operator_number")),
        "field": _text(fields.get("field")),
        "well_type": _text(fields.get("well_type")),
        "symbol": _text(fields.get("symbol") or fields.get("well_type")),
        "symnum": None,
        "profile": _text(fields.get("profile")),
        "wellhead_lat": fields.get("lat"),
        "wellhead_lon": fields.get("lon"),
        "wellhead_crs": "EPSG:4326",
        "toe_lat": None,
        "toe_lon": None,
        "toe_crs": None,
        "location_kind": "wellhead",
        "location_source": fields.get("source"),
        "gis_lat83": fields.get("lat"),
        "gis_long83": fields.get("lon"),
        "gis_lat27": None,
        "gis_long27": None,
        "source": fields.get("source"),
        "migrated_from_permit": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def split_nm_well_name(name: str) -> tuple[str, str, str]:
    text = (name or "").strip()
    if " #" in text:
        lease, number = text.rsplit(" #", 1)
        return text, lease.strip(), number.strip()
    return text, text, ""


def nm_permit_status(raw: object) -> str | None:
    """Map OCD statuses that are still APDs onto Texas permit status values."""
    key = _text(raw).lower()
    return {
        "new": "approved",
        "never drilled": "approved",
        "cancelled": "cancelled",
        "canceled": "cancelled",
    }.get(key)


def nm_profile(raw: object) -> str:
    key = _text(raw).upper()
    return {"H": "horizontal", "V": "vertical", "D": "directional"}.get(key, "")


def nm_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    if nm_permit_status(attrs.get("status")):
        return None
    api = _api10(attrs.get("id") or attrs.get("api") or attrs.get("API"), "nm")
    lat, lon = _xy(feature)
    if not api or lat is None or lon is None:
        return None
    well_name, lease_name, well_no = split_nm_well_name(
        _text(attrs.get("name"), attrs.get("well_name"))
    )
    return _well_row(
        "nm",
        api,
        well_name=well_name,
        well_no=well_no,
        lease_name=lease_name,
        operator=_assigned_operator(attrs.get("ogrid_name"), attrs.get("operator")),
        operator_number=attrs.get("ogrid") or attrs.get("ogrid_no"),
        county=attrs.get("county"),
        district=attrs.get("district"),
        field=attrs.get("pool_id_list") or attrs.get("field") or attrs.get("pool"),
        well_type=attrs.get("type"),
        symbol=attrs.get("status") or attrs.get("type"),
        profile=nm_profile(attrs.get("directional_status")),
        lat=lat,
        lon=lon,
        source="nm_ocd",
    )


def nm_feature_to_permit(feature: dict, *, now: str, lifetime_days: int) -> dict | None:
    attrs = feature.get("attributes") or {}
    status = nm_permit_status(attrs.get("status"))
    if not status:
        return None
    api = _api10(attrs.get("id") or attrs.get("api") or attrs.get("API"), "nm")
    lat, lon = _xy(feature)
    if not api or lat is None or lon is None:
        return None
    well_name, lease_name, well_no = split_nm_well_name(
        _text(attrs.get("name"), attrs.get("well_name"))
    )
    permit_no = _text(attrs.get("id")) or f"OCD-{api[2:]}"
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
        "operator": normalize_operator_name(
            _assigned_operator(attrs.get("ogrid_name"), attrs.get("operator"))
        ),
        "operator_number": _text(attrs.get("ogrid"), attrs.get("ogrid_no")),
        "profile": nm_profile(attrs.get("directional_status")),
        "symbol": _text(attrs.get("status"), attrs.get("type")),
        "symnum": None,
        "wellhead_lat": lat,
        "wellhead_lon": lon,
        "wellhead_crs": "EPSG:4326",
        "approved_at": None,
        "submitted_at": None,
        "expires_at": None,
        "lifetime_days": lifetime_days,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "nm_ocd",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }


def ok_feature_to_well(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("api") or attrs.get("API"), "ok")
    lat, lon = _xy(feature)
    if not api or lat is None or lon is None:
        return None
    name = _text(attrs.get("well_name"), attrs.get("name"))
    number = _text(attrs.get("well_num"), attrs.get("well_no"))
    return _well_row(
        "ok",
        api,
        well_name=name,
        well_no=number,
        operator=_assigned_operator(attrs.get("operator") or attrs.get("op")),
        operator_number=attrs.get("operator_num")
        or attrs.get("op_num")
        or attrs.get("operator_number"),
        county=attrs.get("county"),
        well_type=attrs.get("welltype") or attrs.get("well_type"),
        symbol=attrs.get("wellstatus") or attrs.get("status"),
        lat=lat,
        lon=lon,
        source="ok_occ",
    )


def la_feature_to_well(feature: dict, used_apis: set[str] | None = None) -> dict | None:
    attrs = feature.get("attributes") or {}
    official = _api10(attrs.get("API_NUM") or attrs.get("api"), "la")
    serial = attrs.get("WELL_SERIA") or attrs.get("WELL_SERIAL")
    api = official
    if not api or (used_apis is not None and api in used_apis):
        api = la_serial_api(serial)
    if not api or (used_apis is not None and api in used_apis):
        return None
    lat, lon = _xy(feature)
    if lat is None or lon is None:
        return None
    name = _text(attrs.get("WELL_NAME"), attrs.get("LUW_NAME"))
    number = _text(attrs.get("WELL_NUM"))
    if name and number and number not in name:
        name = f"{name} #{number}".strip(" #")
    row = _well_row(
        "la",
        api,
        well_name=name,
        well_no=number,
        lease_name=attrs.get("LUW_NAME") or attrs.get("LEASE_NUM"),
        lease_no=attrs.get("LEASE_NUM") or serial,
        operator=attrs.get("ORG_OPER_N") or attrs.get("ORGANIZATI"),
        operator_number=attrs.get("ORGANIZATI"),
        county=attrs.get("PARISH_NAM"),
        district=attrs.get("DISTRICT_C"),
        field=attrs.get("FIELD_NAME"),
        well_type=attrs.get("WELL_CLASS") or attrs.get("PRODUCT_TY") or attrs.get("LEGEND_DES"),
        symbol=attrs.get("LEGEND_DES") or attrs.get("WELL_STA_1") or attrs.get("WELL_STATU"),
        lat=lat,
        lon=lon,
        source="la_sonris",
    )
    parish = _text(attrs.get("PARISH_COD")).zfill(3)[:3]
    if parish and parish != "000":
        row["county_code"] = parish
    row["location_source"] = f"la_serial:{serial}" if serial not in (None, "") else row["location_source"]
    return row


def _is_nm_waste(well_type: str) -> bool:
    text = (well_type or "").strip().lower()
    return text in NM_WASTE_TYPES or "salt water" in text or text == "swd"


def _is_la_waste(attrs: dict) -> bool:
    inject = _text(attrs.get("INJECTION_"), attrs.get("INJECTION1"), attrs.get("WELL_CLASS"))
    classifica = _text(attrs.get("CLASSIFICA"))
    blob = f"{inject} {classifica}".lower()
    return any(token in blob for token in ("inject", "dispos", "swd", "commercial"))


def _disposal_row(state: str, site_id: int, **fields: object) -> dict | None:
    lat = fields.get("lat")
    lon = fields.get("lon")
    if lat is None or lon is None:
        return None
    return {
        "id": int(site_id),
        "operator": _text(fields.get("operator")),
        "facility": _text(fields.get("facility")),
        "permit_no": _text(fields.get("permit_no")),
        "permit_type": _text(fields.get("permit_type")),
        "discharge_type": _text(fields.get("discharge_type")),
        "permit_expiration": "",
        "district": _text(fields.get("district")),
        "county": _text(fields.get("county")),
        "permit_url": _text(fields.get("permit_url")),
        "lat": float(lat),
        "lon": float(lon),
    }


def ok_uic_to_site(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    object_id = attrs.get("objectid") or attrs.get("OBJECTID")
    lat, lon = _xy(feature)
    if object_id in (None, "") or lat is None or lon is None:
        return None
    name = _text(attrs.get("well_name"), attrs.get("facility"))
    number = _text(attrs.get("well_num"))
    if name and number:
        name = f"{name} {number}".strip()
    return _disposal_row(
        "ok",
        DISPOSAL_ID_BASE["ok"] + int(object_id),
        operator=attrs.get("op") or attrs.get("operator"),
        facility=name,
        permit_no=attrs.get("api") or attrs.get("orders"),
        permit_type=attrs.get("well_type") or "Commercial UIC",
        discharge_type=attrs.get("status"),
        county=attrs.get("county"),
        lat=lat,
        lon=lon,
    )


def ok_pit_to_site(feature: dict) -> dict | None:
    attrs = feature.get("attributes") or {}
    object_id = attrs.get("objectid") or attrs.get("OBJECTID") or attrs.get("fid")
    lat, lon = _xy(feature)
    if object_id in (None, "") or lat is None or lon is None:
        return None
    return _disposal_row(
        "ok",
        DISPOSAL_ID_BASE["ok"] + 8_000_000 + int(object_id),
        operator=attrs.get("operator") or attrs.get("op") or attrs.get("opername"),
        facility=attrs.get("facility") or attrs.get("name") or attrs.get("pit_name"),
        permit_no=attrs.get("permit") or attrs.get("permit_no") or attrs.get("api"),
        permit_type="Recycling pit",
        county=attrs.get("county"),
        lat=lat,
        lon=lon,
    )


def _log(message: str) -> None:
    print(message, flush=True)


def _load_wells(
    conn,
    state: str,
    url: str,
    mapper,
    *,
    limit: int = 0,
    delay: float = 0.12,
    page_size: int = 2000,
) -> int:
    batch: list[dict] = []
    total = 0
    seen: set[str] = set()
    for feature in iter_features(url, delay=delay, limit=limit, page_size=page_size):
        row = mapper(feature, used_apis=seen) if mapper is la_feature_to_well else mapper(feature)
        if not row or row["api"] in seen:
            continue
        seen.add(row["api"])
        batch.append(row)
        if len(batch) >= 1000:
            upsert_wells(conn, state, batch)
            conn.commit()
            total += len(batch)
            _log(f"{state} wells {total}")
            batch = []
    if batch:
        upsert_wells(conn, state, batch)
        conn.commit()
        total += len(batch)
    return total


LA_WELL_OBJECTID_MAX = 250_000
LA_WELL_OBJECTID_STEP = 4_000
BSEE_WELLS = (
    "https://gis.boem.gov/server/rest/services/BOEM_BSEE/GOA_Layers/FeatureServer/1/query"
)


def _load_la_wells(conn, *, delay: float = 0.08, limit: int = 0) -> int:
    seen: set[str] = set()
    batch: list[dict] = []
    total = 0

    def flush() -> None:
        nonlocal batch, total
        if not batch:
            return
        upsert_wells(conn, "la", batch)
        conn.commit()
        total += len(batch)
        _log(f"la wells {total}")
        batch = []

    for low in range(0, LA_WELL_OBJECTID_MAX, LA_WELL_OBJECTID_STEP):
        where = f"OBJECTID > {low} AND OBJECTID <= {low + LA_WELL_OBJECTID_STEP}"
        for feature in iter_features(LA_WELLS, where=where, delay=delay, page_size=1000, limit=0):
            row = la_feature_to_well(feature, used_apis=seen)
            if not row:
                continue
            seen.add(row["api"])
            batch.append(row)
            if len(batch) >= 1000:
                flush()
            if limit and total + len(batch) >= limit:
                flush()
                return total
    flush()
    added = _load_la_bsee_wells(conn, seen, delay=delay)
    total += added
    if added:
        _log(f"la wells with BSEE {total}")
    try:
        from wellnav.ingest.la_refresh import refresh_louisiana

        extra = refresh_louisiana(conn)
        _log(f"la refresh {extra}")
    except Exception as exc:
        _log(f"la refresh skipped: {exc}")
        from wellnav.ingest.la_operators import sync_louisiana_catalog

        _log(f"la catalog {sync_louisiana_catalog(conn)}")
    return total


def bsee_feature_to_well(feature: dict, used_apis: set[str] | None = None) -> dict | None:
    attrs = feature.get("attributes") or {}
    api = _api10(attrs.get("API_NUMBER") or attrs.get("API") or attrs.get("api"), "la")
    if not api or (used_apis is not None and api in used_apis):
        return None
    lat, lon = _xy(feature)
    if lat is None or lon is None:
        lat = _coord(attrs.get("Y"))
        lon = _coord(attrs.get("X"))
    if lat is None or lon is None:
        return None
    hole = _text(attrs.get("WELL_NAME"))
    operator_code = _text(attrs.get("OPERATOR"))
    return _well_row(
        "la",
        api,
        well_name=f"OCS #{hole}" if hole else "",
        well_no=hole,
        operator=operator_code,
        operator_number=operator_code,
        well_type=attrs.get("TYPE_CODE_DESC") or attrs.get("TYPE_CODE"),
        symbol=attrs.get("STATUS_DESCRIPTION") or attrs.get("STATUS"),
        county="Louisiana OCS",
        lat=lat,
        lon=lon,
        source="la_bsee",
    )


def _load_la_bsee_wells(conn, seen: set[str], *, delay: float) -> int:
    batch: list[dict] = []
    added = 0
    try:
        features = iter_features(
            BSEE_WELLS, where="API_NUMBER LIKE '17%'", delay=delay, page_size=2000
        )
    except Exception as exc:
        _log(f"la bsee wells skipped: {exc}")
        return 0
    for feature in features:
        row = bsee_feature_to_well(feature, used_apis=seen)
        if not row:
            continue
        seen.add(row["api"])
        batch.append(row)
        if len(batch) >= 1000:
            upsert_wells(conn, "la", batch)
            conn.commit()
            added += len(batch)
            batch = []
    if batch:
        upsert_wells(conn, "la", batch)
        conn.commit()
        added += len(batch)
    return added


def _upsert_disposal(conn, table: str, row: dict) -> None:
    conn.execute(
        f"""
        INSERT INTO {table}(
            id, operator, facility, permit_no, permit_type, discharge_type,
            permit_expiration, district, county, permit_url, lat, lon
        ) VALUES (
            :id, :operator, :facility, :permit_no, :permit_type, :discharge_type,
            :permit_expiration, :district, :county, :permit_url, :lat, :lon
        )
        ON CONFLICT(id) DO UPDATE SET
            operator=excluded.operator,
            facility=excluded.facility,
            permit_no=excluded.permit_no,
            permit_type=excluded.permit_type,
            discharge_type=excluded.discharge_type,
            county=excluded.county,
            lat=excluded.lat,
            lon=excluded.lon
        """,
        row,
    )


def _load_disposal_layer(conn, state: str, url: str, mapper, *, delay: float = 0.12) -> int:
    from wellnav.disposal import ensure_disposal_table

    table = ensure_disposal_table(conn, state)
    count = 0
    for feature in iter_features(url, delay=delay):
        row = mapper(feature)
        if not row:
            continue
        _upsert_disposal(conn, table, row)
        count += 1
    conn.commit()
    return count


def _disposal_from_wells(disp_conn, well_conn, state: str) -> int:
    from wellnav.disposal import ensure_disposal_table

    table = ensure_disposal_table(disp_conn, state)
    if state == "nm":
        rows = well_conn.execute(
            """
            SELECT api, well_name, operator, county, district, well_type, symbol,
                   wellhead_lat, wellhead_lon
            FROM wells_nm
            WHERE wellhead_lat IS NOT NULL AND wellhead_lon IS NOT NULL
            """
        ).fetchall()
        count = 0
        for row in rows:
            if not _is_nm_waste(row["well_type"] or ""):
                continue
            site = _disposal_row(
                "nm",
                DISPOSAL_ID_BASE["nm"] + int(row["api"][2:10]),
                operator=row["operator"],
                facility=row["well_name"],
                permit_no=row["api"],
                permit_type=row["well_type"] or "Salt Water Disposal",
                county=row["county"],
                district=row["district"],
                lat=row["wellhead_lat"],
                lon=row["wellhead_lon"],
            )
            if site:
                _upsert_disposal(disp_conn, table, site)
                count += 1
        disp_conn.commit()
        return count
    if state == "la":
        return 0
    return 0


def load_neighbors(
    *,
    states: list[str] | None = None,
    skip_wells: bool = False,
    skip_disposal: bool = False,
    skip_pipelines: bool = False,
    skip_eia: bool = False,
    limit: int = 0,
    delay: float = 0.12,
    db_path=None,
    disposal_path=None,
    pipe_path=None,
) -> dict:
    wanted = [code for code in (states or list(APP_STATES[1:])) if code in APP_STATES and code != "tx"]
    if not wanted:
        wanted = ["nm", "ok", "la"]
    well_conn = connect(Path(db_path) if db_path else None)
    init_schema(well_conn)
    disp_conn = disposal_connect(Path(disposal_path) if disposal_path else None)
    init_disposal(disp_conn)
    pipe_conn = pipe_connect(Path(pipe_path) if pipe_path else None)
    init_pipelines(pipe_conn)
    now = utcnow()
    stats: dict = {"status": "ok", "states": {}}
    try:
        for state in wanted:
            part = {"wells": 0, "disposal": 0, "pipelines": 0, "permits": 0, "error": None}
            try:
                if not skip_wells:
                    _log(f"loading {state} wells")
                    if state == "la":
                        part["wells"] = _load_la_wells(well_conn, delay=delay, limit=limit)
                    elif state == "ok":
                        from wellnav.ingest.ok_wells import load_ok_wells

                        ok_stats = load_ok_wells(well_conn, delay=delay, limit=limit)
                        part["wells"] = int(ok_stats.get("wells") or 0)
                        part["permits"] = int(ok_stats.get("permits") or 0)
                    elif state == "nm":
                        from wellnav.ingest.nm_wells import load_nm_wells

                        nm_stats = load_nm_wells(
                            well_conn,
                            delay=delay,
                            limit=limit,
                            skip_fracfocus=bool(limit),
                        )
                        part["wells"] = int(nm_stats.get("wells") or 0)
                        part["permits"] = int(nm_stats.get("permits") or 0)
                    else:
                        _log(f"no dedicated well loader for {state}")
                    set_meta(well_conn, f"{state}_wells_loaded_at", now)
                    well_conn.commit()
                if not skip_disposal:
                    _log(f"loading {state} waste sites")
                    if state == "ok":
                        part["disposal"] += _load_disposal_layer(disp_conn, state, OK_UIC, ok_uic_to_site, delay=delay)
                        part["disposal"] += _load_disposal_layer(disp_conn, state, OK_PITS, ok_pit_to_site, delay=delay)
                    elif state == "nm":
                        part["disposal"] += _disposal_from_wells(disp_conn, well_conn, state)
                    elif state == "la":
                        part["disposal"] += _load_la_waste(disp_conn, delay=delay)
                    set_meta(disp_conn, f"disposal_{state}_loaded_at", now)
                    disp_conn.commit()
                _log(f"{state} done {part}")
            except Exception as exc:
                part["error"] = str(exc)
                stats["status"] = "partial"
                _log(f"{state} failed: {exc}")
            stats["states"][state] = part
        if not skip_pipelines:
            try:
                _log("loading NM/OK/LA public pipeline overlays")
                pipe_counts = load_neighbor_pipelines(
                    pipe_conn, wanted, skip_eia=skip_eia, delay=delay
                )
                for state, count in pipe_counts.items():
                    stats["states"][state]["pipelines"] = count
                    pipe_conn.execute(
                        "INSERT INTO meta(key, value) VALUES (?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                        (f"pipelines_{state}_loaded_at", now),
                    )
                pipe_conn.commit()
                _log(f"neighbor pipelines {pipe_counts}")
            except Exception as exc:
                stats["status"] = "partial"
                _log(f"neighbor pipelines failed: {exc}")
                for state in wanted:
                    stats["states"][state]["error"] = str(exc)
    finally:
        well_conn.close()
        disp_conn.close()
        pipe_conn.close()
    return stats


def _load_la_waste(conn, *, delay: float) -> int:
    from wellnav.disposal import ensure_disposal_table

    table = ensure_disposal_table(conn, "la")
    count = 0
    where = (
        "INJECTION_ IS NOT NULL AND TRIM(INJECTION_) <> '' "
        "AND TRIM(INJECTION_) <> '0'"
    )
    try:
        features = iter_features(LA_WELLS, where=where, delay=delay)
        for feature in features:
            attrs = feature.get("attributes") or {}
            if not _is_la_waste(attrs):
                continue
            lat, lon = _xy(feature)
            object_id = attrs.get("OBJECTID") or attrs.get("OBJECTID_1")
            api = _api10(attrs.get("API_NUM"), "la")
            if object_id in (None, "") or lat is None or lon is None:
                continue
            site = _disposal_row(
                "la",
                DISPOSAL_ID_BASE["la"] + int(object_id),
                operator=attrs.get("ORG_OPER_N"),
                facility=_text(attrs.get("WELL_NAME"), attrs.get("LUW_NAME")),
                permit_no=api or attrs.get("WELL_SERIAL"),
                permit_type=_text(attrs.get("WELL_CLASS"), attrs.get("INJECTION_"), "Injection"),
                county=attrs.get("PARISH_NAM"),
                district=attrs.get("DISTRICT_C"),
                lat=lat,
                lon=lon,
            )
            if site:
                _upsert_disposal(conn, table, site)
                count += 1
        conn.commit()
    except Exception as exc:
        _log(f"la waste skipped: {exc}")
    return count
