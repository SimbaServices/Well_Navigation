"""Download RRC county pipeline shapefile zips and load WGS84 lines into SQLite."""

from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import shapefile
from shapely.geometry import shape
from shapely import wkb

from wellnav.db import ROOT
from wellnav.mft import GoAnywhereShare, ShareFile, discover_pipeline_share_url
from wellnav.pipelines import (
    PIPE_DB_PATH,
    connect,
    geometry_wgs84_from_nad27,
    init_schema,
)
from wellnav.states import TX_COUNTY_NAME

ZIP_DIR = ROOT / "data" / "pipelines" / "zips"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(message: str) -> None:
    print(message, flush=True)


def download_pipeline_zips(
    dest: Path | None = None,
    *,
    limit: int | None = None,
    skip_existing: bool = True,
) -> dict:
    dest_dir = dest or ZIP_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    share_url = discover_pipeline_share_url()
    _log(f"pipeline share {share_url}")
    share = GoAnywhereShare(share_url)
    files = [
        item
        for item in share.list_all()
        if item.name.lower().endswith(".zip") and "pipeline" in item.name.lower()
    ]
    files.sort(key=lambda item: item.name)
    wanted = {item.name for item in files}
    if limit:
        files = files[: int(limit)]
        wanted = {item.name for item in files}
    _log(f"pipeline zips listed {len(files)}")
    visible = [
        item
        for item in share.open(rows=max(1000, len(files)))
        if item.name in wanted
    ]
    downloaded = 0
    skipped = 0
    failed: list[str] = []
    seen: set[str] = set()

    def pull(items: list[ShareFile]) -> None:
        nonlocal downloaded, skipped
        for item in items:
            if item.name in seen:
                continue
            seen.add(item.name)
            target = dest_dir / item.name
            if skip_existing and _zip_looks_complete(target, item):
                skipped += 1
                continue
            try:
                share.download(item, target)
                downloaded += 1
                _log(f"downloaded {item.name} ({len(seen)}/{len(files)})")
            except Exception as exc:
                _log(f"retry {item.name} after {exc}")
                try:
                    share.open(rows=1000)
                    item = share.file_named(item.name) or item
                    share.download(item, target)
                    downloaded += 1
                    _log(f"downloaded {item.name} ({len(seen)}/{len(files)})")
                except Exception as retry_exc:
                    failed.append(f"{item.name}: {retry_exc}")
                    _log(f"failed {item.name}: {retry_exc}")

    pull(visible)
    first = len(visible)
    while len(seen) < len(files):
        page = [item for item in share._request_page(first=first, rows=250) if item.name in wanted]
        if not page:
            missing = wanted - seen
            if missing:
                failed.extend(f"{name}: not listed on a downloadable page" for name in sorted(missing))
            break
        pull(page)
        first += 250
    return {
        "share_url": share_url,
        "listed": len(wanted),
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "dir": str(dest_dir),
    }


def _zip_looks_complete(path: Path, item: ShareFile) -> bool:
    if not path.is_file() or path.stat().st_size < 64:
        return False
    if item.size_bytes and abs(path.stat().st_size - item.size_bytes) > max(2048, item.size_bytes * 0.05):
        return False
    return zipfile.is_zipfile(path)


def load_shapefile_zip(zip_path: Path, conn) -> int:
    inserted = 0
    with tempfile.TemporaryDirectory() as tmp:
        extract_dir = Path(tmp)
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(extract_dir)
        shapefiles = sorted(extract_dir.rglob("*.shp"))
        rows: list[tuple] = []
        rtree_rows: list[tuple] = []
        for shp in shapefiles:
            rows.extend(_records_from_shapefile(shp, rtree_rows))
        if not rows:
            return 0
        conn.executemany(
            """
            INSERT INTO pipelines_tx(
                tpms_id, county_code, county_name, operator, p5_num, system_name,
                subsystem, t4_permit, diameter, commodity, commodity_desc, status,
                systype, interstate, quality, pipeline_id, geom, minx, miny, maxx, maxy
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(tpms_id) DO UPDATE SET
                county_code=excluded.county_code,
                county_name=excluded.county_name,
                operator=excluded.operator,
                p5_num=excluded.p5_num,
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
                minx=excluded.minx,
                miny=excluded.miny,
                maxx=excluded.maxx,
                maxy=excluded.maxy
            """,
            rows,
        )
        try:
            conn.executemany(
                """
                INSERT OR REPLACE INTO pipelines_tx_rtree(id, minx, maxx, miny, maxy)
                VALUES (?,?,?,?,?)
                """,
                rtree_rows,
            )
        except Exception:
            try:
                conn.execute("DROP TABLE IF EXISTS pipelines_tx_rtree")
            except Exception:
                pass
        inserted = len(rows)
    return inserted


