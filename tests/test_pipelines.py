from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

import shapefile

from wellnav.coords import transform_line_nad27
from wellnav.ingest.pipelines import load_pipeline_zips, load_shapefile_zip
from wellnav.mft import parse_share_listing, parse_size_label
from wellnav.pipelines import (
    connect,
    init_schema,
    list_systems,
    owner_summary,
    query_geojson,
    search_operators,
    search_systems,
    segment_detail,
)
from wellnav.db import connect as well_connect
from wellnav.db import init_schema as init_well_schema


LISTING_HTML = """
<form id="fileList" action="/webclient/godrive/PublicGoDrive.xhtml" method="post">
<table>
<tr data-ri="0" data-rk="3320" class="ui-widget-content GoDriveItem FileItem">
  <td class="NameColumn"><a id="fileTable:0:j_id_2f" href="#">pipeline001.zip</a></td>
  <td class="SizeColumn">622.71 KB</td>
</tr>
<tr data-ri="1" data-rk="3321" class="ui-widget-content GoDriveItem FileItem">
  <td class="NameColumn"><a id="fileTable:1:j_id_2f" href="#">pipeline003.zip</a></td>
  <td class="SizeColumn">2.81 MB</td>
</tr>
</table>
<input name="fileList_SUBMIT" type="hidden" value="1"/>
<input name="javax.faces.ViewState" type="hidden" value="VIEWSTATE123"/>
</form>
<script>PrimeFaces.cw("DataTable","files",{id:"fileTable",paginator:{rowCount:255}});</script>
"""


def _write_pipeline_zip(path: Path) -> Path:
    shp_dir = path.parent / "shp"
    shp_dir.mkdir(parents=True, exist_ok=True)
    writer = shapefile.Writer(str(shp_dir / "pipe001l"), shapeType=shapefile.POLYLINE)
    writer.field("TPMS_ID", "N", size=10, decimal=0)
    writer.field("OPER_NM", "C", size=40)
    writer.field("P5_NUM", "C", size=6)
    writer.field("SYS_NM", "C", size=40)
    writer.field("SUBSYS_NM", "C", size=40)
    writer.field("T4PERMIT", "C", size=5)
    writer.field("DIAMETER", "N", size=6, decimal=2)
    writer.field("COMMODITY1", "C", size=3)
    writer.field("CMDTY_DESC", "C", size=40)
    writer.field("INTERSTATE", "C", size=1)
    writer.field("STATUS_CD", "C", size=1)
    writer.field("QUALITY_CD", "C", size=1)
    writer.field("SYSTYPE", "C", size=1)
    writer.field("COUNTY", "C", size=3)
    writer.field("PLINE_ID", "C", size=20)
    writer.line([[[-95.92, 31.90], [-95.90, 31.91]]])
    writer.record(
        101, "TEST GAS LLC", "123456", "MAIN LINE", "", "01234", 16.0, "NGG",
        "NATURAL GAS", "N", "I", "E", "T", "001", "L1",
    )
    writer.line([[[-95.80, 31.88], [-95.79, 31.89]]])
    writer.record(
        102, "TEST GATHERING", "654321", "GATHER", "", "05678", 2.38, "NGG",
        "NATURAL GAS", "N", "I", "E", "G", "001", "L2",
    )
    writer.line([[[-95.70, 31.70], [-95.69, 31.71]]])
    writer.record(
        103, "OLD LINE", "000111", "ABANDONED", "", "09999", 8.0, "CRL",
        "CRUDE OIL", "N", "B", "U", "T", "001", "L3",
    )
    writer.close()
    with zipfile.ZipFile(path, "w") as archive:
        for part in shp_dir.glob("pipe001l.*"):
            archive.write(part, part.name)
    return path


class MftListingTests(unittest.TestCase):
    def test_parse_share_listing(self) -> None:
        files, meta = parse_share_listing(LISTING_HTML)
        self.assertEqual([item.name for item in files], ["pipeline001.zip", "pipeline003.zip"])
        self.assertEqual(files[0].link_id, "fileTable:0:j_id_2f")
        self.assertEqual(files[0].size_bytes, parse_size_label("622.71 KB"))
        self.assertEqual(meta["viewstate"], "VIEWSTATE123")
        self.assertEqual(meta["total"], 255)

    def test_parse_size_label(self) -> None:
        self.assertEqual(parse_size_label("2.81 MB"), int(2.81 * 1024 * 1024))
        self.assertIsNone(parse_size_label("n/a"))


class PipelineTransformTests(unittest.TestCase):
    def test_nad27_line_stays_in_east_texas(self) -> None:
        converted = transform_line_nad27([(-95.92, 31.90), (-95.90, 31.91)])
        self.assertEqual(len(converted), 2)
        lon, lat = converted[0]
        self.assertTrue(-97 < lon < -94)
        self.assertTrue(31 < lat < 33)
        self.assertNotAlmostEqual(lon, -95.92, places=5)


