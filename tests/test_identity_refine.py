"""Refine and over-limit tests for county EWA identity."""

from __future__ import annotations

import sqlite3
import unittest

from wellnav.db import init_schema
from wellnav.ingest.identity import IDENTITY_FIELDS, initial_spec, refine
from wellnav.ingest.persist import update_identity
from wellnav.rrc import annotate_wellbore_page
from wellnav.states import wells_table


class RefineTests(unittest.TestCase):
    def test_oversized_parent_splits_to_yn_times_og(self):
        children = refine(initial_spec())
        pairs = {(c["schedule"], c["lease_type"]) for c in children}
        self.assertEqual(pairs, {("Y", "O"), ("Y", "G"), ("N", "O"), ("N", "G")})
        self.assertTrue(all(not c.get("district") and not c.get("well_type") for c in children))

    def test_on_schedule_oil_splits_by_well_type_not_districts(self):
        children = refine({"schedule": "Y", "lease_type": "O", "well_type": "", "district": ""})
        self.assertGreater(len(children), 4)
        self.assertTrue(all(c["well_type"] for c in children))
        self.assertTrue(all(not c.get("district") for c in children))

    def test_off_schedule_oil_splits_by_district_first(self):
        children = refine({"schedule": "N", "lease_type": "O", "well_type": "", "district": ""})
        self.assertEqual(
            [c["district"] for c in children],
            ["01", "02", "03", "04", "05", "06", "6E", "7B", "7C", "08", "8A", "09", "10"],
        )


class AnnotateTests(unittest.TestCase):
    def test_ewa_123_is_over_limit_not_success(self):
        html = (
            "<html><title>Wellbore Query</title><span id='messageArea'>"
            "(Ewa_123) 15898 records found which exceeds the maximum records allowed. "
            "Please refine your search.</span></html>"
        )
        page = annotate_wellbore_page(html, page_size=100, offset=0)
        self.assertTrue(page["over_limit"])
        self.assertEqual(page["total"], 15898)
        self.assertEqual(page["wells"], [])
        self.assertFalse(page["no_results"])

    def test_application_error_is_over_limit(self):
        html = "<html><title>Application Error</title>Application Error</html>"
        page = annotate_wellbore_page(html, page_size=100, offset=0)
        self.assertTrue(page["over_limit"])
        self.assertEqual(page["wells"], [])

    def test_ewa_117_is_empty_not_refine(self):
        html = "<html><title>Wellbore Query</title>(Ewa_117) No results found.</html>"
        page = annotate_wellbore_page(html, page_size=100, offset=0)
        self.assertFalse(page["over_limit"])
        self.assertTrue(page["no_results"])


class PersistTests(unittest.TestCase):
    def test_coalesce_does_not_wipe_existing(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        table = wells_table("tx")
        conn.execute(
            f"""
            INSERT INTO {table}(
                api, api8, well_name, lease_name, lease_no, district, operator,
                operator_number, field, first_seen_at, last_seen_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 't', 't', 't')
            """,
            ("4200100001", "00100001", "OLD NAME", "KEEP LEASE", "9", "06", "KEEP OP", "2", "KEEP FIELD"),
        )
        n = update_identity(
            conn,
            "tx",
            [{
                "api": "00100001",
                "well_name": "NEW NAME",
                "well_no": "7",
                "lease_name": "",
                "lease_no": "",
                "district": "06",
                "operator": "",
                "operator_number": "",
                "field": "NEW FIELD",
            }],
        )
        self.assertEqual(n, 1)
        row = conn.execute(f"SELECT * FROM {table} WHERE api8='00100001'").fetchone()
        self.assertEqual(row["well_name"], "NEW NAME")
        self.assertEqual(row["well_no"], "7")
        self.assertEqual(row["lease_name"], "KEEP LEASE")
        self.assertEqual(row["operator"], "KEEP OP")
        self.assertEqual(row["field"], "NEW FIELD")
        self.assertTrue("lease_name" in IDENTITY_FIELDS)


class CliTests(unittest.TestCase):
    def test_identity_only_flag(self):
        from unittest import mock

        from wellnav.ingest.__main__ import main

        with mock.patch("wellnav.ingest.__main__.load_texas", return_value={"status": "ok"}) as load:
            rc = main(["load-texas", "--identity-only", "--counties", "003", "009", "029"])
        self.assertEqual(rc, 0)
        kwargs = load.call_args.kwargs
        self.assertTrue(kwargs["identity_only"])
        self.assertEqual(kwargs["counties"], ["003", "009", "029"])


if __name__ == "__main__":
    unittest.main()
