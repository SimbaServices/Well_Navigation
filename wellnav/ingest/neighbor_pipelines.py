"""Public pipeline overlays for New Mexico, Oklahoma, and Louisiana.

Texas stays on RRC T-4. Neighbor states have no public T-4 equivalent:

* EIA/HIFLD transmission (gas, crude, products, HGL) — already used
* BSEE Gulf of America OCS pipeline arcs — Louisiana / adjacent federal waters
* BLM NM realty issued lines — Mineral Leasing Act and named oilfield ROWs

OCC detailed pipeline GIS and NM OCD gathering as-builts are not public.
"""

from __future__ import annotations

import re
import tempfile
import zipfile
from pathlib import Path

import requests
import shapefile
from shapely import wkb
from shapely.geometry import GeometryCollection, LineString, MultiLineString, box, shape

from wellnav.db import ROOT
from wellnav.ingest.arcgis import iter_envelope, iter_features
from wellnav.pipelines import ensure_pipeline_table, geometry_wgs84_from_nad27
from wellnav.states import STATE_BBOX, STATE_LABELS

EIA_LAYERS = (
    (0, "HVL", "Hydrocarbon gas liquids"),
    (1, "PRD", "Petroleum products"),
    (2, "CRL", "Crude oil"),
    (3, "NGT", "Natural gas"),
)
EIA_BASE = (
    "https://arcgis.netl.doe.gov/server/rest/services/Hosted/"
    "EIA_pipeline_data/FeatureServer"
)
BLM_ISSUED_LINES = (
    "https://gis.blm.gov/nmarcgis/rest/services/Lands/"
    "BLM_NM_Realty_Issuances/MapServer/1/query"
)
BSEE_ZIP_URL = "https://www.data.bsee.gov/Mapping/Files/ppl_arcs.zip"
BSEE_ZIP_PATH = ROOT / "data" / "pipelines" / "bsee" / "ppl_arcs.zip"
USER_AGENT = (
    "Mozilla/5.0 (compatible; WellNavigation/1.0; +https://github.com/sparker113/Well_Navigation)"
)

EIA_ID_BASE = {"nm": 3_000_000_000, "ok": 3_100_000_000, "la": 3_200_000_000}
BLM_ID_BASE = {"nm": 3_050_000_000, "ok": 3_150_000_000}
BSEE_ID_BASE = 3_300_000_000

# Land Louisiana plus adjacent GOM so export/flowlines that serve LA are kept.
LA_PIPE_BBOX = {"lon_min": -94.1, "lat_min": 26.0, "lon_max": -88.8, "lat_max": 33.1}

BSEE_SKIP_PROD = {
    "TOW",
    "CBLR",
    "CBLP",
    "CBLC",
    "UMB",
    "UMBE",
    "UMBH",
    "UMBC",
    "UBEH",
}
BSEE_SKIP_STATUS = {"CNCL", "PROP"}
BSEE_GAS = {
    "GAS",
    "GASH",
    "G/C",
    "G/CH",
    "G/O",
    "G/OH",
    "BLKG",
    "BLGH",
    "FLG",
    "SPLY",
    "LIFT",
    "INJ",
    "NGER",
}
BSEE_CRUDE = {"OIL", "OILH", "BLKO", "BLOH", "COND", "O/W"}
BSEE_HVL = {"NGL", "LPRO"}
BSEE_GATHER = {"BLKG", "BLGH", "BLKO", "BLOH"}
BLM_PIPE_WHERE = (
    "FACILTYPE = 'MLA Feature' OR ("
    "FACILTYPE IN ('Other FLPMA', 'Minerals Feature') AND ("
    "UPPER(CASENAME) LIKE '%PIPE%' OR UPPER(CASENAME) LIKE '%FLOWLINE%' "
    "OR UPPER(CASENAME) LIKE '%GATHER%'"
    "))"
)
_PIPE_INSERT = """
INSERT INTO {table}(
    tpms_id, county_code, county_name, operator, p5_num, system_name,
    subsystem, t4_permit, diameter, commodity, commodity_desc, status,
    systype, interstate, quality, pipeline_id, geom, minx, miny, maxx, maxy
) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(tpms_id) DO UPDATE SET
    operator=excluded.operator,
    system_name=excluded.system_name,
    subsystem=excluded.subsystem,
    t4_permit=excluded.t4_permit,
    diameter=excluded.diameter,
    commodity=excluded.commodity,
    commodity_desc=excluded.commodity_desc,
    status=excluded.status,
    systype=excluded.systype,
    interstate=excluded.interstate,
    quality=excluded.quality,
    pipeline_id=excluded.pipeline_id,
    geom=excluded.geom,
    minx=excluded.minx, miny=excluded.miny,
    maxx=excluded.maxx, maxy=excluded.maxy
"""