class PipelineIngestTests(unittest.TestCase):
    def test_load_and_query_by_zoom(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = _write_pipeline_zip(root / "pipeline001.zip")
            db_path = root / "pipelines.db"
            conn = connect(db_path)
            init_schema(conn)
            written = load_shapefile_zip(zip_path, conn)
            conn.commit()
            conn.close()
            self.assertEqual(written, 3)

            statewide = query_geojson(
                bbox="-96.2,31.4,-95.4,32.1",
                zoom=6,
                path=db_path,
            )
            ids = {feat["id"] for feat in statewide["features"]}
            self.assertIn(101, ids)
            self.assertNotIn(102, ids)
            self.assertNotIn(103, ids)

            detail = query_geojson(
                bbox="-96.2,31.4,-95.4,32.1",
                zoom=13,
                path=db_path,
            )
            self.assertEqual({feat["id"] for feat in detail["features"]}, {101, 102})

            abandoned = query_geojson(
                bbox="-96.2,31.4,-95.4,32.1",
                zoom=13,
                abandoned=True,
                path=db_path,
            )
            self.assertEqual({feat["id"] for feat in abandoned["features"]}, {101, 102, 103})
            gas = next(feat for feat in detail["features"] if feat["id"] == 101)
            self.assertEqual(gas["properties"]["commodity_group"], "gas")
            self.assertEqual(gas["properties"]["p5"], "123456")
            self.assertEqual(gas["properties"]["subsystem"], "")
            self.assertEqual(gas["geometry"]["type"], "LineString")
            self.assertGreaterEqual(len(gas["geometry"]["coordinates"]), 2)

            gathering = query_geojson(
                bbox="-96.2,31.4,-95.4,32.1",
                zoom=6,
                p5="654321",
                path=db_path,
            )
            self.assertEqual({feat["id"] for feat in gathering["features"]}, {102})
            self.assertEqual(gathering["meta"]["p5"], "654321")

    def test_search_operator_and_system_name(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = _write_pipeline_zip(root / "pipeline001.zip")
            db_path = root / "pipelines.db"
            conn = connect(db_path)
            init_schema(conn)
            load_shapefile_zip(zip_path, conn)
            conn.commit()
            conn.close()

            operators = search_operators("TEST GAS", path=db_path)
            self.assertEqual(len(operators), 1)
            self.assertEqual(operators[0]["p5"], "123456")
            self.assertEqual(operators[0]["operator"], "TEST GAS LLC")
            self.assertGreaterEqual(operators[0]["segments"], 1)

            systems = search_systems("MAIN LINE", path=db_path)
            self.assertEqual(len(systems), 1)
            self.assertEqual(systems[0]["system"], "MAIN LINE")
            self.assertEqual(systems[0]["p5"], "123456")

            listed = list_systems(p5="123456", path=db_path)
            self.assertEqual([row["system"] for row in listed], ["MAIN LINE"])

    def test_segment_ownership_joins_p5(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zip_path = _write_pipeline_zip(root / "pipeline001.zip")
            db_path = root / "pipelines.db"
            conn = connect(db_path)
            init_schema(conn)
            load_shapefile_zip(zip_path, conn)
            conn.commit()
            conn.close()

            identity_db = root / "wellnav.db"
            wells = well_connect(identity_db)
            init_well_schema(wells)
            wells.execute(
                """
                INSERT INTO operators_tx(
                    operator_number, operator_name, org_status, org_type, status, wells
                ) VALUES ('123456', 'TEST GAS RRC ORG', 'Active', 'Corporation', 'ready', 12)
                """
            )
            wells.commit()
            wells.close()

            detail = segment_detail(101, path=db_path, identity_db=identity_db)
            self.assertIsNotNone(detail)
            self.assertEqual(detail["operator"], "TEST GAS LLC")
            self.assertEqual(detail["p5"], "123456")
            self.assertEqual(detail["t4"], "01234")
            self.assertEqual(detail["identity"]["name"], "TEST GAS RRC ORG")
            self.assertEqual(detail["identity"]["org_status"], "Active")
            self.assertEqual(detail["summary"]["segments"], 1)

            summary = owner_summary(p5="123456", path=db_path, identity_db=identity_db)
            self.assertEqual(summary["operator"], "TEST GAS LLC")
            self.assertEqual(summary["identity"]["wells"], 12)

    def test_load_pipeline_zips_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            zips = root / "zips"
            zips.mkdir()
            _write_pipeline_zip(zips / "pipeline001.zip")
            db_path = root / "pipelines.db"
            result = load_pipeline_zips(zips, db_path)
            self.assertEqual(result["unique"], 3)
            self.assertEqual(result["loaded"], 1)


if __name__ == "__main__":
    unittest.main()
