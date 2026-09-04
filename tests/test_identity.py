"""Refine / paging tests for county-wide EWA identity."""

from __future__ import annotations

import sqlite3
import unittest

from wellnav.db import init_schema
from wellnav.http_client import BlockedRequest
from wellnav.ingest.identity import (
    IDENTITY_FIELDS,
    PAGE_SIZE,
    fetch_county_identity,
    initial_spec,
    merge_identity,
    refine,
)
from wellnav.ingest.persist import update_identity
from wellnav.rrc import annotate_wellbore_page
from wellnav.states import wells_table


class FakeClient:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []

    def search_wellbores(self, **kwargs):
        self.calls.append(kwargs)
        return self.handler(kwargs)


def _page(wells, total, start, end, *, over_limit=False, no_results=False):
    return {
        "wells": wells,
        "total": total,
        "start": start,
        "end": end,
        "over_limit": over_limit,
        "no_results": no_results,
    }


def _well(api, lease="LEASE"):
    return {
        "api": api,
        "well_name": f"{lease} #1",
        "well_no": "1",
        "lease_name": lease,
        "lease_no": "100",
        "district": "08",
        "operator": "OP",
        "operator_number": "1",
        "field": "FIELD",
    }


class RefineTests(unittest.TestCase):
    def test_oversized_parent_splits_to_yn_times_og(self):
        children = refine(initial_spec())
        pairs = {(c["schedule"], c["lease_type"]) for c in children}
        self.assertEqual(pairs, {("Y", "O"), ("Y", "G"), ("N", "O"), ("N", "G")})
        self.assertTrue(all(c["district"] == "" and c["well_type"] == "" for c in children))
        self.assertTrue(all(c["lease_type"] in {"O", "G"} for c in children))

    def test_on_schedule_oil_splits_by_well_type_not_districts(self):
        children = refine({"schedule": "Y", "lease_type": "O", "well_type": "", "district": ""})
        self.assertGreater(len(children), 4)
        self.assertTrue(all(c["well_type"] for c in children))
        self.assertTrue(all(c["district"] == "" for c in children))

    def test_off_schedule_oil_splits_by_district_first(self):
        children = refine({"schedule": "N", "lease_type": "O", "well_type": "", "district": ""})
        codes = [c["district"] for c in children]
        self.assertEqual(
            codes,
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
        html = (
            "<html><title>Wellbore Query</title>"
            "(Ewa_117) No results found.</html>"
        )
        page = annotate_wellbore_page(html, page_size=100, offset=0)
        self.assertFalse(page["over_limit"])
        self.assertTrue(page["no_results"])


class FetchTests(unittest.TestCase):
    def test_over_limit_refines_immediately_and_keeps_first_page_wells(self):
        def handler(kw):
            sch, lt, wt, dist = (
                kw.get("schedule"),
                kw.get("lease_type"),
                kw.get("well_type"),
                kw.get("district"),
            )
            if sch == "Both" and not lt:
                return _page([_well("00300001", "KEPT")], 12000, 1, 1, over_limit=True)
            if sch == "Y" and lt == "O" and not wt and not dist:
                return _page([], 11000, 0, 0, over_limit=True)
            if sch == "Y" and lt == "O" and wt == "PR":
                return _page([_well("00300002")], 1, 1, 1)
            if sch == "Y" and lt == "G":
                return _page([_well("00300003")], 1, 1, 1)
            if sch == "N" and lt == "G":
                return _page([_well("00300004")], 1, 1, 1)
            if sch == "N" and lt == "O" and not dist:
                return _page([], 11000, 0, 0, over_limit=True)
            if sch == "N" and lt == "O" and dist == "08":
                return _page([_well("00300005")], 1, 1, 1)
            return _page([], 0, 0, 0, no_results=True)

        client = FakeClient(handler)
        result = fetch_county_identity("003", client=client, delay=0)
        apis = {w["api"] for w in result["wells"]}
        self.assertIn("00300001", apis)
        self.assertIn("00300002", apis)
        self.assertIn("00300003", apis)
        self.assertIn("00300004", apis)
        self.assertIn("00300005", apis)
        first = client.calls[0]
        self.assertEqual(first["schedule"], "Both")
        self.assertEqual(first["lease_type"], "")
        four_way = {
            (c["schedule"], c["lease_type"])
            for c in client.calls[1:]
            if not c.get("well_type") and not c.get("district")
        }
        self.assertEqual(four_way, {("Y", "O"), ("Y", "G"), ("N", "O"), ("N", "G")})
        both_calls = [
            c for c in client.calls
            if c["schedule"] == "Both" and not c.get("lease_type")
        ]
        self.assertEqual(len(both_calls), 1)

    def test_pages_all_results_using_end_offset(self):
        def handler(kw):
            offset = int(kw.get("offset") or 0)
            if offset == 0:
                wells = [_well(f"001{i:05d}") for i in range(100)]
                return _page(wells, 250, 1, 100)
            if offset == 100:
                wells = [_well(f"001{i:05d}") for i in range(100, 200)]
                return _page(wells, 250, 101, 200)
            if offset == 200:
                wells = [_well(f"001{i:05d}") for i in range(200, 250)]
                return _page(wells, 250, 201, 250)
            self.fail(f"unexpected offset {offset}")

        client = FakeClient(handler)
        result = fetch_county_identity("001", client=client, delay=0)
        self.assertEqual(result["count"], 250)
        self.assertEqual([c["offset"] for c in client.calls], [0, 100, 200])
        self.assertTrue(all(c["page_size"] == PAGE_SIZE for c in client.calls))

    def test_blocked_request_keeps_scratch_and_first_page(self):
        calls = {"n": 0}

        def handler(kw):
            calls["n"] += 1
            if calls["n"] == 1:
                return _page([_well("00100001")], 150, 1, 100)
            raise BlockedRequest("ewa blocked", retry_after=2)

        client = FakeClient(handler)
        scratch = {}
        with self.assertRaises(BlockedRequest):
            fetch_county_identity("001", client=client, delay=0, scratch=scratch)
        self.assertFalse(scratch.get("identity_complete"))
        self.assertEqual(len(scratch.get("identity_wells") or []), 1)
        self.assertTrue(scratch.get("identity_queue"))


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

    def test_merge_identity_maps_api8(self):
        rows = [{"api8": "00100001", "lease_name": "", "operator": ""}]
        merge_identity(rows, [_well("00100001", "ALPHA")])
        self.assertEqual(rows[0]["lease_name"], "ALPHA")
        self.assertTrue(all(name in IDENTITY_FIELDS for name in (
            "lease_name", "lease_no", "district", "operator", "operator_number", "field", "well_name"
        )))


class CliTests(unittest.TestCase):
    def test_identity_only_flag(self):
        from wellnav.ingest.__main__ import main
        from unittest import mock

        with mock.patch("wellnav.ingest.__main__.load_texas", return_value={"status": "ok"}) as load:
            rc = main(["load-texas", "--identity-only", "--counties", "003", "009", "029"])
        self.assertEqual(rc, 0)
        kwargs = load.call_args.kwargs
        self.assertTrue(kwargs["identity_only"])
        self.assertEqual(kwargs["counties"], ["003", "009", "029"])


if __name__ == "__main__":
    unittest.main()
