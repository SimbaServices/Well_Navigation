"""Offline tests for the Texas EWA permitted-location pull."""

from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import patch

from wellnav.coords import to_wgs84
from wellnav.gis import LAYER_SURFACE, LAYER_WELL_LOCATIONS, _serialize_point
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.ingest.tx_permit_pull import fetch_texas_permits
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS


def _ewa_row(
    api8: str,
    approved_at: str,
    *,
    status: str = "APPROVED",
    permit_no: str | None = None,
) -> dict:
    return {
        "api": api8,
        "permit_no": permit_no or api8[-6:],
        "lease_name": "BABOON E14",
        "well_no": "14H",
        "operator": "VTX ENERGY OPERATING, LLC",
        "operator_number": "101377",
        "county": "REEVES",
        "county_code": api8[:3],
        "district": "08",
        "profile": "Horizontal",
        "status": status,
        "submitted_at": "09/01/2026",
        "approved_at": approved_at,
    }


def _gis_ok(features: list[dict]) -> dict:
    return {"features": features, "complete": True, "blocked": False, "error": None}


def _assert_bounded(test: unittest.TestCase, calls: list[tuple[str, str]], start: date, end: date) -> None:
    test.assertTrue(calls)
    for approved_from, approved_to in calls:
        test.assertRegex(approved_from, r"^\d{2}/\d{2}/\d{4}$")
        test.assertRegex(approved_to, r"^\d{2}/\d{2}/\d{4}$")
        test.assertNotEqual(approved_from, "")
        test.assertNotEqual(approved_to, "")
        got_start = datetime.strptime(approved_from, "%m/%d/%Y").date()
        got_end = datetime.strptime(approved_to, "%m/%d/%Y").date()
        test.assertGreaterEqual(got_start, start)
        test.assertLessEqual(got_end, end)
        test.assertLessEqual(got_start, got_end)


