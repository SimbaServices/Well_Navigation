"""Texas-style well names for every state table."""

from __future__ import annotations

import sqlite3
import unittest

from wellnav.db import init_schema
from wellnav.states import wells_table
from wellnav.well_names import (
    META_KEY,
    normalize_stored_well_names,
    normalize_well_identity,
)


class NormalizeWellIdentityTests(unittest.TestCase):
    def test_oklahoma_lease_plus_hashed_number(self):
        self.assertEqual(
            normalize_well_identity("LOVINA", "#9-16-1H", ""),
            ("LOVINA #9-16-1H", "9-16-1H", "LOVINA"),
        )
        self.assertEqual(
            normalize_well_identity('LOVINA WALKER "A"', "#3", ""),
            ('LOVINA WALKER "A" #3', "3", 'LOVINA WALKER "A"'),
        )
        self.assertEqual(
            normalize_well_identity("LOVINA 7-26-12", "#1H", ""),
            ("LOVINA 7-26-12 #1H", "1H", "LOVINA 7-26-12"),
        )

    def test_idempotent(self):
        once = normalize_well_identity("LOVINA", "#9-16-1H", "")
        self.assertEqual(normalize_well_identity(*once), once)

    def test_texas_and_new_mexico_already_composed(self):
        self.assertEqual(
            normalize_well_identity("WILLIAMS #1", "1", "WILLIAMS"),
            ("WILLIAMS #1", "1", "WILLIAMS"),
        )
        self.assertEqual(
            normalize_well_identity("DUSTIN #001", "001", "DUSTIN"),
            ("DUSTIN #001", "001", "DUSTIN"),
        )
        self.assertEqual(
            normalize_well_identity("LRR", "2", "LRR"),
            ("LRR #2", "2", "LRR"),
        )

    def test_number_only_name_is_not_doubled(self):
        self.assertEqual(normalize_well_identity("1", "1", ""), ("1", "1", ""))
        self.assertEqual(normalize_well_identity("30L", "30L", ""), ("30L", "30L", ""))

    def test_space_separated_number_becomes_hash_form(self):
        self.assertEqual(
            normalize_well_identity("KORFF A 1-6", "1-6", "KORFF A"),
            ("KORFF A #1-6", "1-6", "KORFF A"),
        )
        self.assertEqual(
            normalize_well_identity("COMEGYS 25", "25", "COMEGYS 25"),
            ("COMEGYS #25", "25", "COMEGYS"),
        )

    def test_existing_hash_that_is_not_the_well_number_stays(self):
        self.assertEqual(
            normalize_well_identity("S.E. EUREKA (TUCKER #1)", "#21", ""),
            ("S.E. EUREKA (TUCKER #1)", "21", ""),
        )
        self.assertEqual(
            normalize_well_identity("KRAFT #1-13", "#1", ""),
            ("KRAFT #1-13", "1", ""),
        )
        self.assertEqual(
            normalize_well_identity("#1 BIG RED", "#1", ""),
            ("#1 BIG RED", "1", ""),
        )

    def test_louisiana_lease_backfill(self):
        self.assertEqual(
            normalize_well_identity("MARSHALL FEE #005", "005", ""),
            ("MARSHALL FEE #005", "005", "MARSHALL FEE"),
        )

    def test_short_number_is_not_taken_from_the_middle(self):
        self.assertEqual(
            normalize_well_identity("WILLIAMS #10", "1", "WILLIAMS"),
            ("WILLIAMS #10", "1", "WILLIAMS"),
        )


class StoredWellNameTests(unittest.TestCase):
    def test_migration_rewrites_oklahoma_and_leaves_texas_display(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        conn.execute("DELETE FROM meta WHERE key = ?", (META_KEY,))
        ok = wells_table("ok")
        tx = wells_table("tx")
        conn.execute(
            f"""
            INSERT INTO {ok}(
                api, api8, well_name, well_no, lease_name,
                first_seen_at, last_seen_at, updated_at
            ) VALUES ('3506920206', '06920206', 'LOVINA', '#9-16-1H', '', 't', 't', 't')
            """
        )
        conn.execute(
            f"""
            INSERT INTO {tx}(
                api, api8, well_name, well_no, lease_name,
                first_seen_at, last_seen_at, updated_at
            ) VALUES ('4200100001', '00100001', 'WILLIAMS #1', '1', 'WILLIAMS', 't', 't', 't')
            """
        )
        changed = normalize_stored_well_names(conn)
        self.assertEqual(changed, 1)
        ok_row = conn.execute(f"SELECT * FROM {ok}").fetchone()
        self.assertEqual(ok_row["well_name"], "LOVINA #9-16-1H")
        self.assertEqual(ok_row["well_no"], "9-16-1H")
        self.assertEqual(ok_row["lease_name"], "LOVINA")
        tx_row = conn.execute(f"SELECT * FROM {tx}").fetchone()
        self.assertEqual(tx_row["well_name"], "WILLIAMS #1")
        self.assertEqual(tx_row["lease_name"], "WILLIAMS")
        self.assertEqual(normalize_stored_well_names(conn), 0)


if __name__ == "__main__":
    unittest.main()
