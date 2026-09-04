"""Unit tests for RRC EWA wellbore HTML parsing."""

from __future__ import annotations

import unittest
from pathlib import Path

from wellnav.parsers import parse_wellbore_results

FIXTURE = Path(__file__).parent / "fixtures" / "wellbore_datagrid.html"
SAMPLE = Path(__file__).resolve().parents[1] / "rrc_wellbore_query.txt"


class ParseWellboreResultsTest(unittest.TestCase):
    def test_fixture_recovers_identity_from_href_and_td_text(self) -> None:
        html = FIXTURE.read_text(encoding="utf-8")
        parsed = parse_wellbore_results(html)

        self.assertTrue(parsed["pager"])
        self.assertEqual(parsed["total"], 100)
        self.assertEqual(parsed["start"], 1)
        self.assertEqual(parsed["end"], 4)
        self.assertEqual(len(parsed["wells"]), 4)

        by_api = {well["api"]: well for well in parsed["wells"]}

        linked = by_api["00300290"]
        self.assertEqual(linked["lease_name"], "UNIVERSITY 11 SEC 1")
        self.assertEqual(linked["lease_no"], "39306")
        self.assertEqual(linked["district"], "08")
        self.assertEqual(linked["operator"], "OXY USA EOR, LLC")
        self.assertEqual(linked["operator_number"], "103869")
        self.assertEqual(linked["well_no"], "2A")

        fallback = by_api["00112345"]
        self.assertEqual(fallback["lease_name"], "ANDERSON FALLBACK UNIT")
        self.assertEqual(fallback["lease_no"], "18801")
        self.assertEqual(fallback["district"], "05")
        self.assertEqual(fallback["operator"], "OXY USA INC.")
        self.assertEqual(fallback["operator_number"], "630591")
        self.assertEqual(fallback["well_no"], "14H")

        api_only = by_api["00100001"]
        self.assertEqual(api_only["lease_name"], "API ONLY LEASE")
        self.assertEqual(api_only["lease_no"], "77")
        self.assertEqual(api_only["district"], "7B")
        self.assertEqual(api_only["operator"], "NO LINK OPERATOR LLC")

        long_name = by_api["00100002"]
        self.assertIn("EIGHTY CHARACTERS", long_name["lease_name"])
        self.assertEqual(long_name["lease_no"], "55555")
        self.assertEqual(long_name["district"], "06")
        self.assertEqual(long_name["operator"], "OXY USA WTP LP")

    def test_saved_operator_query_sample_still_parses(self) -> None:
        raw = SAMPLE.read_text(encoding="utf-8", errors="replace")
        html = raw[raw.find("<!DOCTYPE") :]
        parsed = parse_wellbore_results(html)
        self.assertTrue(parsed["pager"])
        self.assertEqual(parsed["total"], 4594)
        self.assertEqual(len(parsed["wells"]), 10)
        first = parsed["wells"][0]
        self.assertEqual(first["api"], "00300290")
        self.assertEqual(first["lease_name"], "UNIVERSITY 11 SEC 1")
        self.assertEqual(first["operator"], "OXY USA EOR, LLC")
        self.assertEqual(first["district"], "08")
        self.assertEqual(first["lease_no"], "39306")
        incomplete = [
            well
            for well in parsed["wells"]
            if not (well["lease_name"] and well["operator"] and well["district"] and well["lease_no"])
        ]
        self.assertEqual(incomplete, [])


if __name__ == "__main__":
    unittest.main()
