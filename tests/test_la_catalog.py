"""Louisiana tables match Texas columns and the same search/catalog behavior."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wellnav.db import init_schema
from wellnav.ingest.la_operators import sync_louisiana_catalog
from wellnav.ingest.la_permits import is_la_permit_only, parse_sonris_date, permit_from_well
from wellnav.ingest.sonris_portal import load_sonris_export, sonris_export_row_to_well
from wellnav.repository import WellRepository
from wellnav.states import operators_table, permits_table, wells_table


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return [
        (row[1], row[2], row[3], row[5])
        for row in conn.execute(f"PRAGMA table_info({table})")
    ]


class LaSchemaMatchTest(unittest.TestCase):
    def test_la_tables_match_tx_columns(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        self.assertEqual(_columns(conn, wells_table("la")), _columns(conn, wells_table("tx")))
        self.assertEqual(_columns(conn, permits_table("la")), _columns(conn, permits_table("tx")))
        self.assertEqual(
            _columns(conn, operators_table("la")),
            _columns(conn, operators_table("tx")),
        )
        conn.close()


class LaPermitClassifyTest(unittest.TestCase):
    def test_parse_sonris_dates(self) -> None:
        self.assertEqual(parse_sonris_date("17-DEC-2018"), "2018-12-17")
        self.assertEqual(parse_sonris_date("01-AUG-2020"), "2020-08-01")
        self.assertEqual(parse_sonris_date("12/17/2018"), "2018-12-17")
        self.assertEqual(parse_sonris_date(""), "")

    def test_permit_expired_is_permit_only(self) -> None:
        self.assertTrue(
            is_la_permit_only({"symbol": "PERMIT EXPIRED", "source": "la_sonris_portal"})
        )
        self.assertTrue(
            is_la_permit_only({"symbol": "PERMIT CANCELLED", "well_type": "", "source": "la_sonris"})
        )
        self.assertFalse(
            is_la_permit_only({"symbol": "ACTIVE - PRODUCING  OIL/GAS", "source": "la_sonris_portal"})
        )
        self.assertFalse(
            is_la_permit_only({"symbol": "PLUGGED AND ABANDONED", "source": "la_sonris"})
        )
        self.assertFalse(
            is_la_permit_only({"symbol": "ACTIVE- INJECTION", "source": "la_sonris_portal"})
        )
        self.assertFalse(
            is_la_permit_only({"symbol": "PERMIT EXPIRED", "source": "la_fracfocus"})
        )
        self.assertFalse(
            is_la_permit_only({"symbol": "PERMIT EXPIRED", "_spud_at": "2019-01-21"})
        )

    def test_permit_from_well_uses_serial_and_dates(self) -> None:
        permit = permit_from_well(
            {
                "api": "1701736331",
                "api8": "01736331",
                "well_name": "C EDWARDS ETAL #035",
                "well_no": "035",
                "lease_name": "C EDWARDS ETAL",
                "lease_no": "252213",
                "operator": "A. M. A. CHILES",
                "operator_number": "0024",
                "county": "CADDO",
                "county_code": "017",
                "district": "SHREVEPORT",
                "symbol": "PERMIT EXPIRED",
                "well_type": "",
                "wellhead_lat": 32.86,
                "wellhead_lon": -93.97,
                "wellhead_crs": "EPSG:4326",
                "location_source": "la_serial:252213",
                "source": "la_sonris_portal",
                "_approved_at": "04-FEB-2020",
                "_expires_at": "01-AUG-2020",
                "first_seen_at": "t",
            },
            now="2026-09-07T00:00:00+00:00",
        )
        self.assertEqual(permit["permit_no"], "252213")
        self.assertEqual(permit["status"], "expired")
        self.assertEqual(permit["approved_at"], "2020-02-04")
        self.assertEqual(permit["expires_at"], "2020-08-01")
        self.assertEqual(permit["operator_number"], "0024")
        self.assertEqual(permit["lifetime_days"], 730)


class LaCatalogBehaviorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_sync_moves_permits_and_builds_operators_la(self) -> None:
        now = "t"
        self.conn.executemany(
            f"""
            INSERT INTO {wells_table("la")}(
                api, api8, well_name, well_no, lease_name, lease_no, county,
                operator, operator_number, well_type, symbol, source,
                first_seen_at, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    "1703127357",
                    "03127357",
                    "LA MINERALS 33-28HC",
                    "001-ALT",
                    "LA MINERALS",
                    "252001",
                    "DE SOTO",
                    "BPX OPERATING COMPANY",
                    "B6983",
                    "OIL/GAS",
                    "ACTIVE - PRODUCING  OIL/GAS",
                    "la_sonris_portal",
                    now,
                    now,
                    now,
                ),
                (
                    "1701736331",
                    "01736331",
                    "C EDWARDS ETAL #035",
                    "035",
                    "C EDWARDS ETAL",
                    "252213",
                    "CADDO",
                    "A. M. A. CHILES",
                    "0024",
                    "",
                    "PERMIT EXPIRED",
                    "la_sonris_portal",
                    now,
                    now,
                    now,
                ),
                (
                    "1703127002",
                    "03127002",
                    "BPX FRACFOCUS",
                    "2",
                    "",
                    "",
                    "DE SOTO",
                    "",
                    "B6983",
                    "GAS",
                    "COMPLETED",
                    "la_fracfocus",
                    now,
                    now,
                    now,
                ),
            ],
        )
        self.conn.commit()
        stats = sync_louisiana_catalog(self.conn)
        self.assertEqual(stats["permits_moved"], 1)
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {wells_table('la')}").fetchone()[0],
            2,
        )
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {permits_table('la')}").fetchone()[0],
            1,
        )
        permit = self.conn.execute(f"SELECT * FROM {permits_table('la')}").fetchone()
        self.assertEqual(permit["permit_no"], "252213")
        self.assertEqual(permit["status"], "expired")
        self.assertEqual(permit["operator_number"], "0024")

        op = self.conn.execute(
            f"SELECT * FROM {operators_table('la')} WHERE operator_number = 'B6983'"
        ).fetchone()
        self.assertIsNotNone(op)
        self.assertEqual(op["operator_name"], "BPX OPERATING COMPANY")
        self.assertEqual(op["wells"], 2)
        self.assertEqual(op["oil"], 1)
        self.assertEqual(op["gas"], 1)
        self.assertEqual(op["status"], "ok")

        unnamed = self.conn.execute(
            f"SELECT operator FROM {wells_table('la')} WHERE api = '1703127002'"
        ).fetchone()[0]
        self.assertEqual(unnamed, "BPX OPERATING COMPANY")

        repo = WellRepository(self.conn)
        found = repo.search_operators("BPX", state="la")
        self.assertEqual(found, [{"number": "B6983", "name": "BPX OPERATING COMPANY"}])
        tx_leak = repo.search_operators("BPX", state="tx")
        self.assertEqual(tx_leak, [])

        wells = repo.search(state="la", operator_numbers=["B6983"])
        apis = {well["api"] for well in wells["wells"]}
        self.assertEqual(apis, {"03127357", "03127002"})

        permits = repo.search(state="la", operator_numbers=["0024"])
        self.assertEqual(permits["total"], 1)
        self.assertEqual(permits["wells"][0]["record_kind"], "permit")
        self.assertEqual(permits["wells"][0]["well_name"], "C EDWARDS ETAL #035")

        by_api = repo.wells_for_apis(["1701736331", "1703127357"], state="la")
        self.assertEqual(by_api[0]["record_kind"], "permit")
        self.assertEqual(by_api[1]["record_kind"], "as_drilled")

    def test_load_sonris_writes_permits_la(self) -> None:
        csv_text = (
            "Well Serial Num,Operator Name,Operator ID,Well Name,Well Num,"
            "Well Status Code Description,API Num,Parish Name,Parish Code,"
            "Permit Date,Spud Date,Expiration Date,Latitude,Longitude\n"
            "252213,A. M. A. CHILES,0024,C EDWARDS ETAL,035,PERMIT EXPIRED,"
            "17017363310000,CADDO,017,04-FEB-2020,,01-AUG-2020,32.8639,-93.9719\n"
            "252001,BPX OPERATING COMPANY,B6983,LA MINERALS 33-28HC,001-ALT,"
            "ACTIVE - PRODUCING,17031273570000,DE SOTO,031,01-JUN-2024,02-JUN-2024,,"
            "31.892804,-93.501435\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "export.csv"
            path.write_text(csv_text, encoding="utf-8")
            stats = load_sonris_export(self.conn, path)
        self.assertEqual(stats["added"], 1)
        self.assertEqual(stats["permits"], 1)
        well = sonris_export_row_to_well(
            {
                "Well Serial Num": "252213",
                "Well Status Code Description": "PERMIT EXPIRED",
                "API Num": "17017363310000",
                "Permit Date": "04-FEB-2020",
                "Expiration Date": "01-AUG-2020",
            }
        )
        self.assertEqual(well["_approved_at"], "2020-02-04")
        self.assertEqual(well["_expires_at"], "2020-08-01")
        permit = self.conn.execute(f"SELECT * FROM {permits_table('la')}").fetchone()
        self.assertEqual(permit["status"], "expired")
        self.assertEqual(permit["approved_at"], "2020-02-04")
        self.assertEqual(permit["expires_at"], "2020-08-01")
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {wells_table('la')}").fetchone()[0],
            1,
        )
        op = self.conn.execute(f"SELECT COUNT(*) FROM {operators_table('la')}").fetchone()[0]
        self.assertGreaterEqual(op, 1)


if __name__ == "__main__":
    unittest.main()
