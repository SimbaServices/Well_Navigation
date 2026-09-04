"""Unit tests for identity-only persist and worker skip-GIS behavior."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wellnav.db import connect, init_schema
from wellnav.ingest.identity import _refine, _spec
from wellnav.ingest.persist import update_identity, upsert_permits, upsert_wells
from wellnav.ingest.worker import run_partition


def _well(**overrides):
    now = "2026-09-04T00:00:00+00:00"
    row = {
        "api": "4200300001",
        "api8": "00300001",
        "well_name": "ANDREWS #1",
        "well_no": "1",
        "lease_name": "",
        "lease_no": "",
        "county": "ANDREWS",
        "county_code": "003",
        "district": "",
        "operator": "",
        "operator_number": "",
        "field": "",
        "well_type": "Oil",
        "symbol": "Oil Well",
        "symnum": 1,
        "profile": "vertical",
        "wellhead_lat": 32.3,
        "wellhead_lon": -102.5,
        "wellhead_crs": "WGS84",
        "toe_lat": None,
        "toe_lon": None,
        "toe_crs": None,
        "location_kind": "vertical",
        "location_source": "GIS",
        "gis_lat83": 32.3,
        "gis_long83": -102.5,
        "gis_lat27": None,
        "gis_long27": None,
        "source": "rrc_gis",
        "migrated_from_permit": 0,
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _permit(**overrides):
    now = "2026-09-04T00:00:00+00:00"
    row = {
        "api": "4200900002",
        "api8": "00900002",
        "permit_no": "GIS-00900002",
        "status": "approved",
        "well_name": "ARCHER #2",
        "well_no": "2",
        "lease_name": "",
        "lease_no": "",
        "county": "ARCHER",
        "county_code": "009",
        "district": "",
        "operator": "",
        "operator_number": "",
        "profile": "vertical",
        "symbol": "Permitted Location",
        "symnum": 2,
        "wellhead_lat": 33.6,
        "wellhead_lon": -98.6,
        "wellhead_crs": "WGS84",
        "approved_at": now,
        "submitted_at": None,
        "expires_at": "2028-09-04T00:00:00+00:00",
        "lifetime_days": 730,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "rrc_gis",
        "first_seen_at": now,
        "last_seen_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


class PersistIdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.conn = connect(Path(self.tmpdir.name) / "test.db")
        init_schema(self.conn)

    def tearDown(self):
        self.conn.close()
        self.tmpdir.cleanup()

    def test_upsert_keeps_identity_when_incoming_blank(self):
        upsert_wells(
            self.conn,
            "tx",
            [_well(lease_name="UNIV ANDREWS", operator="OXY USA INC.", district="08", field="SPRABERRY")],
        )
        upsert_wells(self.conn, "tx", [_well(wellhead_lat=32.31, wellhead_lon=-102.51)])
        row = self.conn.execute("SELECT * FROM wells_tx WHERE api8='00300001'").fetchone()
        self.assertEqual(row["lease_name"], "UNIV ANDREWS")
        self.assertEqual(row["operator"], "OXY USA INC.")
        self.assertEqual(row["district"], "08")
        self.assertEqual(row["field"], "SPRABERRY")
        self.assertEqual(row["wellhead_lat"], 32.31)
        self.assertEqual(row["wellhead_lon"], -102.51)

    def test_upsert_permits_keeps_identity_when_incoming_blank(self):
        upsert_permits(
            self.conn,
            "tx",
            [_permit(lease_name="ARCHER LEASE", operator="PIONEER", district="09")],
        )
        upsert_permits(self.conn, "tx", [_permit(wellhead_lat=33.7)])
        row = self.conn.execute("SELECT * FROM permits_tx WHERE api8='00900002'").fetchone()
        self.assertEqual(row["lease_name"], "ARCHER LEASE")
        self.assertEqual(row["operator"], "PIONEER")
        self.assertEqual(row["district"], "09")
        self.assertEqual(row["wellhead_lat"], 33.7)

    def test_upsert_replaces_identity_when_incoming_nonempty(self):
        upsert_wells(self.conn, "tx", [_well(lease_name="OLD", operator="OLD OP")])
        upsert_wells(self.conn, "tx", [_well(lease_name="NEW LEASE", operator="NEW OP")])
        row = self.conn.execute("SELECT lease_name, operator FROM wells_tx WHERE api8='00300001'").fetchone()
        self.assertEqual(row["lease_name"], "NEW LEASE")
        self.assertEqual(row["operator"], "NEW OP")

    def test_update_identity_fills_blank_and_skips_blank_incoming(self):
        upsert_wells(self.conn, "tx", [_well(lease_name="KEEP ME", operator="")])
        upsert_permits(self.conn, "tx", [_permit(lease_name="", operator="KEEP OP")])
        wells, permits = update_identity(
            self.conn,
            "tx",
            [
                {
                    "api8": "00300001",
                    "lease_name": "",
                    "operator": "OXY USA INC.",
                    "district": "08",
                    "field": "SPRABERRY",
                    "well_name": "UNIV ANDREWS #1",
                },
                {
                    "api": "00900002",
                    "lease_name": "ARCHER LEASE",
                    "operator": "",
                    "district": "09",
                },
            ],
        )
        self.assertEqual(wells, 1)
        self.assertEqual(permits, 1)
        well = self.conn.execute("SELECT * FROM wells_tx WHERE api8='00300001'").fetchone()
        permit = self.conn.execute("SELECT * FROM permits_tx WHERE api8='00900002'").fetchone()
        self.assertEqual(well["lease_name"], "KEEP ME")
        self.assertEqual(well["operator"], "OXY USA INC.")
        self.assertEqual(well["district"], "08")
        self.assertEqual(well["field"], "SPRABERRY")
        self.assertEqual(well["well_name"], "UNIV ANDREWS #1")
        self.assertEqual(well["wellhead_lat"], 32.3)
        self.assertEqual(well["wellhead_lon"], -102.5)
        self.assertEqual(permit["lease_name"], "ARCHER LEASE")
        self.assertEqual(permit["operator"], "KEEP OP")
        self.assertEqual(permit["wellhead_lat"], 33.6)

    def test_update_identity_does_not_insert_or_touch_other_county(self):
        upsert_wells(self.conn, "tx", [_well()])
        wells, permits = update_identity(
            self.conn,
            "tx",
            [{"api8": "00300001", "lease_name": "UNIV ANDREWS"}],
            county_code="009",
        )
        self.assertEqual((wells, permits), (0, 0))
        row = self.conn.execute("SELECT lease_name, wellhead_lat FROM wells_tx WHERE api8='00300001'").fetchone()
        self.assertEqual(row["lease_name"], "")
        self.assertEqual(row["wellhead_lat"], 32.3)


class WorkerIdentityOnlyTests(unittest.TestCase):
    def test_identity_only_skips_gis_and_returns_identity_dicts(self):
        identities = [
            {"api": "00300001", "api8": "00300001", "lease_name": "UNIV ANDREWS", "operator": "OXY"}
        ]
        payload = {
            "county_code": "003",
            "county_name": "ANDREWS",
            "identity_only": True,
            "delay": 0.0,
            "attempt": 1,
        }
        with (
            patch("wellnav.ingest.worker.fetch_county_identities") as fetch,
            patch("wellnav.ingest.worker.fetch_layer") as gis,
        ):
            fetch.return_value = {
                "identities": identities,
                "complete": True,
                "blocked": False,
                "scratch": {"identities": identities, "identity_queue": [], "identity_complete": True},
            }
            result = run_partition(payload)
        self.assertTrue(result["ok"])
        self.assertTrue(result["identity_only"])
        self.assertEqual(result["identities"][0]["lease_name"], "UNIV ANDREWS")
        self.assertEqual(result["default_features"], 0)
        self.assertEqual(result["surface_features"], 0)
        gis.assert_not_called()

    def test_identity_refine_starts_at_schedule_and_lease_type(self):
        kids = _refine(_spec(schedule="Both"), "003")
        pairs = {(row["schedule"], row["lease_type"]) for row in kids}
        self.assertEqual(pairs, {("Y", "O"), ("Y", "G"), ("N", "O"), ("N", "G")})


class CliTests(unittest.TestCase):
    def test_load_texas_identity_only_flag(self):
        from wellnav.ingest.__main__ import main

        with patch("wellnav.ingest.__main__.load_texas") as load:
            load.return_value = {"status": "ok"}
            code = main(["load-texas", "--identity-only", "--counties", "003", "009"])
        self.assertEqual(code, 0)
        kwargs = load.call_args.kwargs
        self.assertTrue(kwargs["identity_only"])
        self.assertEqual(kwargs["counties"], ["003", "009"])


if __name__ == "__main__":
    unittest.main()
