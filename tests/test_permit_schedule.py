"""Weekly permit-window scheduling. No agency HTTP."""

from __future__ import annotations

import sqlite3
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

from wellnav.db import get_cursor, init_schema, set_cursor
from wellnav.ingest.permit_schedule import (
    refresh_permitted_locations,
    store_permits,
    window_for_state,
)
from wellnav.ingest.permits import DEFAULT_LOOKBACK_DAYS
from wellnav.ingest.persist import upsert_permits, upsert_wells

NOW = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
STAMP = "2026-09-01T00:00:00+00:00"


def _permit(
    api8: str,
    permit_no: str,
    approved_at: str | None,
    *,
    lat: float | None = 31.5,
) -> dict:
    return {
        "api": f"42{api8}",
        "api8": api8,
        "permit_no": permit_no,
        "status": "approved",
        "well_name": f"Permit {permit_no}",
        "well_no": "1H",
        "lease_name": "LEASE",
        "lease_no": "1",
        "county": "REEVES",
        "county_code": "389",
        "district": "08",
        "operator": "TEST OPERATOR",
        "operator_number": "100",
        "profile": "horizontal",
        "symbol": "Permitted",
        "symnum": 2,
        "wellhead_lat": lat,
        "wellhead_lon": -103.2 if lat is not None else None,
        "wellhead_crs": "nad83" if lat is not None else None,
        "approved_at": approved_at,
        "submitted_at": "2026-09-01",
        "expires_at": "2028-09-01",
        "lifetime_days": 730,
        "as_drilled_ready": 0,
        "migrated_at": None,
        "source": "test",
        "first_seen_at": STAMP,
        "last_seen_at": STAMP,
        "updated_at": STAMP,
    }


def _well(api8: str, symbol: str, symnum: int | None) -> dict:
    return {
        "api": f"42{api8}",
        "api8": api8,
        "well_name": symbol,
        "symbol": symbol,
        "symnum": symnum,
        "wellhead_lat": 31.1,
        "wellhead_lon": -101.0,
        "source": "test",
        "first_seen_at": STAMP,
        "last_seen_at": STAMP,
        "updated_at": STAMP,
    }


def _permit_numbers(conn: sqlite3.Connection, state: str) -> set[str]:
    table = f"permits_{state}"
    return {row["permit_no"] for row in conn.execute(f"SELECT permit_no FROM {table}")}


def _wells(conn: sqlite3.Connection, state: str) -> dict[str, sqlite3.Row]:
    table = f"wells_{state}"
    return {row["api8"]: row for row in conn.execute(f"SELECT * FROM {table}")}


class WindowForStateTest(unittest.TestCase):
    def test_cursor_date_is_window_start(self) -> None:
        start, end = window_for_state("2026-09-24", now=NOW)
        self.assertEqual(start, date(2026, 9, 24))
        self.assertEqual(end, date(2026, 10, 1))

        # 2026-09-24 18:00 UTC is still that calendar day in America/Chicago.
        start, end = window_for_state("2026-09-24T18:00:00+00:00", now=NOW)
        self.assertEqual(start, date(2026, 9, 24))
        self.assertEqual(end, date(2026, 10, 1))

        # 2026-09-25 03:00 UTC is 2026-09-24 22:00 CDT.
        start, end = window_for_state("2026-09-25T03:00:00+00:00", now=NOW)
        self.assertEqual(start, date(2026, 9, 24))
        self.assertEqual(end, date(2026, 10, 1))

    def test_missing_cursor_looks_back_seven_days(self) -> None:
        self.assertEqual(DEFAULT_LOOKBACK_DAYS, 7)
        start, end = window_for_state(None, now=NOW)
        self.assertEqual(end, date(2026, 10, 1))
        self.assertEqual(start, end - timedelta(days=7))
        self.assertEqual(start, date(2026, 9, 24))
        self.assertNotEqual(start, end - timedelta(days=730))

        start, end = window_for_state("  ", now=NOW)
        self.assertEqual(start, date(2026, 9, 24))

    def test_explicit_dates_override_cursor_and_clamp(self) -> None:
        start, end = window_for_state(
            "2020-01-01",
            now=NOW,
            from_date=date(2024, 10, 1),
            to_date=date(2026, 10, 1),
        )
        self.assertEqual(start, date(2024, 10, 1))
        self.assertEqual(end, date(2026, 10, 1))

        start, end = window_for_state(
            None,
            from_date=date(2026, 10, 8),
            to_date=date(2026, 10, 1),
        )
        self.assertEqual(start, date(2026, 10, 1))
        self.assertEqual(end, date(2026, 10, 1))

        # 2026-10-02 03:00 UTC is 2026-10-01 22:00 CDT.
        chicago_evening = datetime(2026, 10, 2, 3, 0, tzinfo=timezone.utc)
        start, end = window_for_state("2026-09-24", now=chicago_evening)
        self.assertEqual(end, date(2026, 10, 1))
        self.assertEqual(start, date(2026, 9, 24))


class StorePermitsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_replace_deletes_permitted_wells_and_all_permits(self) -> None:
        upsert_wells(
            self.conn,
            "tx",
            [
                _well("00100001", "Oil Well", None),
                _well("00100002", "Gas Well", None),
                _well("00100003", "Permitted", 2),
                _well("00100004", "Canceled Location", 9),
                _well("00100005", "Surface", 2),
            ],
        )
        upsert_permits(
            self.conn,
            "tx",
            [
                _permit("11111111", "OLD-A", "2026-08-01"),
                _permit("22222222", "OLD-B", "2026-08-02"),
            ],
        )
        self.conn.commit()

        result = store_permits(
            self.conn,
            "tx",
            [_permit("33333333", "NEW", "2026-09-28", lat=None)],
            replace=True,
        )

        self.assertEqual(result["state"], "tx")
        self.assertTrue(result["replace"])
        self.assertEqual(result["deleted_permits"], 2)
        self.assertEqual(result["deleted_permitted_wells"], 3)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"NEW"})
        stored = self.conn.execute(
            "SELECT wellhead_lat FROM permits_tx WHERE permit_no = 'NEW'"
        ).fetchone()
        self.assertIsNone(stored["wellhead_lat"])

        wells = _wells(self.conn, "tx")
        self.assertEqual(wells["00100001"]["symbol"], "Oil Well")
        self.assertEqual(wells["00100002"]["symbol"], "Gas Well")
        self.assertNotIn("00100003", wells)
        self.assertNotIn("00100004", wells)
        self.assertNotIn("00100005", wells)
        self.assertNotIn("33333333", wells)
        self.assertEqual(get_cursor(self.conn, "tx_permits_refreshed_at"), result["cursor"])
        self.assertFalse(self.conn.in_transaction)

    def test_upsert_leaves_older_permit_in_place(self) -> None:
        upsert_wells(self.conn, "tx", [_well("00100003", "Permitted", 2)])
        upsert_permits(self.conn, "tx", [_permit("11111111", "OLD", "2024-01-15")])
        self.conn.commit()

        result = store_permits(
            self.conn,
            "tx",
            [_permit("22222222", "NEW", "2026-09-30")],
            replace=False,
        )

        self.assertFalse(result["replace"])
        self.assertEqual(result["deleted_permits"], 0)
        self.assertEqual(result["deleted_permitted_wells"], 0)
        self.assertEqual(result["stored"], 1)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"OLD", "NEW"})
        wells = _wells(self.conn, "tx")
        self.assertEqual(wells["00100003"]["symbol"], "Permitted")
        self.assertEqual(get_cursor(self.conn, "tx_permits_refreshed_at"), result["cursor"])

    def test_rows_are_not_inserted_into_wells(self) -> None:
        good = _permit("44444444", "KEEP", "2026-09-20", lat=None)
        result = store_permits(
            self.conn,
            "tx",
            [
                good,
                _permit("55555555", "MDY", "09/20/2026"),
                _permit("66666666", "NONE", None),
                _permit("77777777", "STAMP", "2026-09-20T00:00:00"),
                _permit("88888888", "BADDAY", "2026-02-31"),
            ],
            replace=False,
        )
        self.assertEqual(result["stored"], 1)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"KEEP"})
        row = self.conn.execute(
            "SELECT wellhead_lat FROM permits_tx WHERE api8 = '44444444'"
        ).fetchone()
        self.assertIsNone(row["wellhead_lat"])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM wells_tx").fetchone()[0],
            0,
        )
        self.assertIsNone(
            self.conn.execute(
                "SELECT api FROM wells_tx WHERE api8 = ?",
                (good["api8"],),
            ).fetchone()
        )

    def test_upsert_failure_rolls_back_without_moving_cursor(self) -> None:
        old_cursor = "2026-09-24T12:00:00+00:00"
        set_cursor(self.conn, "tx_permits_refreshed_at", old_cursor, old_cursor)
        upsert_wells(
            self.conn,
            "tx",
            [
                _well("00100001", "Oil Well", None),
                _well("00100003", "Permitted", 2),
            ],
        )
        upsert_permits(self.conn, "tx", [_permit("11111111", "OLD", "2026-08-01")])
        self.conn.commit()

        from wellnav.ingest import persist as persist_mod

        def boom(conn, state, rows):
            persist_mod.upsert_permits(conn, state, rows)
            current = get_cursor(conn, "tx_permits_refreshed_at")
            if current != old_cursor:
                raise AssertionError(f"cursor moved before upsert returned: {current}")
            raise RuntimeError("upsert failed")

        with patch("wellnav.ingest.permit_schedule.upsert_permits", side_effect=boom):
            with self.assertRaises(RuntimeError):
                store_permits(
                    self.conn,
                    "tx",
                    [_permit("33333333", "NEW", "2026-09-28")],
                    replace=True,
                )

        self.assertEqual(get_cursor(self.conn, "tx_permits_refreshed_at"), old_cursor)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"OLD"})
        self.assertIn("00100003", _wells(self.conn, "tx"))

        self.conn.rollback()

        self.assertEqual(get_cursor(self.conn, "tx_permits_refreshed_at"), old_cursor)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"OLD"})
        self.assertNotIn("NEW", _permit_numbers(self.conn, "tx"))
        wells = _wells(self.conn, "tx")
        self.assertEqual(wells["00100001"]["symbol"], "Oil Well")
        self.assertEqual(wells["00100003"]["symbol"], "Permitted")


class RefreshPermittedLocationsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_fetcher_failure_does_not_advance_cursor_or_delete(self) -> None:
        old_cursor = "2026-09-24T18:00:00+00:00"
        set_cursor(self.conn, "tx_permits_refreshed_at", old_cursor, old_cursor)
        upsert_wells(
            self.conn,
            "tx",
            [
                _well("00100001", "Oil Well", None),
                _well("00100003", "Permitted", 2),
            ],
        )
        upsert_permits(self.conn, "tx", [_permit("11111111", "OLD", "2026-09-01")])
        self.conn.commit()

        seen: dict[str, tuple[date, date]] = {}

        def tx_fetch(start, end):
            seen["tx"] = (start, end)
            raise RuntimeError("gis down")

        def nm_fetch(start, end):
            seen["nm"] = (start, end)
            return [_permit("30000001", "NM-1", "2026-09-29")]

        result = refresh_permitted_locations(
            self.conn,
            ("tx", "nm"),
            now=NOW,
            replace=True,
            fetchers={"tx": tx_fetch, "nm": nm_fetch},
        )

        self.assertEqual(seen["tx"], (date(2026, 9, 24), date(2026, 10, 1)))
        self.assertEqual(seen["nm"], (date(2026, 9, 24), date(2026, 10, 1)))
        self.assertEqual(result["tx"]["status"], "failed")
        self.assertEqual(result["tx"]["deleted_permits"], 0)
        self.assertEqual(result["tx"]["cursor"], old_cursor)
        self.assertIn("gis down", result["tx"]["error"])
        self.assertEqual(get_cursor(self.conn, "tx_permits_refreshed_at"), old_cursor)
        self.assertEqual(_permit_numbers(self.conn, "tx"), {"OLD"})
        wells = _wells(self.conn, "tx")
        self.assertEqual(wells["00100001"]["symbol"], "Oil Well")
        self.assertEqual(wells["00100003"]["symbol"], "Permitted")

        self.assertEqual(result["nm"]["status"], "ok")
        self.assertEqual(result["nm"]["stored"], 1)
        self.assertEqual(_permit_numbers(self.conn, "nm"), {"NM-1"})
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM wells_nm").fetchone()[0],
            0,
        )
        self.assertEqual(
            get_cursor(self.conn, "nm_permits_refreshed_at"),
            result["nm"]["cursor"],
        )
        self.assertNotEqual(result["nm"]["cursor"], None)

    def test_explicit_window_is_what_the_fetcher_receives(self) -> None:
        set_cursor(
            self.conn,
            "la_permits_refreshed_at",
            "2020-01-01",
            "2020-01-01T00:00:00+00:00",
        )
        seen: dict[str, tuple[date, date]] = {}

        def la_fetch(start, end):
            seen["la"] = (start, end)
            return [_permit("17000001", "LA-1", "2025-06-01")]

        result = refresh_permitted_locations(
            self.conn,
            ("la",),
            now=NOW,
            from_date=date(2024, 10, 1),
            to_date=date(2026, 10, 1),
            replace=True,
            fetchers={"la": la_fetch},
        )
        self.assertEqual(seen["la"], (date(2024, 10, 1), date(2026, 10, 1)))
        self.assertEqual(result["la"]["status"], "ok")
        self.assertEqual(result["la"]["deleted_permits"], 0)
        self.assertEqual(_permit_numbers(self.conn, "la"), {"LA-1"})
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM wells_la").fetchone()[0],
            0,
        )


if __name__ == "__main__":
    unittest.main()
