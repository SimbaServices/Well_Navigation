"""Two workers must not both add the same column at startup."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from threading import Barrier, Thread
from unittest.mock import patch

from wellnav.db import SCHEMA_VERSION, ensure_column, init_schema


class SchemaStartupTests(unittest.TestCase):
    def test_second_init_skips_a_finished_migration(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        with patch("wellnav.db._init_schema_locked") as body:
            init_schema(conn)
        body.assert_not_called()
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        self.assertEqual(int(version), SCHEMA_VERSION)
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("last_opened_at", cols)

    def test_duplicate_column_from_the_other_worker_is_ignored(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE users (id INTEGER, last_opened_at TEXT)")
        with patch(
            "wellnav.db._column_names",
            side_effect=[set(), {"last_opened_at"}],
        ):
            ensure_column(conn, "users", "last_opened_at", "TEXT")
        cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        self.assertIn("last_opened_at", cols)

    def test_two_threads_migrate_one_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "wellnav.db"
            errors: list[BaseException] = []
            gate = Barrier(2)

            def migrate() -> None:
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                try:
                    gate.wait(timeout=10)
                    init_schema(conn)
                    conn.commit()
                except BaseException as exc:
                    errors.append(exc)
                finally:
                    conn.close()

            threads = [Thread(target=migrate) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
                self.assertFalse(thread.is_alive())

            self.assertEqual(errors, [])
            conn = sqlite3.connect(path)
            try:
                cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
                self.assertIn("last_opened_at", cols)
                version = conn.execute(
                    "SELECT value FROM meta WHERE key = 'schema_version'"
                ).fetchone()[0]
                self.assertEqual(int(version), SCHEMA_VERSION)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
