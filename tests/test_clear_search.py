"""Clearing filters must not scan every well into the results table."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import wellnav.db as dbmod

# Point the process at an empty database before the app opens the local file.
_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_db.close()
dbmod.DB_PATH = Path(_db.name)

from starlette.testclient import TestClient

from app import app

USER = {
    "id": 999999,
    "username": "tester",
    "email": "tester@example.com",
    "org_id": None,
}


class ClearSearchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.patches = [
            patch("app.current_user", return_value=USER),
            patch("app.billing_enforced", return_value=False),
            patch("app.USERS.touch_activity"),
            patch("app.SAVED.api_set", return_value=set()),
            patch("app.CACHE.recent_searches", return_value=[]),
            patch("app.CACHE.stats", return_value=None),
            patch("app.CACHE.remember_recent"),
            patch("app.CACHE.set"),
            patch("app.REPO.search_leases", return_value=[]),
            patch(
                "app.REPO.counts",
                return_value={"wells": 0, "permits": 0, "live_permits": 0, "total": 0},
            ),
        ]
        for item in self.patches:
            item.start()
        self.search = patch("app.REPO.search").start()
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.search.stop()
        for item in self.patches:
            item.stop()

    def test_clear_filters_keeps_the_table_and_skips_the_query(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "clear_filters": "1",
                "opn": "123|OXY",
                "op": "123",
                "name": "UNI",
                "use_name": "1",
                "state": "tx",
                "mode": "name",
            },
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_not_called()
        self.assertEqual(response.headers.get("hx-reswap"), "none")
        self.assertIn("state=tx", response.headers.get("hx-push-url", ""))
        self.assertNotIn("clear_filters", response.headers.get("hx-push-url", ""))
        self.assertNotIn("opn=", response.headers.get("hx-push-url", ""))
        self.assertIn('id="active-filters"', response.text)
        self.assertNotIn("filter-chip", response.text)
        self.assertNotIn("data-results-table", response.text)
        self.assertNotIn("Pick a state or All, then search.", response.text)

    def test_removing_the_last_chip_does_not_scan_every_well(self) -> None:
        response = self.client.get(
            "/search",
            params={
                "opn": "123|OXY",
                "op": "123",
                "remove_op": "123",
                "state": "nm",
                "mode": "name",
            },
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_not_called()
        self.assertEqual(response.headers.get("hx-reswap"), "none")
        self.assertNotIn("filter-chip", response.text)

    def test_empty_submit_prompts_instead_of_scanning(self) -> None:
        response = self.client.get(
            "/search",
            params={"commit": "1", "q": "", "mode": "name", "state": "tx"},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_not_called()
        self.assertIsNone(response.headers.get("hx-reswap"))
        self.assertIn("Pick a state or All, then search.", response.text)

    def test_named_search_still_queries(self) -> None:
        self.search.return_value = {"wells": [], "total": 0, "start": 0, "end": 0}
        response = self.client.get(
            "/search",
            params={"commit": "1", "q": "UNIVERSITY", "mode": "name", "state": "tx"},
            headers={"HX-Request": "true"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_called_once()
        self.assertEqual(self.search.call_args.kwargs["name"], "UNIVERSITY")
        self.assertIsNone(response.headers.get("hx-reswap"))
        self.assertIn('hx-swap="none"', response.text)
        self.assertIn("UNIVERSITY", response.text)

    def test_column_filter_still_queries(self) -> None:
        self.search.return_value = {"wells": [], "total": 0, "start": 0, "end": 0}
        response = self.client.get(
            "/search",
            params={"cf_operator": "OXY", "mode": "name", "state": "tx"},
            headers={"HX-Request": "true", "X-Column-Filter": "1"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_called_once()
        self.assertEqual(self.search.call_args.kwargs["column_filters"], {"operator": "OXY"})
        self.assertIsNone(response.headers.get("hx-reswap"))

    def test_full_page_clear_does_not_list_wells(self) -> None:
        response = self.client.get(
            "/search",
            params={"clear_filters": "1", "opn": "123|OXY", "op": "123", "state": "tx"},
        )
        self.assertEqual(response.status_code, 200)
        self.search.assert_not_called()
        self.assertIn("Pick a state or All, then search.", response.text)
        self.assertNotIn("data-results-table", response.text)