def _field_name(field) -> str:
    return field.name if hasattr(field, "name") else field[0]


def _records_from_shapefile(shp_path: Path, rtree_rows: list[tuple]) -> list[tuple]:
    rows: list[tuple] = []
    reader = shapefile.Reader(str(shp_path))
    try:
        if getattr(reader, "shapeTypeName", "") not in {"POLYLINE", "POLYLINEZ", "POLYLINEM"} and reader.shapeType not in {3, 13, 23}:
            return rows
        fields = [_field_name(field) for field in reader.fields if _field_name(field) != "DeletionFlag"]
        for index, sr in enumerate(reader.iterShapeRecords()):
            rec = sr.record.as_dict() if hasattr(sr.record, "as_dict") else dict(zip(fields, sr.record))
            geo = sr.shape.__geo_interface__
            converted = geometry_wgs84_from_nad27(geo)
            if converted is None:
                continue
            geom = shape(converted)
            if geom.is_empty:
                continue
            minx, miny, maxx, maxy = geom.bounds
            tpms_id = _as_int(rec.get("TPMS_ID")) or _as_int(rec.get("OBJECTID"))
            if not tpms_id:
                tpms_id = abs(hash((shp_path.name, index))) % (10**9)
            county_code = str(rec.get("COUNTY") or "").strip().zfill(3)[:3]
            county_name = TX_COUNTY_NAME.get(county_code, "")
            blob = wkb.dumps(geom, hex=False)
            rows.append(
                (
                    tpms_id,
                    county_code,
                    county_name,
                    _text(rec.get("OPER_NM")),
                    _text(rec.get("P5_NUM")),
                    _text(rec.get("SYS_NM")),
                    _text(rec.get("SUBSYS_NM")),
                    _text(rec.get("T4PERMIT")),
                    _as_float(rec.get("DIAMETER")),
                    _text(rec.get("COMMODITY1")),
                    _text(rec.get("CMDTY_DESC")),
                    _text(rec.get("STATUS_CD")),
                    _text(rec.get("SYSTYPE")),
                    _text(rec.get("INTERSTATE")),
                    _text(rec.get("QUALITY_CD")),
                    _text(rec.get("PLINE_ID")),
                    blob,
                    minx,
                    miny,
                    maxx,
                    maxy,
                )
            )
            rtree_rows.append((tpms_id, minx, maxx, miny, maxy))
    finally:
        reader.close()
    return rows


def _text(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _as_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_pipeline_zips(zip_dir: Path | None = None, db_path: Path | None = None) -> dict:
    source = zip_dir or ZIP_DIR
    zips = sorted(source.glob("pipeline*.zip"))
    conn = connect(db_path or PIPE_DB_PATH)
    init_schema(conn)
    loaded = 0
    features = 0
    errors: list[str] = []
    for index, zip_path in enumerate(zips, start=1):
        try:
            count = load_shapefile_zip(zip_path, conn)
            conn.commit()
            loaded += 1
            features += count
            if index == 1 or index % 20 == 0 or index == len(zips):
                _log(f"loaded {zip_path.name} +{count} ({index}/{len(zips)})")
        except Exception as exc:
            conn.rollback()
            errors.append(f"{zip_path.name}: {exc}")
            _log(f"load failed {zip_path.name}: {exc}")
    total = conn.execute("SELECT COUNT(*) AS n FROM pipelines_tx").fetchone()["n"]
    conn.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("pipelines_loaded_at", _now()),
    )
    conn.commit()
    conn.close()
    return {
        "zips": len(zips),
        "loaded": loaded,
        "features_written": features,
        "unique": int(total),
        "errors": errors,
        "db": str(db_path or PIPE_DB_PATH),
    }


def load_pipelines(
    *,
    skip_download: bool = False,
    limit: int | None = None,
    zip_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict:
    result: dict = {}
    if not skip_download:
        result["download"] = download_pipeline_zips(zip_dir or ZIP_DIR, limit=limit)
        share_url = result["download"].get("share_url")
        if share_url:
            conn = connect(db_path or PIPE_DB_PATH)
            init_schema(conn)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                ("pipelines_share_url", share_url),
            )
            conn.commit()
            conn.close()
    result["load"] = load_pipeline_zips(zip_dir or ZIP_DIR, db_path)
    result["status"] = "ok" if not result["load"].get("errors") else "partial"
    if result.get("download", {}).get("failed"):
        result["status"] = "partial"
    return result
