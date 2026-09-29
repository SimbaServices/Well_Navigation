from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wellnav.disposal import (
    DRIVE_MPH,
    KM_PER_MI,
    ROAD_FACTOR,
    SWD_COMMERCIAL,
    SWD_OPERATOR,
    classify_swd,
    connect,
    distance_km,
    drive_minutes,
    get_site,
    init_schema,
    nearest_sites,
    query_geojson,
    search_sites,
    waste_classifications_for,
)
from wellnav.ingest.disposal import feature_to_row, uic_feature_to_row, upsert_site


def _feature(oid: int, **attrs: object) -> dict:
    payload = {
        "OBJECTID": oid,
        "OPERATOR_NAME": "R360 ENV SOLUTIONS OF TX, LLC",
        "LEASE_OR_FACILITY_NAME": "SOUTH TEXAS DISPOSAL STF FACILITY",
        "PERMIT_NO": "STF-0006",
        "PERMIT_TYPE": "STATIONARY TREATMENT FACILITY",
        "DISCHARGE_TYPE": None,
        "PERMIT_EXPIRATION": None,
        "LATITUDE": 26.983848,
        "LONGITUDE": -99.069197,
        "RRC_DISTRICT_OFFICE": "04",
        "COUNTY": "ZAPATA",
        "PERMIT_URL": "http://rrc.texas.gov/media/example.pdf",
    }
    payload.update(attrs)
    return {
        "attributes": payload,
        "geometry": {"x": payload["LONGITUDE"], "y": payload["LATITUDE"]},
    }


def _seed_nearest_fixture(db_path: Path) -> None:
    conn = connect(db_path)
    init_schema(conn)
    near = feature_to_row(
        _feature(
            10,
            LEASE_OR_FACILITY_NAME="NEARBY RECLAMATION YARD",
            PERMIT_NO="R-NEAR",
            PERMIT_TYPE="RECLAMATION_PLANT",
            DISCHARGE_TYPE="Oil and gas waste",
            LATITUDE=29.76,
            LONGITUDE=-95.37,
            COUNTY="HARRIS",
        )
    )
    far = feature_to_row(
        _feature(
            11,
            OPERATOR_NAME="WEST TEXAS WASTE LLC",
            LEASE_OR_FACILITY_NAME="FAR WEST PIT",
            PERMIT_NO="PIT-FAR",
            PERMIT_TYPE="PIT",
            LATITUDE=31.85,
            LONGITUDE=-102.37,
            COUNTY="ECTOR",
        )
    )
    radium = feature_to_row(
        _feature(
            12,
            OPERATOR_NAME="NORM SERVICES INC",
            LEASE_OR_FACILITY_NAME="MIDLAND RADIUM / TENORM CELL",
            PERMIT_NO="NORM-1",
            PERMIT_TYPE="LANDFILL",
            DISCHARGE_TYPE="TENORM / radium waste",
            LATITUDE=31.99,
            LONGITUDE=-102.08,
            COUNTY="MIDLAND",
        )
    )
    assert near is not None and far is not None and radium is not None
    upsert_site(conn, near)
    upsert_site(conn, far)
    upsert_site(conn, radium)
    conn.commit()
    conn.close()


class DisposalIngestTests(unittest.TestCase):
    def test_feature_to_row_keeps_wgs84_and_https_permit(self) -> None:
        row = feature_to_row(_feature(2))
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["id"], 2)
        self.assertEqual(row["facility"], "SOUTH TEXAS DISPOSAL STF FACILITY")
        self.assertEqual(row["county"], "ZAPATA")
        self.assertAlmostEqual(row["lat"], 26.983848)
        self.assertTrue(row["permit_url"].startswith("https://www.rrc.texas.gov/"))

    def test_rejects_out_of_state_point(self) -> None:
        self.assertIsNone(feature_to_row(_feature(9, LATITUDE=40.0, LONGITUDE=-74.0)))