def _log(message: str) -> None:
    print(message, flush=True)


def _text(*values: object) -> str:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"none", "null", "nan"}:
            return text
    return ""


def _paths_to_geom(feature: dict):
    geom = feature.get("geometry") or {}
    paths = geom.get("paths") or []
    lines = [LineString(path) for path in paths if path and len(path) >= 2]
    if not lines:
        return None
    return lines[0] if len(lines) == 1 else MultiLineString(lines)


def _clip_lines(geom, west: float, south: float, east: float, north: float):
    clipped = geom.intersection(box(west, south, east, north))
    if clipped.is_empty:
        return None
    if isinstance(clipped, GeometryCollection):
        lines = [
            part
            for part in clipped.geoms
            if part.geom_type in {"LineString", "MultiLineString"}
        ]
        if not lines:
            return None
        flat = []
        for item in lines:
            if item.geom_type == "MultiLineString":
                flat.extend(item.geoms)
            else:
                flat.append(item)
        clipped = flat[0] if len(flat) == 1 else MultiLineString(flat)
    if clipped.geom_type not in {"LineString", "MultiLineString"}:
        return None
    return clipped


def pipe_bbox(state: str) -> dict[str, float]:
    if state == "la":
        return LA_PIPE_BBOX
    return STATE_BBOX[state]


def parse_pipe_size(value: object) -> float | None:
    text = _text(value)
    if not text:
        return None
    numbers = [float(part) for part in re.findall(r"\d+(?:\.\d+)?", text)]
    return max(numbers) if numbers else None


def diameter_from_text(value: object) -> float | None:
    text = _text(value)
    if not text:
        return None
    mixed = re.search(r"(\d+)\s*-\s*(\d+)\s*/\s*(\d+)", text)
    if mixed:
        return int(mixed.group(1)) + int(mixed.group(2)) / int(mixed.group(3))
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:[\"”]|in\b|dia)", text, re.I)
    if match:
        return float(match.group(1))
    return None


def bsee_commodity(prod: str) -> tuple[str, str]:
    code = (prod or "").strip().upper()
    if code in BSEE_GAS:
        return "NGT", "Natural gas"
    if code in BSEE_CRUDE:
        return "CRL", "Crude oil"
    if code in BSEE_HVL:
        return "HVL", "Hydrocarbon gas liquids"
    if code == "CO2":
        return "CO2", "Carbon dioxide"
    return "OTH", code or "Other"


def bsee_status(code: str) -> str | None:
    key = (code or "").strip().upper()
    if key in BSEE_SKIP_STATUS:
        return None
    if key in {"ACT", "COMB"}:
        return "I"
    if key in {"REM", "R/R", "PREM"}:
        return "R"
    if key in {"ABN", "OUT", "A/C", "O/C", "RELQ", "R/A", "R/C", "PABN"}:
        return "B"
    return "I" if key else None


def bsee_systype(prod: str, aprv: str) -> str:
    if (prod or "").strip().upper() in BSEE_GATHER or (aprv or "").strip().upper() == "L":
        return "G"
    return "T"


def keep_bsee_product(prod: str) -> bool:
    return (prod or "").strip().upper() not in BSEE_SKIP_PROD


