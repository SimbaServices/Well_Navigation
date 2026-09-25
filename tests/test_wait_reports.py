from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from wellnav import wait_reports as wr


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


class WaitReportsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.db = Path(self._tmp.name) / "wait_reports.db"
        self.now = datetime(2026, 3, 15, 18, 0, 0, tzinfo=timezone.utc)
        self.now_iso = _iso(self.now)

    def _create(self, **kwargs):
        defaults = {
            "disposal_site_id": 10,
            "user_id": 1,
            "report_kind": "actual",
            "arrival_at": _iso(self.now - timedelta(hours=2)),
            "departure_at": _iso(self.now - timedelta(hours=1)),
            "open_lanes": 3,
            "now": self.now_iso,
            "path": self.db,
        }
        defaults.update(kwargs)
        return wr.create_report(**defaults)

    def test_schema_and_create_actual(self) -> None:
        report = self._create()
        self.assertEqual(report["report_kind"], "actual")
        self.assertEqual(report["wait_minutes"], 60)
        self.assertEqual(report["open_lanes"], 3)
        self.assertFalse(report["is_flagged"])
        self.assertEqual(report["status"], "active")

        conn = wr.connect(self.db)
        try:
            wr.init_schema(conn)
            tables = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            self.assertIn("reports", tables)
            self.assertIn("user_prefs", tables)
            self.assertIn("report_flags", tables)
        finally:
            conn.close()

    def test_actual_rejects_future(self) -> None:
        with self.assertRaisesRegex(ValueError, "future"):
            self._create(
                arrival_at=_iso(self.now + timedelta(hours=1)),
                departure_at=_iso(self.now + timedelta(hours=2)),
            )

    def test_partial_rejects_departure_and_future(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot include departure"):
            self._create(
                report_kind="partial",
                arrival_at=_iso(self.now - timedelta(minutes=30)),
                departure_at=_iso(self.now - timedelta(minutes=5)),
            )
        with self.assertRaisesRegex(ValueError, "future"):
            self._create(
                report_kind="partial",
                arrival_at=_iso(self.now + timedelta(hours=1)),
                departure_at=None,
            )
        partial = self._create(
            report_kind="partial",
            arrival_at=_iso(self.now - timedelta(minutes=15)),
            departure_at=None,
            open_lanes=2,
        )
        self.assertIsNone(partial["wait_minutes"])
        self.assertIsNone(partial["departure_at"])

    def test_estimated_must_be_future(self) -> None:
        with self.assertRaisesRegex(ValueError, "future"):
            self._create(
                report_kind="estimated",
                arrival_at=_iso(self.now - timedelta(minutes=5)),
                departure_at=_iso(self.now + timedelta(hours=1)),
                org_id=7,
            )
        with self.assertRaisesRegex(ValueError, "departure"):
            self._create(
                report_kind="estimated",
                arrival_at=_iso(self.now + timedelta(hours=1)),
                departure_at=None,
                org_id=7,
            )
        estimated = self._create(
            report_kind="estimated",
            arrival_at=_iso(self.now + timedelta(hours=1)),
            departure_at=_iso(self.now + timedelta(hours=2)),
            org_id=7,
            open_lanes=4,
        )
        self.assertEqual(estimated["wait_minutes"], 60)
        self.assertEqual(estimated["org_id"], 7)
        with self.assertRaisesRegex(ValueError, "team membership"):
            self._create(
                report_kind="estimated",
                arrival_at=_iso(self.now + timedelta(hours=1)),
                departure_at=_iso(self.now + timedelta(hours=2)),
                org_id=None,
            )

    def test_actual_requires_departure(self) -> None:
        with self.assertRaisesRegex(ValueError, "departure"):
            self._create(
                arrival_at=_iso(self.now - timedelta(minutes=30)),
                departure_at=None,
            )

    def test_interval_and_lanes_bounds(self) -> None:
        with self.assertRaisesRegex(ValueError, "24 hours"):
            self._create(
                arrival_at=_iso(self.now - timedelta(hours=30)),
                departure_at=_iso(self.now - timedelta(hours=1)),
            )
        with self.assertRaisesRegex(ValueError, "open_lanes"):
            self._create(open_lanes=21)
        with self.assertRaisesRegex(ValueError, "open_lanes"):
            self._create(open_lanes=-1)
        ok = self._create(open_lanes=0)
        self.assertEqual(ok["open_lanes"], 0)
        ok20 = self._create(open_lanes=20, user_id=2)
        self.assertEqual(ok20["open_lanes"], 20)

    def test_auto_flag_outlier_wait(self) -> None:
        for i in range(3):
            self._create(
                user_id=10 + i,
                arrival_at=_iso(self.now - timedelta(hours=2)),
                departure_at=_iso(self.now - timedelta(hours=2) + timedelta(minutes=30)),
                open_lanes=3,
            )
        outlier = self._create(
            user_id=99,
            arrival_at=_iso(self.now - timedelta(hours=4)),
            departure_at=_iso(self.now - timedelta(hours=1)),
            open_lanes=3,
        )
        self.assertTrue(outlier["auto_flagged"])
        self.assertTrue(outlier["is_flagged"])
        self.assertIn("outlier", outlier["flag_reason"])

        flagged = wr.list_flagged_reports(path=self.db)
        self.assertTrue(any(row["id"] == outlier["id"] for row in flagged))

    def test_flag_report_and_summary(self) -> None:
        a = self._create(user_id=1, open_lanes=2)
        b = self._create(
            user_id=2,
            arrival_at=_iso(self.now - timedelta(hours=3)),
            departure_at=_iso(self.now - timedelta(hours=2)),
            open_lanes=5,
        )
        flagged = wr.flag_report(a["id"], user_id=3, reason="Looks wrong", path=self.db)
        self.assertTrue(flagged["is_flagged"])
        self.assertEqual(flagged["flagged_by_user_id"], 3)

        summary = wr.get_site_wait_summary(
            10, window_hours=24, org_id=7, now=self.now_iso, path=self.db
        )
        self.assertEqual(summary["disposal_site_id"], 10)
        self.assertEqual(summary["report_count"], 2)
        # Flagged report excluded from average.
        self.assertEqual(summary["avg_wait_minutes"], 60.0)
        self.assertEqual(summary["open_lanes_latest"], 5)
        self.assertEqual(len(summary["recent_reports"]), 2)

        self._create(
            report_kind="estimated",
            arrival_at=_iso(datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=2)),
            departure_at=_iso(datetime.now(timezone.utc).replace(microsecond=0) + timedelta(hours=3)),
            org_id=7,
            open_lanes=1,
            user_id=8,
            now=_iso(datetime.now(timezone.utc).replace(microsecond=0)),
        )
        with_org = wr.get_site_wait_summary(
            10, window_hours=24, org_id=7, now=self.now_iso, path=self.db
        )
        self.assertEqual(len(with_org["estimated_for_org"]), 1)
        other_org = wr.get_site_wait_summary(
            10, window_hours=24, org_id=99, now=self.now_iso, path=self.db
        )
        self.assertEqual(other_org["estimated_for_org"], [])
        self.assertIsNotNone(b["id"])

    def test_user_prefs(self) -> None:
        default = wr.get_user_pref(42, path=self.db)
        self.assertEqual(default["avg_window_hours"], 24)
        self.assertIsNone(default["updated_at"])

        saved = wr.set_user_pref(42, 48, path=self.db)
        self.assertEqual(saved["avg_window_hours"], 48)
        self.assertIsNotNone(saved["updated_at"])

        again = wr.get_user_pref(42, path=self.db)
        self.assertEqual(again["avg_window_hours"], 48)

        clamped = wr.set_user_pref(42, 999, path=self.db)
        self.assertEqual(clamped["avg_window_hours"], 168)
        low = wr.set_user_pref(42, 0, path=self.db)
        self.assertEqual(low["avg_window_hours"], 1)


if __name__ == "__main__":
    unittest.main()
