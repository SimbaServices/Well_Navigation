"""Louisiana SONRIS permit pull. iter_features is mocked; no live network."""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from wellnav.ingest.la_permit_pull import (
    SONRIS_OC_WELLS,
    fetch_louisiana_permits,
)
from wellnav.ingest.persist import PERMIT_FIELDS
from wellnav.states import DEFAULT_PERMIT_LIFETIME_DAYS

START = date(2024, 10, 1)
END = date(2026, 10, 1)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _ms(day: str) -> int:
    parsed = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return int((parsed - _EPOCH).total_seconds() * 1000)


def _feature(
    *,
    api: str = "17017259990000",
    serial: int = 253001,
    status: str = "01",
    legend: str = "PERMITTED",
    permit: str = "2025-06-15",
    spud: int | None = None,
    lat: float = 32.5,
    lon: float = -93.75,
    surface: bool = True,
    geometry: bool = True,
    operator: str = "Apex Natural Gas, LLC",
    parish: str = "CADDO",
    well_name: str = "FRNK 3",
    well_num: str = "001",
) -> dict:
    attrs = {
        "WELL_SERIAL_NUM": serial,
        "API_NUM": api,
        "PERMIT_DATE": _ms(permit),
        "SPUD_DATE": spud,
        "WELL_STATUS_CODE": status,
        "LEGEND_DESC": legend,
        "PARISH_NAME": parish,
        "ORG_OPER_NAME": operator,
        "ORGANIZATION_ID": "A1169",
        "WELL_NAME": well_name,
        "WELL_NUM": well_num,
        "LEASE_NUM": "L1",
        "LUW_NAME": "FRNK",
        "DISTRICT_CODE": "3N",
        "SURFACE_LAT_DEC_DEG": lat if surface else None,
        "SURFACE_LONG_DEC_DEG": lon if surface else None,
    }
    feature: dict = {"attributes": attrs}
    if geometry:
        feature["geometry"] = {"x": lon, "y": lat}
    return feature


class FetchLouisianaPermitsTest(unittest.TestCase):
    def test_where_keeps_undrilled_and_drops_producing_spud(self) -> None:
        undrilled = _feature()
        producing = _feature(
            api="17017260000000",
            serial=253002,
            status="10",
            legend="ACTIVE - PRODUCING  GAS",
            spud=_ms("2025-07-01"),
            well_name="HOSSTON",
        )
        spudded_permit = _feature(
            api="17013223490000",
            serial=255420,
            status="01",
            legend="PERMITTED",
            spud=_ms("2025-11-28"),
            well_name="SPUDDED",
        )
        expired = _feature(
            api="17031270010000",
            serial=253003,
            status="03",
            legend="PERMIT EXPIRED",
            permit="2024-11-02",
            well_name="OLD PERMIT",
        )
        outside = _feature(
            api="17017370460000",
            serial=255683,
            lat=32.74,
            lon=-98.50,
            well_name="BAD COORDS",
        )
        zero = _feature(
            api="17019220010000",
            serial=253004,
            lat=0.0,
            lon=0.0,
            well_name="NO LOCATION",
        )
        sentinel = _feature(
            api="17023220010000",
            serial=253005,
            spud=_ms("9999-01-01"),
            well_name="SENTINEL SPUD",
        )
        out_of_window = _feature(
            api="17023220020000",
            serial=253006,
            permit="2020-01-01",
            well_name="TOO OLD",
        )
        features = [
            undrilled,
            producing,
            spudded_permit,
            expired,
            outside,
            zero,
            sentinel,
            out_of_window,
        ]
        with patch(
            "wellnav.ingest.la_permit_pull.iter_features",
            return_value=features,
        ) as mocked:
            rows = fetch_louisiana_permits(START, END, delay=0)

        args, kwargs = mocked.call_args
        where = kwargs["where"]
        self.assertEqual(args[0], SONRIS_OC_WELLS)
        self.assertNotIn("dotd.la.gov", args[0])
        self.assertIn("PERMIT_DATE", where)
        self.assertIn(START.isoformat(), where)
        self.assertIn(END.isoformat(), where)
        self.assertNotIn("SPUD_DATE", where)
        self.assertLessEqual(kwargs["page_size"], 1000)
        self.assertEqual(kwargs["delay"], 0)

        by_api = {row["api"]: row for row in rows}
        self.assertNotIn("1701726000", by_api)
        self.assertNotIn("1701322349", by_api)
        self.assertNotIn("1701737046", by_api)
        self.assertNotIn("1702322002", by_api)

        kept = by_api["1701725999"]
        self.assertEqual(kept["status"], "approved")
        self.assertEqual(kept["source"], "la_sonris")
        self.assertEqual(kept["approved_at"], "2025-06-15")
        self.assertEqual(kept["permit_no"], "253001")
        self.assertEqual(kept["api8"], "01725999")
        self.assertEqual(kept["county"], "CADDO")
        self.assertEqual(kept["county_code"], "017")
        self.assertEqual(kept["operator"], "APEX NATURAL GAS, LLC")
        self.assertEqual(kept["lifetime_days"], DEFAULT_PERMIT_LIFETIME_DAYS)
        self.assertEqual(
            kept["expires_at"],
            (date(2025, 6, 15) + timedelta(days=DEFAULT_PERMIT_LIFETIME_DAYS)).isoformat(),
        )
        self.assertEqual(kept["wellhead_lat"], 32.5)
        self.assertEqual(kept["wellhead_lon"], -93.75)
        self.assertEqual(list(kept), PERMIT_FIELDS)

        self.assertEqual(by_api["1703127001"]["status"], "expired")
        self.assertEqual(by_api["1702322001"]["status"], "approved")
        blank = by_api["1701922001"]
        self.assertIsNone(blank["wellhead_lat"])
        self.assertIsNone(blank["wellhead_lon"])
        self.assertEqual(blank["status"], "approved")

    def test_missing_api_uses_serial_and_cancelled_legend_is_kept(self) -> None:
        serial_only = _feature(api="0", serial=255438, well_name="NO API")
        cancelled = _feature(
            api="17045220010000",
            serial=253010,
            status="04",
            legend="PERMIT CANCELLED",
            well_name="CANCELLED",
        )
        with patch(
            "wellnav.ingest.la_permit_pull.iter_features",
            return_value=[serial_only, cancelled],
        ):
            rows = fetch_louisiana_permits(START, END, delay=0)
        by_permit = {row["permit_no"]: row for row in rows}
        serial_row = by_permit["255438"]
        self.assertEqual(serial_row["api"], "1790255438")
        self.assertEqual(serial_row["api8"], "90255438")
        self.assertEqual(by_permit["253010"]["status"], "cancelled")

    def test_same_api_keeps_expired_and_approved_serials(self) -> None:
        approved = _feature(
            api="17047211660000",
            serial=255846,
            status="01",
            permit="2026-09-01",
            well_name="BLACKSTONE MINERALS",
        )
        expired = _feature(
            api="17047211660000",
            serial=255292,
            status="03",
            legend="PERMIT EXPIRED",
            permit="2025-01-15",
            well_name="BLACKSTONE MINERALS",
        )
        with patch(
            "wellnav.ingest.la_permit_pull.iter_features",
            return_value=[approved, expired],
        ):
            rows = fetch_louisiana_permits(START, END, delay=0)
        self.assertEqual(sorted(row["permit_no"] for row in rows), ["255292", "255846"])
        self.assertEqual({row["status"] for row in rows}, {"approved", "expired"})
        self.assertEqual({row["api"] for row in rows}, {"1704721166"})


if __name__ == "__main__":
    unittest.main()
