"""Location and search choices stay on the user account."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from wellnav.db import init_schema
from wellnav.user_settings import get_search_prefs, save_search_prefs


class UserSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = sqlite3.connect(Path(self.tmp.name) / "wellnav.db")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.conn.execute(
            "INSERT INTO users(username, password_hash, created_at) VALUES ('sam@example.com', 'x', '2026-01-01')"
        )
        self.user_id = int(self.conn.execute("SELECT id FROM users").fetchone()["id"])
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def test_defaults_then_saved_choices_round_trip(self) -> None:
        prefs = get_search_prefs(self.conn, self.user_id)
        self.assertEqual(prefs["state"], "tx")
        self.assertEqual(prefs["scope"], "wells")
        saved = save_search_prefs(
            self.conn,
            self.user_id,
            {"state": "nm", "scope": "disposal", "mode": "api", "nope": "x"},
        )
        self.assertEqual(saved["state"], "nm")
        self.assertEqual(saved["scope"], "disposal")
        self.assertEqual(saved["mode"], "api")
        self.assertEqual(saved["pipe_mode"], "operator")
        again = get_search_prefs(self.conn, self.user_id)
        self.assertEqual(again["state"], "nm")
        rejected = save_search_prefs(self.conn, self.user_id, {"state": "zz"})
        self.assertEqual(rejected["state"], "nm")


if __name__ == "__main__":
    unittest.main()
