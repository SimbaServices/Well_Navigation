"""Refresh Louisiana wells with current public FracFocus headers and BSEE names."""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import requests

from wellnav.coords import to_wgs84
from wellnav.db import ROOT
from wellnav.ingest.classify import utcnow
from wellnav.ingest.neighbors import _api10, _text, _well_row
from wellnav.ingest.persist import upsert_wells
from wellnav.operators import normalize_operator_name

FRACFOCUS_ZIP = "https://www.fracfocusdata.org/digitaldownload/fracfocuscsv.zip"
BSEE_API_ZIP = "https://www.data.bsee.gov/Well/Files/APIRawData.zip"
BSEE_COMPANY_ZIP = "https://www.data.bsee.gov/Company/Files/compalldelimit.zip"
CACHE_DIR = ROOT / "data" / "la_refresh"
USER_AGENT = "Mozilla/5.0 (compatible; WellNavigation/1.0)"
LA_LAT_RANGE = (28.0, 33.4)
LA_LON_RANGE = (-94.6, -88.6)

LA_PARISH = {
    "001": "Acadia",
    "003": "Allen",
    "005": "Ascension",
    "007": "Assumption",
    "009": "Avoyelles",
    "011": "Beauregard",
    "013": "Bienville",
    "015": "Bossier",
    "017": "Caddo",
    "019": "Calcasieu",
    "021": "Caldwell",
    "023": "Cameron",
    "025": "Catahoula",
    "027": "Claiborne",
    "029": "Concordia",
    "031": "De Soto",
    "033": "East Baton Rouge",
    "035": "East Carroll",
    "037": "East Feliciana",
    "039": "Evangeline",
    "041": "Franklin",
    "043": "Grant",
    "045": "Iberia",
    "047": "Iberville",
    "049": "Jackson",
    "051": "Jefferson",
    "053": "Jefferson Davis",
    "055": "Lafayette",
    "057": "Lafourche",
    "059": "LaSalle",
    "061": "Lincoln",
    "063": "Livingston",
    "065": "Madison",
    "067": "Morehouse",
    "069": "Natchitoches",
    "071": "Orleans",
    "073": "Ouachita",
    "075": "Plaquemines",
    "077": "Pointe Coupee",
    "079": "Rapides",
    "081": "Red River",
    "083": "Richland",
    "085": "Sabine",
    "087": "St. Bernard",
    "089": "St. Charles",
    "091": "St. Helena",
    "093": "St. James",
    "095": "St. John the Baptist",
    "097": "St. Landry",
    "099": "St. Martin",
    "101": "St. Mary",
    "103": "St. Tammany",
    "105": "Tangipahoa",
    "107": "Tensas",
    "109": "Terrebonne",
    "111": "Union",
    "113": "Vermilion",
    "115": "Vernon",
    "117": "Washington",
    "119": "Webster",
    "121": "West Baton Rouge",
    "123": "West Carroll",
    "125": "West Feliciana",
    "127": "Winn",
}


def _log(message: str) -> None:
    print(message, flush=True)


def _col(row: dict, *names: str) -> str:
    keys = {str(key).strip().lower(): key for key in row}
    for name in names:
        key = keys.get(name.lower())
        if key is None:
            continue
        text = _text(row.get(key))
        if text:
            return text
    return ""


def _force_west_lon(lon: float, lat: float) -> float:
    if lon > 0 and LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1]:
        return -lon
    return lon


def _in_louisiana(lat: float, lon: float) -> bool:
    return LA_LAT_RANGE[0] <= lat <= LA_LAT_RANGE[1] and LA_LON_RANGE[0] <= lon <= LA_LON_RANGE[1]


def _fracfocus_coords(lat_raw: str, lon_raw: str, projection: str) -> tuple[float, float] | None:
    try:
        lat = float(lat_raw)
        lon = float(lon_raw)
    except (TypeError, ValueError):
        return None
    lon = _force_west_lon(lon, lat)
    proj = projection.lower()
    try:
        if "27" in proj:
            point = to_wgs84(lon, lat, "nad27")
            lat, lon = point.lat, point.lon
        elif "83" in proj:
            point = to_wgs84(lon, lat, "nad83")
            lat, lon = point.lat, point.lon
    except Exception:
        pass
    if not _in_louisiana(lat, lon):
        return None
    return lat, lon


