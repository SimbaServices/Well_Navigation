"""Stacked operator / name / API search filters."""

from __future__ import annotations

import sqlite3
import unittest

from wellnav.db import init_schema
from wellnav.repository import WellRepository
from wellnav.states import permits_table, wells_table


def _insert_well(
    conn: sqlite3.Connection,
    *,
    api8: str,
    well_name: str,
    lease_name: str,
    well_no: str,
    county: str,
    operator: str,
    operator_number: str,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {wells_table("tx")}(
            api, api8, well_name, well_no, lease_name, county, operator,
            operator_number, first_seen_at, last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 't', 't', 't')
        """,
        (f"42{api8}", api8, well_name, well_no, lease_name, county, operator, operator_number),
    )


def _insert_permit(
    conn: sqlite3.Connection,
    *,
    api8: str,
    well_name: str,
    operator: str,
    operator_number: str,
    status: str = "approved",
) -> None:
    conn.execute(
        f"""
        INSERT INTO {permits_table("tx")}(
            api, api8, permit_no, status, well_name, operator, operator_number,
            lifetime_days, first_seen_at, last_seen_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 730, 't', 't', 't')
        """,
        (f"42{api8}", api8, f"P-{api8}", status, well_name, operator, operator_number),
    )


def _insert_operator(conn: sqlite3.Connection, number: str, name: str) -> None:
    conn.execute(
        """
        INSERT INTO operators_tx(operator_number, operator_name, status)
        VALUES (?, ?, 'ready')
        """,
        (number, name),
    )


class SearchFiltersTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        _insert_well(
            self.conn,
            api8="00300290",
            well_name="ALPHA 1H",
            lease_name="ALPHA",
            well_no="1H",
            county="ANDREWS",
            operator="PIONEER",
            operator_number="667548",
        )
        _insert_well(
            self.conn,
            api8="00300291",
            well_name="BETA 2H",
            lease_name="BETA",
            well_no="2H",
            county="REEVES",
            operator="PIONEER",
            operator_number="667548",
        )
        _insert_well(
            self.conn,
            api8="32933061",
            well_name="GAMMA 3",
            lease_name="GAMMA",
            well_no="3",
            county="MIDLAND",
            operator="XTO",
            operator_number="945936",
        )
        _insert_well(
            self.conn,
            api8="38942287",
            well_name="DELTA 4H",
            lease_name="DELTA",
            well_no="4H",
            county="REEVES",
            operator="VTX",
            operator_number="101377",
        )
        _insert_permit(
            self.conn,
            api8="20111111",
            well_name="PERMIT ONLY",
            operator="VTX",
            operator_number="101377",
        )
        _insert_operator(self.conn, "667548", "PIONEER NATURAL RES. USA, INC")
        _insert_operator(self.conn, "945936", "XTO ENERGY INC")
        _insert_operator(self.conn, "101377", "VTX ENERGY OPERATING, LLC")
        _insert_operator(self.conn, "999001", "ZZZ CATALOG ONLY OPERATING")
        self.conn.commit()
        self.repo = WellRepository(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_multiple_operator_numbers_returns_only_those_operators(self) -> None:
        result = self.repo.search(operator_numbers=["667548", "101377"])
        numbers = {well["operator_number"] for well in result["wells"]}
        apis = [well["api"] for well in result["wells"]]
        self.assertEqual(numbers, {"667548", "101377"})
        self.assertIn("00300290", apis)
        self.assertIn("00300291", apis)
        self.assertIn("38942287", apis)
        self.assertNotIn("32933061", apis)
        self.assertEqual(result["total"], 4)

    def test_sort_by_operator_desc(self) -> None:
        result = self.repo.search(sort="operator", direction="desc")
        operators = [well["operator"] for well in result["wells"]]
        self.assertEqual(operators, sorted(operators, reverse=True))

    def test_name_filter_ands_with_operator_numbers(self) -> None:
        result = self.repo.search(operator_numbers=["667548"], name="BETA")
        apis = [well["api"] for well in result["wells"]]
        self.assertEqual(apis, ["00300291"])
        self.assertEqual(result["total"], 1)
        empty = self.repo.search(operator_numbers=["667548"], name="GAMMA")
        self.assertEqual(empty["wells"], [])
        self.assertEqual(empty["total"], 0)

    def test_api_filter_ands(self) -> None:
        result = self.repo.search(
            operator_numbers=["667548", "945936"],
            api="42-003-00290",
        )
        apis = [well["api"] for well in result["wells"]]
        self.assertEqual(apis, ["00300290"])
        none = self.repo.search(operator_numbers=["945936"], api="00300290")
        self.assertEqual(none["wells"], [])

    def test_search_operators_hits_operators_tx(self) -> None:
        rows = self.repo.search_operators("CATALOG ONLY")
        self.assertEqual(rows, [{"number": "999001", "name": "ZZZ CATALOG ONLY OPERATING"}])
        pioneer = self.repo.search_operators("NATURAL RES")
        self.assertEqual(
            pioneer,
            [{"number": "667548", "name": "PIONEER NATURAL RES. USA, INC"}],
        )

    def test_search_operators_collapses_case_and_id_variants(self) -> None:
        conn = self.conn
        conn.execute(
            f"""
            INSERT INTO {wells_table("la")}(
                api, api8, well_name, well_no, lease_name, county, operator,
                operator_number, first_seen_at, last_seen_at, updated_at
            ) VALUES
                ('1703127357', '03127357', 'LA MINERALS 33-28HC', '001-ALT', '', 'DE SOTO',
                 'BPX OPERATING COMPANY', 'B6983', 't', 't', 't'),
                ('1703127001', '03127001', 'BPX TITLE CASE', '1', '', 'DE SOTO',
                 'BPX Operating Company', 'B372', 't', 't', 't'),
                ('1703127002', '03127002', 'BPX FRACFOCUS', '2', '', 'DE SOTO',
                 'BPX Operating Company', '', 't', 't', 't')
            """
        )
        conn.commit()
        rows = self.repo.search_operators("bpx", state="la")
        self.assertEqual(
            rows,
            [{"number": "B6983", "name": "BPX OPERATING COMPANY"}],
        )
        tx_only = self.repo.search_operators("PIONEER", state="la")
        self.assertEqual(tx_only, [])

        by_name = self.repo.search(
            state="la",
            operator_names=["BPX OPERATING COMPANY"],
        )
        apis = {well["api"] for well in by_name["wells"]}
        self.assertEqual(apis, {"03127357", "03127001", "03127002"})
        self.assertEqual(by_name["total"], 3)

        by_one_id = self.repo.search(
            state="la",
            operator_numbers=["B6983"],
            operator_names=["BPX OPERATING COMPANY"],
        )
        self.assertEqual({well["api"] for well in by_one_id["wells"]}, apis)

    def test_wells_for_apis_preserves_order_and_skips_missing(self) -> None:
        wells = self.repo.wells_for_apis(
            ["20111111", "00000000", "42-003-00290", "not-an-api", "32933061"]
        )
        self.assertEqual([well["api"] for well in wells], ["20111111", "00300290", "32933061"])
        self.assertEqual(wells[0]["record_kind"], "permit")
        self.assertEqual(wells[0]["well_name"], "PERMIT ONLY")
        self.assertEqual(wells[1]["record_kind"], "as_drilled")
        self.assertEqual(wells[2]["record_kind"], "as_drilled")


if __name__ == "__main__":
    unittest.main()