def blm_is_oilfield_row(attrs: dict) -> bool:
    facil = _text(attrs.get("FACILTYPE")).lower()
    if facil == "mla feature":
        return True
    blob = f"{_text(attrs.get('CASENAME'))} {_text(attrs.get('COMMENTS'))}".lower()
    if not any(token in blob for token in ("pipe", "flowline", "gather")):
        return False
    if any(token in blob for token in ("gas", "oil", "crude", "swd", "salt", "produced", "waste", "ngl", "condensate", "h2s")):
        return True
    if "water" in blob or "storm" in blob:
        return False
    return facil in {"other flpma", "minerals feature"}


def blm_commodity(name: str) -> tuple[str, str]:
    blob = (name or "").lower()
    if any(token in blob for token in ("ngl", "hgl", "propane", "butane")):
        return "HVL", "Hydrocarbon gas liquids"
    if any(token in blob for token in ("gas", "methane")):
        return "NGT", "Natural gas"
    if any(token in blob for token in ("oil", "crude")):
        return "CRL", "Crude oil"
    if any(token in blob for token in ("swd", "salt", "produced water", "waste")):
        return "OTH", "Produced water"
    return "OTH", "Right-of-way"


def blm_state(attrs: dict, geom) -> str | None:
    admin = _text(attrs.get("ADMIN_ST")).lower()
    if admin in {"nm", "ok"}:
        return admin
    if geom is None or geom.is_empty:
        return None
    x, y = geom.centroid.x, geom.centroid.y
    for code in ("nm", "ok"):
        bbox = STATE_BBOX[code]
        if bbox["lon_min"] <= x <= bbox["lon_max"] and bbox["lat_min"] <= y <= bbox["lat_max"]:
            return code
    return None


def _row_tuple(
    *,
    tpms_id: int,
    county_name: str,
    operator: str,
    system_name: str,
    t4_permit: str,
    diameter: float | None,
    commodity: str,
    commodity_desc: str,
    status: str,
    systype: str,
    interstate: str,
    quality: str,
    pipeline_id: str,
    geom,
    subsystem: str = "",
) -> tuple:
    minx, miny, maxx, maxy = geom.bounds
    return (
        tpms_id,
        "",
        county_name,
        operator,
        "",
        system_name,
        subsystem,
        t4_permit,
        diameter,
        commodity,
        commodity_desc,
        status,
        systype,
        interstate,
        quality,
        pipeline_id,
        wkb.dumps(geom, hex=False),
        minx,
        miny,
        maxx,
        maxy,
    )


def _upsert_rows(conn, table: str, rows: list[tuple]) -> None:
    if not rows:
        return
    conn.executemany(_PIPE_INSERT.format(table=table), rows)


def _delete_quality(conn, table: str, quality: str) -> None:
    conn.execute(f"DELETE FROM {table} WHERE quality = ?", (quality,))


def _load_eia_pipelines(conn, state: str, *, delay: float = 0.12) -> int:
    bbox = pipe_bbox(state)
    west, south, east, north = bbox["lon_min"], bbox["lat_min"], bbox["lon_max"], bbox["lat_max"]
    table = ensure_pipeline_table(conn, state)
    inserted = 0
    for layer, commodity, commodity_desc in EIA_LAYERS:
        url = f"{EIA_BASE}/{layer}/query"
        for feature in iter_envelope(
            url, west=west, south=south, east=east, north=north, delay=delay
        ):
            attrs = feature.get("attributes") or {}
            geom = _paths_to_geom(feature)
            if geom is None:
                continue
            clipped = _clip_lines(geom, west, south, east, north)
            if clipped is None:
                continue
            fid = attrs.get("fid") or attrs.get("OBJECTID") or attrs.get("objectid")
            if fid in (None, ""):
                continue
            operator = _text(attrs.get("opername"), attrs.get("operator"))
            system = _text(attrs.get("pipename"), attrs.get("typepipe"), commodity_desc)
            interstate = "Y"
            tpms_id = EIA_ID_BASE[state] + layer * 10_000_000 + int(fid)
            _upsert_rows(
                conn,
                table,
                [
                    _row_tuple(
                        tpms_id=tpms_id,
                        county_name=STATE_LABELS[state],
                        operator=operator,
                        system_name=system,
                        t4_permit="",
                        diameter=None,
                        commodity=commodity,
                        commodity_desc=commodity_desc,
                        status="I",
                        systype="T",
                        interstate=interstate,
                        quality="eia",
                        pipeline_id=str(fid),
                        geom=clipped,
                    )
                ],
            )
            inserted += 1
        conn.commit()
        _log(f"{state} eia layer {layer} running total {inserted}")
    return inserted