def fracfocus_row_to_well(row: dict) -> dict | None:
    api = _api10(_col(row, "APINumber", "api_number", "api"), "la")
    if not api:
        return None
    coords = _fracfocus_coords(
        _col(row, "Latitude", "lat"),
        _col(row, "Longitude", "lon"),
        _col(row, "Projection"),
    )
    if coords is None:
        return None
    lat, lon = coords
    name = _col(row, "WellName", "well_name")
    operator = _col(row, "OperatorName", "operator")
    parish_code = "".join(ch for ch in _col(row, "CountyNumber", "county_number") if ch.isdigit()).zfill(3)[-3:]
    county = _col(row, "CountyName", "county") or LA_PARISH.get(parish_code, "")
    if county.lower().endswith(" parish"):
        county = county[: -len(" parish")]
    well = _well_row(
        "la",
        api,
        well_name=name,
        operator=operator,
        operator_number="",
        county=county,
        well_type=_col(row, "ProductionType", "well_type") or "stimulated",
        symbol="FracFocus",
        lat=lat,
        lon=lon,
        source="la_fracfocus",
    )
    if parish_code in LA_PARISH:
        well["county_code"] = parish_code
    return well


def _date_key(value: str) -> str:
    digits = "".join(ch for ch in value if ch.isdigit())
    return digits[:8] if len(digits) >= 8 else value


def iter_fracfocus_la_rows(zip_path: Path):
    latest: dict[str, tuple[str, dict]] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = [
            name
            for name in archive.namelist()
            if name.lower().endswith(".csv")
            and Path(name).name.lower().startswith(("disclosurelist", "registryupload"))
        ]
        if not names:
            raise RuntimeError("FracFocus zip has no DisclosureList or RegistryUpload CSV files")
        for name in names:
            with archive.open(name) as raw:
                text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
                reader = csv.DictReader(text)
                for row in reader:
                    state = _col(row, "StateName", "StateNumber").lower()
                    api_digits = "".join(ch for ch in _col(row, "APINumber", "api") if ch.isdigit())
                    if not state.startswith("louis") and state != "17" and not api_digits.startswith("17"):
                        continue
                    well = fracfocus_row_to_well(row)
                    if not well:
                        continue
                    stamp = _date_key(_col(row, "JobEndDate", "JobStartDate"))
                    prev = latest.get(well["api"])
                    if prev is None or stamp >= prev[0]:
                        latest[well["api"]] = (stamp, well)
    for _, well in latest.values():
        yield well


def bsee_api_identity(row: dict) -> dict | None:
    api = _api10(_col(row, "API_WELL_NUMBER", "API"), "la")
    if not api:
        return None
    hole = _col(row, "WELL_NAME").lstrip("0") or _col(row, "WELL_NAME")
    area = _col(row, "SURF_AREA_CODE")
    block = _col(row, "SURF_BLOCK_NUMBER").lstrip("0") or _col(row, "SURF_BLOCK_NUMBER")
    lease = _col(row, "SURF_LEASE_NUMBER").lstrip("0") or _col(row, "SURF_LEASE_NUMBER")
    if area and block and hole:
        well_name = f"{area} {block} #{hole}"
    elif hole:
        well_name = f"OCS #{hole}"
    else:
        well_name = ""
    return {
        "api": api,
        "well_name": well_name,
        "well_no": hole,
        "lease_name": f"OCS-G {lease}" if lease else "",
        "lease_no": lease,
        "operator": _col(row, "COMPANY_NAME"),
        "suffix": _col(row, "WELL_NAME_SUFFIX"),
        "status_date": _date_key(_col(row, "BOREHOLE_STAT_DT")),
    }


def iter_bsee_api_identities(zip_path: Path):
    chosen: dict[str, dict] = {}
    with zipfile.ZipFile(zip_path) as archive:
        name = next(
            item
            for item in archive.namelist()
            if item.lower().endswith(".txt") and "wellcomp" not in item.lower()
        )
        with archive.open(name) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace", newline="")
            reader = csv.DictReader(text)
            for row in reader:
                item = bsee_api_identity(row)
                if not item:
                    continue
                current = chosen.get(item["api"])
                if current is None:
                    chosen[item["api"]] = item
                    continue
                better_suffix = item["suffix"].startswith("ST00") and not current["suffix"].startswith("ST00")
                newer = item["status_date"] >= current["status_date"] and item["operator"]
                if better_suffix or (newer and not current["suffix"].startswith("ST00")):
                    chosen[item["api"]] = item
    return chosen


def load_bsee_companies(zip_path: Path) -> dict[str, str]:
    names: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        with archive.open(archive.namelist()[0]) as raw:
            text = io.TextIOWrapper(raw, encoding="latin-1", errors="replace", newline="")
            reader = csv.reader(text)
            for row in reader:
                if len(row) < 3:
                    continue
                number = "".join(ch for ch in row[0] if ch.isdigit()).zfill(5)
                name = _text(row[2]) or _text(row[3] if len(row) > 3 else "")
                if number and name:
                    names[number] = name
    return names