class FetchTexasPermitsTest(unittest.TestCase):
    def test_requested_window_drops_outside_row_and_copies_surface_point(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 30)
        calls: list[tuple[str, str]] = []
        gis_calls: list[tuple[int, str]] = []

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            calls.append((approved_from, approved_to))
            permits = [
                _ewa_row("38942287", "09/15/2026", permit_no="917969"),
                _ewa_row("00100002", "09/16/2026", status="CANCELLED", permit_no="100002"),
                _ewa_row("00100003", "08/01/2026", permit_no="100003"),
                _ewa_row("12345678", "", permit_no="345678"),
            ]
            return {"permits": permits, "total": len(permits), "page_size": len(permits)}

        def fake_gis(layer_id: int, where: str, **kwargs: object) -> dict:
            gis_calls.append((layer_id, where))
            if layer_id == LAYER_SURFACE:
                return _gis_ok(
                    [
                        {
                            "attributes": {
                                "API": "38942287",
                                "GIS_LAT83": 30.0,
                                "GIS_LONG83": -100.0,
                            },
                            "geometry": {"x": -103.2, "y": 31.5},
                        }
                    ]
                )
            if layer_id == LAYER_WELL_LOCATIONS:
                return _gis_ok(
                    [
                        {
                            "attributes": {"API": "38942287"},
                            "geometry": {"x": -99.0, "y": 30.0},
                        }
                    ]
                )
            raise AssertionError(f"unexpected layer {layer_id}")

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer", side_effect=fake_gis),
        ):
            records = fetch_texas_permits(start, end, delay=0)

        self.assertEqual(calls, [("09/01/2026", "09/30/2026")])
        _assert_bounded(self, calls, start, end)
        self.assertEqual([row["api"] for row in records], ["4238942287", "4200100002"])
        surface = records[0]
        self.assertEqual(list(surface.keys()), PERMIT_FIELDS)
        self.assertEqual(surface["api8"], "38942287")
        self.assertEqual(surface["permit_no"], "917969")
        self.assertEqual(surface["status"], "approved")
        self.assertEqual(surface["approved_at"], "2026-09-15")
        self.assertEqual(surface["source"], "rrc_ewa")
        self.assertEqual(surface["lifetime_days"], DEFAULT_PERMIT_LIFETIME_DAYS)
        self.assertEqual(surface["as_drilled_ready"], 0)
        self.assertIsNone(surface["migrated_at"])
        self.assertEqual(surface["wellhead_lat"], 31.5)
        self.assertEqual(surface["wellhead_lon"], -103.2)
        self.assertEqual(surface["wellhead_crs"], "WGS 84 geographic")
        cancelled = records[1]
        self.assertEqual(cancelled["status"], "cancelled")
        self.assertEqual(cancelled["approved_at"], "2026-09-16")
        self.assertIsNone(cancelled["wellhead_lat"])
        self.assertIsNone(cancelled["wellhead_lon"])
        self.assertIsNone(cancelled["wellhead_crs"])
        self.assertEqual([call[0] for call in gis_calls], [LAYER_SURFACE, LAYER_WELL_LOCATIONS])
        self.assertIn("38942287", gis_calls[0][1])
        self.assertIn("00100002", gis_calls[0][1])
        self.assertNotIn("00100003", gis_calls[0][1])
        self.assertNotIn("12345678", gis_calls[0][1])

    def test_month_chunks_stay_inside_the_requested_window(self) -> None:
        start = date(2026, 9, 17)
        end = date(2026, 10, 1)
        calls: list[tuple[str, str]] = []

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            calls.append((approved_from, approved_to))
            return {"permits": [], "total": 0, "page_size": 0}

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer") as gis,
        ):
            records = fetch_texas_permits(start, end, delay=0)

        self.assertEqual(records, [])
        self.assertEqual(
            calls,
            [("09/17/2026", "09/30/2026"), ("10/01/2026", "10/01/2026")],
        )
        _assert_bounded(self, calls, start, end)
        gis.assert_not_called()

    def test_truncated_month_is_split_and_not_skipped(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 30)
        calls: list[tuple[str, str]] = []

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            calls.append((approved_from, approved_to))
            if (approved_from, approved_to) == ("09/01/2026", "09/30/2026"):
                return {
                    "permits": [
                        _ewa_row("38942287", "09/10/2026"),
                        _ewa_row("00100002", "09/20/2026", status="EXPIRED"),
                    ],
                    "total": 10,
                    "page_size": 10,
                }
            if (approved_from, approved_to) == ("09/01/2026", "09/15/2026"):
                row = _ewa_row("38942287", "09/10/2026")
                return {"permits": [row], "total": 1, "page_size": 1}
            if (approved_from, approved_to) == ("09/16/2026", "09/30/2026"):
                row = _ewa_row("00100002", "09/20/2026", status="EXPIRED")
                return {"permits": [row], "total": 1, "page_size": 1}
            raise AssertionError(f"unexpected window {approved_from}..{approved_to}")

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer", return_value=_gis_ok([])),
        ):
            records = fetch_texas_permits(start, end, delay=0)

        self.assertEqual(
            calls,
            [
                ("09/01/2026", "09/30/2026"),
                ("09/01/2026", "09/15/2026"),
                ("09/16/2026", "09/30/2026"),
            ],
        )
        _assert_bounded(self, calls, start, end)
        self.assertEqual([row["api"] for row in records], ["4238942287", "4200100002"])
        self.assertEqual(records[1]["status"], "expired")
        self.assertEqual(records[1]["approved_at"], "2026-09-20")
        self.assertIsNone(records[1]["wellhead_lat"])

    def test_duplicate_banner_count_is_not_a_truncated_page(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 30)
        calls: list[tuple[str, str]] = []

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            calls.append((approved_from, approved_to))
            row = _ewa_row("38942287", "09/15/2026", permit_no="917969")
            return {"permits": [row], "total": 1, "page_size": 2}

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer", return_value=_gis_ok([])),
        ):
            records = fetch_texas_permits(start, end, delay=0)

        self.assertEqual(calls, [("09/01/2026", "09/30/2026")])
        self.assertEqual([row["api"] for row in records], ["4238942287"])

    def test_failed_chunk_raises_instead_of_returning_partial(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            calls.append((approved_from, approved_to))
            raise RuntimeError("ewa down")

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer") as gis,
        ):
            with self.assertRaises(RuntimeError):
                fetch_texas_permits(date(2026, 9, 1), date(2026, 9, 3), delay=0)

        _assert_bounded(self, calls, date(2026, 9, 1), date(2026, 9, 3))
        self.assertIn(("09/01/2026", "09/01/2026"), calls)
        gis.assert_not_called()

    def test_nad83_attribute_used_when_geometry_missing_and_layer1_is_fallback(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 30)

        def fake_search(*, approved_from: str, approved_to: str) -> dict:
            self.assertEqual((approved_from, approved_to), ("09/01/2026", "09/30/2026"))
            permits = [
                _ewa_row("38942287", "09/15/2026", permit_no="917969"),
                _ewa_row("00100002", "09/16/2026", permit_no="100002"),
            ]
            return {"permits": permits, "total": len(permits), "page_size": len(permits)}

        def fake_gis(layer_id: int, where: str, **kwargs: object) -> dict:
            if layer_id == LAYER_SURFACE:
                return _gis_ok(
                    [
                        {
                            "attributes": {
                                "API": "38942287",
                                "GIS_LAT83": 31.8,
                                "GIS_LONG83": -102.4,
                            },
                            "geometry": None,
                        }
                    ]
                )
            if layer_id == LAYER_WELL_LOCATIONS:
                return _gis_ok(
                    [
                        {
                            "attributes": {"API": "00100002"},
                            "geometry": {"x": -101.25, "y": 32.1},
                        }
                    ]
                )
            raise AssertionError(layer_id)

        with (
            patch("wellnav.ingest.tx_permit_pull.CLIENT.search_drilling_permits", side_effect=fake_search),
            patch("wellnav.ingest.tx_permit_pull.fetch_layer", side_effect=fake_gis),
        ):
            records = fetch_texas_permits(start, end, delay=0)

        expected = _serialize_point(to_wgs84(-102.4, 31.8, "nad83"))
        by_api = {row["api8"]: row for row in records}
        self.assertEqual(by_api["38942287"]["wellhead_lat"], expected["lat"])
        self.assertEqual(by_api["38942287"]["wellhead_lon"], expected["lon"])
        self.assertEqual(by_api["38942287"]["wellhead_crs"], expected["source_label"])
        self.assertEqual(by_api["00100002"]["wellhead_lat"], 32.1)
        self.assertEqual(by_api["00100002"]["wellhead_lon"], -101.25)
        self.assertEqual(by_api["00100002"]["wellhead_crs"], "WGS 84 geographic")


if __name__ == "__main__":
    unittest.main()