def download_bsee_zip(dest: Path | None = None) -> Path:
    path = Path(dest) if dest else BSEE_ZIP_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size > 1_000_000 and zipfile.is_zipfile(path):
        return path
    _log(f"downloading BSEE pipelines {BSEE_ZIP_URL}")
    resp = requests.get(BSEE_ZIP_URL, headers={"User-Agent": USER_AGENT}, timeout=180)
    resp.raise_for_status()
    path.write_bytes(resp.content)
    if not zipfile.is_zipfile(path):
        path.unlink(missing_ok=True)
        raise RuntimeError("BSEE pipeline download was not a zip")
    return path


def _bsee_rec(rec: dict, *names: str):
    upper = {str(key).upper(): value for key, value in rec.items()}
    for name in names:
        if name.upper() in upper:
            return upper[name.upper()]
        for key, value in upper.items():
            if name.upper().startswith(key) or key.startswith(name.upper()[:8]):
                return value
    return None


def bsee_record_row(rec: dict, geom) -> tuple | None:
    prod = _text(_bsee_rec(rec, "PROD_CODE"))
    if not keep_bsee_product(prod):
        return None
    status = bsee_status(_text(_bsee_rec(rec, "STATUS_COD", "STATUS_CODE")))
    if status is None:
        return None
    segment = _bsee_rec(rec, "SEGMENT_NU", "SEGMENT_NUM")
    if segment in (None, ""):
        return None
    aprv = _text(_bsee_rec(rec, "APRV_CODE"))
    commodity, commodity_desc = bsee_commodity(prod)
    diameter = parse_pipe_size(_bsee_rec(rec, "PPL_SIZE_C", "PPL_SIZE_CODE"))
    row_number = _text(_bsee_rec(rec, "ROW_NUMBER"))
    operator = _text(_bsee_rec(rec, "SDE_COMPAN", "SDE_COMPANY_DESG"))
    systype = bsee_systype(prod, aprv)
    interstate = "Y" if aprv == "R" and systype == "T" else "N"
    return _row_tuple(
        tpms_id=BSEE_ID_BASE + int(segment),
        county_name="Louisiana OCS",
        operator=operator,
        system_name=row_number or f"BSEE {segment}",
        t4_permit=row_number,
        diameter=diameter,
        commodity=commodity,
        commodity_desc=commodity_desc,
        status=status,
        systype=systype,
        interstate=interstate,
        quality="bsee",
        pipeline_id=str(int(segment)),
        geom=geom,
        subsystem=prod,
    )


def load_bsee_zip(conn, zip_path: Path) -> int:
    table = ensure_pipeline_table(conn, "la")
    _delete_quality(conn, table, "bsee")
    bbox = LA_PIPE_BBOX
    west, south, east, north = bbox["lon_min"], bbox["lat_min"], bbox["lon_max"], bbox["lat_max"]
    inserted = 0
    batch: list[tuple] = []
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        shapefiles = sorted(extract_dir.rglob("*.shp"))
        for shp in shapefiles:
            reader = shapefile.Reader(str(shp))
            try:
                fields = [
                    field.name if hasattr(field, "name") else field[0]
                    for field in reader.fields
                    if (field.name if hasattr(field, "name") else field[0]) != "DeletionFlag"
                ]
                for sr in reader.iterShapeRecords():
                    rec = (
                        sr.record.as_dict()
                        if hasattr(sr.record, "as_dict")
                        else dict(zip(fields, sr.record))
                    )
                    converted = geometry_wgs84_from_nad27(sr.shape.__geo_interface__)
                    if converted is None:
                        continue
                    geom = shape(converted)
                    if geom.is_empty:
                        continue
                    clipped = _clip_lines(geom, west, south, east, north)
                    if clipped is None:
                        continue
                    row = bsee_record_row(rec, clipped)
                    if row is None:
                        continue
                    batch.append(row)
                    if len(batch) >= 500:
                        _upsert_rows(conn, table, batch)
                        inserted += len(batch)
                        batch = []
                        if inserted % 2000 == 0:
                            _log(f"la bsee {inserted}")
            finally:
                reader.close()
    if batch:
        _upsert_rows(conn, table, batch)
        inserted += len(batch)
    conn.commit()
    _log(f"la bsee loaded {inserted}")
    return inserted


