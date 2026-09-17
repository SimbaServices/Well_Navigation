from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from wellnav.disposal import connect, get_site, init_schema, query_geojson, search_sites
from wellnav.ingest.disposal import feature_to_row, upsert_site


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

            by_op = search_sites("R360", mode="operator", path=db_path)
            self.assertEqual(len(by_op), 1)
            self.assertEqual(by_op[0]["permit_no"], "STF-0006")

            site = get_site(2, path=db_path)
            self.assertIsNotNone(site)
            assert site is not None
            self.assertAlmostEqual(site["lon"], -99.069197)

            geo = query_geojson("-100,26,-97,28", path=db_path)
            self.assertEqual(geo["meta"]["stored"], 2)
            self.assertEqual(len(geo["features"]), 2)
            ids = {feat["id"] for feat in geo["features"]}
            self.assertEqual(ids, {2, 3})
