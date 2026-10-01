"""New Mexico OCD permit pull. HTTP is mocked; no live network."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from wellnav.ingest.nm_permit_pull import (
    DATE_FIELD,
    NM_PERMIT_QUERY,
    SPUD_SENTINEL,
    fetch_new_mexico_permits,
)
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS

def _epoch_ms(day: str) -> int:
    parsed = datetime.fromisoformat(day).replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


IN_WINDOW_MS = _epoch_ms("2026-09-20")
OUTSIDE_MS = _epoch_ms("2020-01-15")


class _Response:
    def __init__(self, payload: dict):
        self.status_code = 200
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _feature(
    api: str,
    *,
    status: str = "New",
    effective_ms: int | None = IN_WINDOW_MS,
    spud_ms: int | None = SPUD_SENTINEL,
    lat: float | None = 32.4,
    lon: float | None = -104.1,
) -> dict:
    return {
        "attributes": {
            "id": api,
            "name": "PALE RIDER 8 5 STATE COM #551H",
            "status": status,
            "ogrid_name": "Example Operator LLC",
            "ogrid": 14744,
            "county": "Eddy",
            "district": "Artesia",
            "directional_status": "H",
            "projection": "NAD83",
            "latitude": lat,
            "longitude": lon,
            DATE_FIELD: effective_ms,
            "spud_date": spud_ms,
        },
        "geometry": {"x": lon, "y": lat} if lat is not None else None,
    }


class FetchNewMexicoPermitsTest(unittest.TestCase):
    def test_query_uses_date_window_and_drops_outside_and_sentinel_spud(self) -> None:
        pages = [
            {
                "features": [
                    _feature("30-015-45607"),
                    _feature("30-025-11111", effective_ms=OUTSIDE_MS),
                    _feature("30-015-22222", status="Cancelled", effective_ms=None, spud_ms=SPUD_SENTINEL),
                    _feature("30-005-33333", status="New", effective_ms=SPUD_SENTINEL, spud_ms=SPUD_SENTINEL),
                    _feature("30-015-44444", spud_ms=_epoch_ms("2025-08-01")),
                ]
            },
            {"features": []},
        ]

        def fake_get(url, params=None, **kwargs):
            self.assertEqual(url, NM_PERMIT_QUERY)
            self.assertIn("Wells/FeatureServer/0", url)
            self.assertNotIn("Wells_Public", url)
            return _Response(pages.pop(0))

        with patch("wellnav.ingest.arcgis.requests.get", side_effect=fake_get) as mocked:
            rows = fetch_new_mexico_permits(date(2026, 9, 17), date(2026, 10, 1), delay=0)

        self.assertEqual(mocked.call_count, 1)
        where = mocked.call_args_list[0].kwargs["params"]["where"]
        self.assertIn(f"{DATE_FIELD} >= DATE '2026-09-17'", where)
        self.assertIn(f"{DATE_FIELD} < DATE '2026-10-02'", where)
        self.assertIn("year_spudded = '9999'", where)
        self.assertNotIn("spud_date", where)

        self.assertEqual([row["api"] for row in rows], ["3001545607"])
        row = rows[0]
        self.assertEqual(set(row), set(PERMIT_FIELDS))
        self.assertEqual(row["api8"], "01545607")
        self.assertEqual(row["permit_no"], "30-015-45607")
        self.assertEqual(row["status"], "approved")
        self.assertEqual(row["approved_at"], "2026-09-20")
        self.assertNotEqual(row["approved_at"], "9999-12-31")
        self.assertIsNone(row["submitted_at"])
        self.assertEqual(row["wellhead_lat"], 32.4)
        self.assertEqual(row["wellhead_lon"], -104.1)
        self.assertEqual(row["wellhead_crs"], "EPSG:4269")
        self.assertEqual(row["source"], "nm_ocd")
        self.assertEqual(row["lifetime_days"], DEFAULT_PERMIT_LIFETIME_DAYS)
        self.assertEqual(row["county_code"], "015")
        self.assertEqual(row["well_no"], "551H")
        self.assertEqual(row["operator"], "EXAMPLE OPERATOR LLC")

    def test_cancelled_maps_and_missing_coordinates_stay_null(self) -> None:
        pages = [
            {
                "features": [
                    _feature(
                        "30-025-56990",
                        status="Cancelled",
                        lat=None,
                        lon=None,
                    )
                ]
            },
            {"features": []},
        ]

        def fake_get(url, params=None, **kwargs):
            return _Response(pages.pop(0))

        with patch("wellnav.ingest.arcgis.requests.get", side_effect=fake_get):
            rows = fetch_new_mexico_permits(date(2026, 9, 17), date(2026, 10, 1), delay=0)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["api"], "3002556990")
        self.assertEqual(rows[0]["status"], "cancelled")
        self.assertEqual(rows[0]["approved_at"], "2026-09-20")
        self.assertIsNone(rows[0]["wellhead_lat"])
        self.assertIsNone(rows[0]["wellhead_lon"])


if __name__ == "__main__":
    unittest.main()