def _load_bsee_pipelines(conn, zip_path: Path | None = None) -> int:
    return load_bsee_zip(conn, download_bsee_zip(zip_path))


def blm_feature_row(feature: dict, state: str) -> tuple | None:
    attrs = feature.get("attributes") or {}
    if not blm_is_oilfield_row(attrs):
        return None
    geom = _paths_to_geom(feature)
    if geom is None:
        return None
    bbox = STATE_BBOX[state]
    clipped = _clip_lines(
        geom, bbox["lon_min"], bbox["lat_min"], bbox["lon_max"], bbox["lat_max"]
    )
    if clipped is None:
        return None
    object_id = attrs.get("OBJECTID") or attrs.get("objectid")
    if object_id in (None, ""):
        return None
    name = _text(attrs.get("CASENAME"))
    commodity, commodity_desc = blm_commodity(name)
    blob = name.lower()
    systype = "G" if any(token in blob for token in ("gather", "flowline", "swd")) else "T"
    serial = _text(attrs.get("SERIALNUMBER"), attrs.get("LEG_CSE_NR"))
    return _row_tuple(
        tpms_id=BLM_ID_BASE[state] + int(object_id),
        county_name=STATE_LABELS[state],
        operator=_text(attrs.get("HOLDER")),
        system_name=name or serial or "BLM ROW",
        t4_permit=serial,
        diameter=diameter_from_text(name),
        commodity=commodity,
        commodity_desc=commodity_desc,
        status="I",
        systype=systype,
        interstate="N",
        quality="blm_row",
        pipeline_id=serial or str(int(object_id)),
        geom=clipped,
        subsystem=_text(attrs.get("FACILTYPE")),
    )


def _load_blm_pipelines(conn, states: list[str], *, delay: float = 0.12) -> dict[str, int]:
    wanted = [code for code in states if code in BLM_ID_BASE]
    counts = {code: 0 for code in wanted}
    if not wanted:
        return counts
    for code in wanted:
        table = ensure_pipeline_table(conn, code)
        _delete_quality(conn, table, "blm_row")
    for feature in iter_features(BLM_ISSUED_LINES, where=BLM_PIPE_WHERE, delay=delay):
        attrs = feature.get("attributes") or {}
        geom = _paths_to_geom(feature)
        code = blm_state(attrs, geom)
        if code not in wanted:
            continue
        row = blm_feature_row(feature, code)
        if row is None:
            continue
        _upsert_rows(conn, ensure_pipeline_table(conn, code), [row])
        counts[code] += 1
        if sum(counts.values()) % 1000 == 0:
            conn.commit()
            _log(f"blm row running {counts}")
    conn.commit()
    _log(f"blm row loaded {counts}")
    return counts


def load_neighbor_pipelines(
    conn,
    states: list[str],
    *,
    skip_eia: bool = False,
    delay: float = 0.12,
    bsee_zip: Path | None = None,
) -> dict[str, int]:
    counts = {code: 0 for code in states}
    if not skip_eia:
        for state in states:
            _log(f"loading {state} EIA/HIFLD pipelines")
            counts[state] += _load_eia_pipelines(conn, state, delay=delay)
    if "la" in states:
        _log("loading Louisiana BSEE OCS pipelines")
        counts["la"] += _load_bsee_pipelines(conn, bsee_zip)
    blm_states = [code for code in states if code in BLM_ID_BASE]
    if blm_states:
        _log("loading BLM NM/OK oilfield ROW lines")
        for state, count in _load_blm_pipelines(conn, blm_states, delay=delay).items():
            counts[state] = counts.get(state, 0) + count
    return counts