class DisposalSearchTests(unittest.TestCase):
    def test_search_and_geojson(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "disposal.db"
            conn = connect(db_path)
            init_schema(conn)
            first = feature_to_row(_feature(2))
            second = feature_to_row(
                _feature(
                    3,
                    OPERATOR_NAME="THE MAKER'S OIL CORPORATION",
                    LEASE_OR_FACILITY_NAME="NUECES CO - CORPUS CHRISTI RECLAMATION FACILITY",
                    PERMIT_NO="R9 04-1309A",
                    PERMIT_TYPE="RECLAMATION_PLANT",
                    DISCHARGE_TYPE="Produced water",
                    LATITUDE=27.711234,
                    LONGITUDE=-97.46324,
                    COUNTY="NUECES",
                )
            )
            assert first is not None and second is not None
            upsert_site(conn, first)
            upsert_site(conn, second)
            conn.commit()
            conn.close()

            by_name = search_sites("corpus", mode="name", path=db_path)
            self.assertEqual(len(by_name), 1)
            self.assertEqual(by_name[0]["county"], "NUECES")
            self.assertEqual(by_name[0]["permit_type_label"], "Reclamation Plant")
            self.assertEqual(by_name[0]["permit_type"], "RECLAMATION_PLANT")
            self.assertEqual(by_name[0]["discharge_type"], "Produced water")
            self.assertEqual(
                by_name[0]["waste_classifications"],
                ["Reclamation Plant", "Produced Water"],
            )

            by_op = search_sites("R360", mode="operator", path=db_path)
            self.assertEqual(len(by_op), 1)
            self.assertEqual(by_op[0]["permit_no"], "STF-0006")

            site = get_site(2, path=db_path)
            self.assertIsNotNone(site)
            assert site is not None
            self.assertAlmostEqual(site["lon"], -99.069197)
            self.assertIn("waste_classifications", site)

            geo = query_geojson("-100,26,-97,28", path=db_path)
            self.assertEqual(geo["meta"]["stored"], 2)
            self.assertEqual(len(geo["features"]), 2)
            ids = {feat["id"] for feat in geo["features"]}
            self.assertEqual(ids, {2, 3})
            props = next(feat["properties"] for feat in geo["features"] if feat["id"] == 3)
            self.assertEqual(props["permit_type"], "RECLAMATION_PLANT")
            self.assertEqual(props["permit_type_label"], "Reclamation Plant")
            self.assertEqual(props["discharge_type"], "Produced water")
            self.assertEqual(props["waste_classifications"], ["Reclamation Plant", "Produced Water"])
            self.assertEqual(props["swd_class"], SWD_COMMERCIAL)
            self.assertEqual(props["swd_class_label"], "Commercial public SWD")

    def test_waste_classifications_dedupe(self) -> None:
        labels = waste_classifications_for(
            permit_type="LANDFILL",
            discharge_type="landfill",
        )
        self.assertEqual(labels, ["Landfill"])
        self.assertEqual(
            waste_classifications_for(permit_type="N/A", discharge_type=""),
            [],
        )


class DisposalNearestTests(unittest.TestCase):
    def test_nearest_sort_and_radium_filter(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "disposal.db"
            _seed_nearest_fixture(db_path)

            # Houston downtown — nearest should be the Harris County yard.
            near = nearest_sites(29.7604, -95.3698, limit=10, path=db_path)
            self.assertEqual(len(near), 3)
            self.assertEqual(near[0]["id"], 10)
            self.assertLess(near[0]["distance_km"], near[1]["distance_km"])
            self.assertLess(near[1]["distance_km"], near[2]["distance_km"])
            self.assertIn("distance_mi", near[0])
            self.assertAlmostEqual(
                near[0]["distance_mi"],
                near[0]["distance_km"] / 1.609344,
                places=2,
            )

            # Radium-only from Midland: only the TENORM cell, not silent fallback.
            radium = nearest_sites(
                31.9973,
                -102.0779,
                limit=10,
                radium_only=True,
                path=db_path,
            )
            self.assertEqual([row["id"] for row in radium], [12])
            self.assertTrue(
                any(
                    "Radium" in label or "Tenorm" in label
                    for label in radium[0]["waste_classifications"]
                )
            )

            # max_km still applies after radium filter (no silent fill with non-radium).
            too_far = nearest_sites(
                29.76,
                -95.37,
                radium_only=True,
                max_km=5,
                path=db_path,
            )
            self.assertEqual(too_far, [])

            self.assertEqual(
                search_sites("", mode="near", path=db_path),
                [],
            )
            self.assertEqual(
                search_sites("", mode="radium_near", path=db_path),
                [],
            )


class SwdClassTests(unittest.TestCase):
    def test_classify_commercial_facility_and_operator_well(self) -> None:
        self.assertEqual(
            classify_swd(permit_type="STATIONARY TREATMENT FACILITY", site_id=12),
            SWD_COMMERCIAL,
        )
        self.assertEqual(
            classify_swd(permit_type="Commercial UIC", site_id=2_100_000_010),
            SWD_COMMERCIAL,
        )
        self.assertEqual(
            classify_swd(permit_type="Salt Water Disposal", site_id=2_000_000_100),
            SWD_OPERATOR,
        )
        self.assertEqual(
            classify_swd(swd_class="operator", permit_type="Commercial disposal"),
            SWD_OPERATOR,
        )

    def test_uic_rows_keep_distinct_classes(self) -> None:
        commercial = uic_feature_to_row(
            {"attributes": {"OBJECTID": 9, "API": "00300111"}, "geometry": {"x": -101.5, "y": 31.8}},
            swd_class=SWD_COMMERCIAL,
            id_base=100_000_000,
        )
        operator = uic_feature_to_row(
            {
                "attributes": {"OBJECTID": 9, "API": "00300222"},
                "geometry": {"x": -101.4, "y": 31.7},
            },
            swd_class=SWD_OPERATOR,
            id_base=300_000_000,
            well={"well_name": "SMITH", "well_no": "1H", "operator": "OXY USA INC.", "symbol": "Injection / Disposal from Oil"},
        )
        assert commercial is not None and operator is not None
        self.assertEqual(commercial["swd_class"], SWD_COMMERCIAL)
        self.assertEqual(commercial["permit_no"], "00300111")
        self.assertEqual(operator["swd_class"], SWD_OPERATOR)
        self.assertEqual(operator["facility"], "SMITH 1H")
        self.assertNotEqual(commercial["id"], operator["id"])

    def test_map_layer_colors_commercial_and_operator_wells(self) -> None:
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "disposal.db"
            wells_path = Path(tmp) / "wells.db"
            conn = connect(db_path)
            init_schema(conn)
            commercial = feature_to_row(
                _feature(
                    4,
                    PERMIT_NO="42-003-00111",
                    LEASE_OR_FACILITY_NAME="PUBLIC SWD",
                    LATITUDE=31.80,
                    LONGITUDE=-101.50,
                )
            )
            assert commercial is not None
            upsert_site(conn, commercial)
            conn.commit()
            conn.close()

            wells = sqlite3.connect(wells_path)
            wells.execute(
                """
                CREATE TABLE wells_tx (
                    api TEXT, api8 TEXT, well_name TEXT, well_no TEXT, lease_name TEXT,
                    operator TEXT, county TEXT, district TEXT, symbol TEXT, well_type TEXT,
                    wellhead_lat REAL, wellhead_lon REAL
                )
                """
            )
            wells.executemany(
                """
                INSERT INTO wells_tx(
                    api, api8, well_name, well_no, lease_name, operator, county, district,
                    symbol, well_type, wellhead_lat, wellhead_lon
                ) VALUES (?, ?, ?, ?, '', ?, '', '', ?, '', ?, ?)
                """,
                [
                    ("42-003-00111", "00300111", "PUBLIC WELL", "1", "R360", "Injection / Disposal from Oil", 31.81, -101.51),
                    ("42-003-00222", "00300222", "LEASE SWD", "2H", "OXY USA INC.", "Injection / Disposal from Oil", 31.82, -101.52),
                    ("42-003-00333", "00300333", "PRODUCER", "3", "OXY USA INC.", "Oil Well", 31.83, -101.53),
                    ("42-003-00444", "00300444", "PLUGGED SWD", "4", "OXY USA INC.", "Plugged Injection Well", 31.84, -101.54),
                ],
            )
            wells.commit()
            wells.close()

            geo = query_geojson(
                "-102,31.5,-101,32.2",
                path=db_path,
                wells_path=wells_path,
            )
            by_class = {}
            for feat in geo["features"]:
                props = feat["properties"]
                by_class.setdefault(props["swd_class"], []).append(props["facility"])
            self.assertEqual(by_class[SWD_COMMERCIAL], ["PUBLIC SWD"])
            self.assertEqual(by_class[SWD_OPERATOR], ["LEASE SWD 2H"])
            self.assertEqual(geo["meta"]["commercial"], 1)
            self.assertEqual(geo["meta"]["operator"], 1)
            self.assertFalse(geo["meta"]["truncated"])


class DriveMinutesTests(unittest.TestCase):
    def test_same_point_is_zero(self) -> None:
        self.assertEqual(drive_minutes(31.9, -102.1, 31.9, -102.1), 0)

    def test_scales_with_road_distance(self) -> None:
        minutes = drive_minutes(31.0, -102.0, 32.0, -102.0)
        miles = distance_km(31.0, -102.0, 32.0, -102.0) / KM_PER_MI
        expected = int(round((miles * ROAD_FACTOR) / DRIVE_MPH * 60.0))
        self.assertEqual(minutes, expected)
        self.assertGreater(minutes, 90)


if __name__ == "__main__":
    unittest.main()
