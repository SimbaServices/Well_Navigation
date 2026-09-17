"""Unit tests for RRC EWA drilling-permit (W-1) parsing and weekly window."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from wellnav.db import init_schema
from wellnav.ingest.permits import approved_interval, ewa_row_to_permit
from wellnav.ingest.persist import upsert_ewa_permits
from wellnav.parsers import parse_drilling_permit_results, rewrite_permit_page_url
from wellnav.rrc import drilling_permit_search_data
from wellnav.states import permits_table

SAMPLE = Path(__file__).resolve().parents[1] / "permits_request.txt"


def _first_response_html() -> str:
    raw = SAMPLE.read_text(encoding="utf-8", errors="replace")
    start = raw.find("<!DOCTYPE")
    end = raw.find("@@@@@@")
    return raw[start:end]


class ParseDrillingPermitResultsTest(unittest.TestCase):
    def test_captured_first_page_count_and_rows(self) -> None:
        parsed = parse_drilling_permit_results(_first_response_html())
        self.assertTrue(parsed["pager"])
        self.assertEqual(parsed["total"], 1126)
        self.assertEqual(parsed["start"], 1)
        self.assertEqual(parsed["end"], 10)
        self.assertEqual(len(parsed["permits"]), 10)
        self.assertIn("pager.pageSize=", parsed["pager_href"])
        self.assertIn("rrcActionMan=", parsed["pager_href"])

        first = parsed["permits"][0]
        self.assertEqual(first["api"], "38942287")
        self.assertEqual(first["permit_no"], "917969")
        self.assertEqual(first["lease_name"], "BABOON E14")
        self.assertEqual(first["well_no"], "14H")
        self.assertEqual(first["operator"], "VTX ENERGY OPERATING, LLC")
        self.assertEqual(first["operator_number"], "101377")
        self.assertEqual(first["county"], "REEVES")
        self.assertEqual(first["district"], "08")
        self.assertEqual(first["profile"], "Horizontal")
        self.assertEqual(first["filing_purpose"], "New Drill")
        self.assertEqual(first["status"], "APPROVED")
        self.assertEqual(first["submitted_at"], "08/18/2026")
        self.assertEqual(first["approved_at"], "08/21/2026")
        self.assertEqual(first["universal_doc_no"], "496966562")

        reenter = next(row for row in parsed["permits"] if row["api"] == "32933061")
        self.assertEqual(reenter["operator"], "OVERFLOW ENERGY PERMIAN, LLC")
        self.assertEqual(reenter["operator_number"], "628566")
        self.assertEqual(reenter["filing_purpose"], "Reenter")
        self.assertEqual(reenter["profile"], "Vertical")

    def test_full_page_url_uses_total_as_page_size_and_offset_zero(self) -> None:
        parsed = parse_drilling_permit_results(_first_response_html())
        url = rewrite_permit_page_url(parsed["pager_href"], page_size=parsed["total"], offset=0)
        parts = urlparse(url)
        qs = parse_qs(parts.query)
        self.assertEqual(parts.path, "/EWA/drillingPermitsQueryAction.do")
        self.assertEqual(qs["pager.pageSize"], ["1126"])
        self.assertEqual(qs["pager.offset"], ["0"])
        self.assertEqual(qs["methodToCall"], ["search"])
        self.assertTrue(qs["rrcActionMan"][0])
        param = qs["searchArgs.paramValue"][0]
        self.assertIn("1024=08/01/2026", param)
        self.assertIn("1025=09/04/2026", param)


class PermitSearchPayloadTest(unittest.TestCase):
    def test_approved_dates_are_the_only_filled_criteria(self) -> None:
        data = drilling_permit_search_data("08/01/2026", "09/04/2026")
        self.assertEqual(data["methodToCall"], "search")
        self.assertEqual(data["searchArgs.approvedDtFromHndlr.inputValue"], "08/01/2026")
        self.assertEqual(data["searchArgs.approvedDtToHndlr.inputValue"], "09/04/2026")
        self.assertEqual(data["searchArgs.submittedDtFromHndlr.inputValue"], "")
        self.assertEqual(data["searchArgs.operatorNameWildcardHndlr.inputValue"], "beginsWith")


class ApprovedIntervalTest(unittest.TestCase):
    def test_uses_last_request_date_through_today(self) -> None:
        now = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
        start, end = approved_interval("2026-08-28T17:00:00+00:00", now=now)
        self.assertEqual(start, "08/28/2026")
        self.assertEqual(end, "09/04/2026")

    def test_defaults_to_one_week_when_no_prior_request(self) -> None:
        now = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)
        start, end = approved_interval(None, now=now)
        self.assertEqual(start, "08/28/2026")
        self.assertEqual(end, "09/04/2026")


class EwaRowToPermitTest(unittest.TestCase):
    def test_maps_status_number_and_dates(self) -> None:
        record = ewa_row_to_permit(
            {
                "api": "38942287",
                "permit_no": "917969",
                "lease_name": "BABOON E14",
                "well_no": "14H",
                "operator": "VTX ENERGY OPERATING, LLC",
                "operator_number": "101377",
                "county": "REEVES",
                "county_code": "389",
                "district": "08",
                "profile": "Horizontal",
                "status": "APPROVED",
                "submitted_at": "08/18/2026",
                "approved_at": "08/21/2026",
            },
            now="2026-09-04T03:00:00+00:00",
            lifetime_days=730,
        )
        self.assertEqual(record["api"], "4238942287")
        self.assertEqual(record["permit_no"], "917969")
        self.assertEqual(record["status"], "approved")
        self.assertEqual(record["approved_at"], "2026-08-21")
        self.assertEqual(record["submitted_at"], "2026-08-18")
        self.assertEqual(record["expires_at"], "2028-08-20")
        self.assertEqual(record["source"], "rrc_ewa")
        self.assertEqual(record["profile"], "horizontal")


class UpsertEwaPermitsTest(unittest.TestCase):
    def test_promotes_gis_placeholder_to_status_number(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        table = permits_table("tx")
        conn.execute(
            f"""
            INSERT INTO {table}(
                api, api8, permit_no, status, well_name, county_code, lifetime_days,
                wellhead_lat, wellhead_lon, wellhead_crs, source,
                first_seen_at, last_seen_at, updated_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "4238942287", "38942287", "GIS-38942287", "approved", "old",
                "389", 730, 31.5, -103.2, "nad83", "rrc_gis",
                "2026-01-01", "2026-01-01", "2026-01-01",
            ),
        )
        record = ewa_row_to_permit(
            {
                "api": "38942287",
                "permit_no": "917969",
                "lease_name": "BABOON E14",
                "well_no": "14H",
                "operator": "VTX ENERGY OPERATING, LLC",
                "operator_number": "101377",
                "county": "REEVES",
                "county_code": "389",
                "district": "08",
                "profile": "Horizontal",
                "status": "APPROVED",
                "submitted_at": "08/18/2026",
                "approved_at": "08/21/2026",
            },
            now="2026-09-04T03:00:00+00:00",
            lifetime_days=730,
        )
        upsert_ewa_permits(conn, "tx", [record])
        rows = conn.execute(f"SELECT * FROM {table} WHERE api8='38942287'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["permit_no"], "917969")
        self.assertEqual(rows[0]["operator"], "VTX ENERGY OPERATING, LLC")
        self.assertEqual(rows[0]["wellhead_lat"], 31.5)
        self.assertEqual(rows[0]["first_seen_at"], "2026-01-01")
        conn.close()


if __name__ == "__main__":
    unittest.main()