def download_file(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    headers = {"User-Agent": USER_AGENT}
    if dest.is_file() and dest.stat().st_size > 1024:
        head = requests.head(url, headers=headers, timeout=30, allow_redirects=True)
        length = int(head.headers.get("Content-Length") or 0)
        if length and dest.stat().st_size == length:
            _log(f"using cached {dest.name}")
            return dest
    _log(f"downloading {url}")
    with requests.get(url, headers=headers, stream=True, timeout=120) as resp:
        resp.raise_for_status()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with tmp.open("wb") as handle:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if chunk:
                    handle.write(chunk)
        tmp.replace(dest)
    _log(f"saved {dest} ({dest.stat().st_size} bytes)")
    return dest


def _load_fracfocus(conn, cache: Path) -> int:
    zip_path = download_file(FRACFOCUS_ZIP, cache / "fracfocuscsv.zip")
    existing = {row[0] for row in conn.execute("SELECT api FROM wells_la")}
    batch: list[dict] = []
    added = 0
    patched = 0
    now = utcnow()
    for well in iter_fracfocus_la_rows(zip_path):
        if well["api"] in existing:
            if well["operator"]:
                cursor = conn.execute(
                    """
                    UPDATE wells_la
                    SET operator = ?, updated_at = ?
                    WHERE api = ? AND TRIM(COALESCE(operator, '')) != ?
                    """,
                    (well["operator"], now, well["api"], well["operator"]),
                )
                patched += max(cursor.rowcount, 0)
            continue
        batch.append(well)
        existing.add(well["api"])
        if len(batch) >= 500:
            upsert_wells(conn, "la", batch)
            added += len(batch)
            batch = []
    if batch:
        upsert_wells(conn, "la", batch)
        added += len(batch)
    conn.commit()
    _log(f"la fracfocus added {added} patched {patched}")
    return added


def _enrich_bsee(conn, cache: Path) -> int:
    api_zip = download_file(BSEE_API_ZIP, cache / "APIRawData.zip")
    identities = iter_bsee_api_identities(api_zip)
    companies: dict[str, str] = {}
    try:
        company_zip = download_file(BSEE_COMPANY_ZIP, cache / "compalldelimit.zip")
        companies = load_bsee_companies(company_zip)
    except Exception as exc:
        _log(f"bsee companies skipped: {exc}")
    now = utcnow()
    updated = 0
    rows = conn.execute(
        """
        SELECT api, well_name, operator, operator_number, lease_name
        FROM wells_la
        WHERE source = 'la_bsee'
        """
    ).fetchall()
    for api, well_name, operator, operator_number, lease_name in rows:
        ident = identities.get(api)
        new_name = (ident or {}).get("well_name") or (
            f"OCS #{well_name}" if well_name and not str(well_name).upper().startswith(("OCS", "GI", "MC", "EW", "VR", "ST", "WD", "SP", "SS", "SM", "EI", "WC")) else well_name
        )
        new_operator = normalize_operator_name(
            (ident or {}).get("operator")
            or companies.get(
                "".join(ch for ch in str(operator_number or operator or "") if ch.isdigit()).zfill(5),
                operator,
            )
        )
        new_lease = (ident or {}).get("lease_name") or lease_name
        new_lease_no = (ident or {}).get("lease_no") or ""
        new_well_no = (ident or {}).get("well_no") or ""
        if (
            new_name == well_name
            and new_operator == operator
            and new_lease == lease_name
        ):
            continue
        conn.execute(
            """
            UPDATE wells_la
            SET well_name = ?, well_no = COALESCE(NULLIF(?, ''), well_no),
                lease_name = ?, lease_no = COALESCE(NULLIF(?, ''), lease_no),
                operator = ?, updated_at = ?
            WHERE api = ? AND source = 'la_bsee'
            """,
            (new_name, new_well_no, new_lease, new_lease_no, new_operator, now, api),
        )
        updated += 1
    conn.commit()
    _log(f"la bsee names updated {updated}")
    return updated


def refresh_louisiana(conn, *, cache_dir: Path | None = None) -> dict:
    cache = Path(cache_dir) if cache_dir else CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    fracfocus = _load_fracfocus(conn, cache)
    bsee = _enrich_bsee(conn, cache)
    from wellnav.ingest.la_operators import sync_louisiana_catalog

    catalog = sync_louisiana_catalog(conn)
    return {"fracfocus": fracfocus, "bsee_updated": bsee, "catalog": catalog}
