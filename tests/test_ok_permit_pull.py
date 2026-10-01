"""Oklahoma OCC permit pull sends a server-side date window and drops outsiders."""

from __future__ import annotations

import unittest
from datetime import date
from unittest.mock import patch

from wellnav.ingest.ok_permit_pull import fetch_oklahoma_permits
from wellnav.ingest.ok_wells import OK_ITD
from wellnav.ingest.persist import PERMIT_FIELDS


def _feature(
    *,
    api: str = "3500100001",
    approval: str | None = "2026-09-20",
    cancel: str | None = None,
    expire: str | None = "2028-09-20",
    lat: float | None = 35.5,
    lon: float | None = -97.5,
    status: str = "APPROVED",
    well_status: str = "ND",
) -> dict:
    return {
        "attributes": {
            "api_number": api,
            "surf_lat_y": lat,
            "surf_long_x": lon,
            "approval_date": approval,
            "cancel_date": cancel,
            "expire_date": expire,
            "permit_status": status,
            "well_status": well_status,
            "well_name": "TEST WELL",
            "well_number": "1H",
            "county": "CANADIAN",
            "entity_name": "TEST OPERATOR",
        }
    }


class OklahomaPermitPullTest(unittest.TestCase):
    def test_where_clause_and_window(self) -> None:
        start = date(2026, 9, 17)
        end = date(2026, 10, 1)
        features = [
            _feature(api="3500100001", approval="2026-09-20"),
            _feature(api="3500100002", approval="2026-10-01"),
            _feature(api="3500100003", approval="2026-10-02"),
            _feature(api="3500100004", approval="2026-09-16"),
            _feature(api="3500100005", approval="2020-01-01", cancel="1900-01-01", expire="2022-01-01"),
            _feature(api="3500100006", approval="2024-01-15", cancel="2026-09-20", expire="2024-06-01"),
            _feature(api="3500100007", approval="2024-02-01", cancel="1900-01-01", expire="2026-09-18"),
            _feature(api="3500100008", approval=None, cancel=None, expire="2026-09-20"),
            _feature(api="3500100009", approval="2026-09-20", lat=32.0, lon=-97.5),
            _feature(api="3500100010", approval="2026-09-20", status="WRA"),
            _feature(api="3500100011", approval="2026-09-20", status="WRA", well_status="AC"),
        ]
        captured: dict = {}

        def fake_iter(url, *, where="1=1", delay=0.12, **kwargs):
            captured["url"] = url
            captured["where"] = where
            captured["delay"] = delay
            captured["kwargs"] = kwargs
            return iter(features)

        with patch("wellnav.ingest.ok_permit_pull.iter_features", fake_iter):
            rows = fetch_oklahoma_permits(start, end)

        expected_where = (
            "(approval_date >= DATE '2026-09-17' AND approval_date < DATE '2026-10-02') OR "
            "(cancel_date >= DATE '2026-09-17' AND cancel_date < DATE '2026-10-02') OR "
            "(expire_date >= DATE '2026-09-17' AND expire_date < DATE '2026-10-02')"
        )
        self.assertEqual(captured["url"], OK_ITD)
        self.assertEqual(captured["where"], expected_where)
        self.assertIn("2026-09-17", captured["where"])
        self.assertIn("2026-10-02", captured["where"])
        self.assertEqual(captured["delay"], 0.12)
        self.assertNotIn("1=1", captured["where"])

        apis = [row["api"] for row in rows]
        self.assertEqual(
            apis,
            ["3500100001", "3500100002", "3500100006", "3500100007", "3500100010"],
        )
        self.assertNotIn("3500100003", apis)
        self.assertNotIn("3500100004", apis)
        self.assertNotIn("3500100005", apis)
        self.assertNotIn("3500100008", apis)
        self.assertNotIn("3500100009", apis)
        self.assertNotIn("3500100011", apis)

        by_api = {row["api"]: row for row in rows}
        self.assertEqual(by_api["3500100010"]["status"], "approved")
        self.assertEqual(by_api["3500100010"]["symbol"], "Permitted")
        self.assertEqual(by_api["3500100001"]["approved_at"], "2026-09-20")
        self.assertEqual(by_api["3500100001"]["status"], "approved")
        self.assertEqual(by_api["3500100001"]["source"], "ok_occ_itd")
        self.assertEqual(by_api["3500100001"]["wellhead_lat"], 35.5)
        self.assertEqual(by_api["3500100001"]["wellhead_lon"], -97.5)
        self.assertEqual(by_api["3500100006"]["approved_at"], "2024-01-15")
        self.assertEqual(by_api["3500100007"]["approved_at"], "2024-02-01")
        for row in rows:
            self.assertEqual(list(row), PERMIT_FIELDS)
            self.assertTrue(row["api"].startswith("35"))
            self.assertTrue(row["approved_at"])
            self.assertEqual(row["source"], "ok_occ_itd")
