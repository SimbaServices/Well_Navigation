import unittest

from wellnav.cache_view import describe_cache_entries, history_stats


class CacheViewTests(unittest.TestCase):
    def test_search_key_reads_as_a_well_name(self) -> None:
        shown = describe_cache_entries(
            [
                {
                    "id": 4,
                    "kind": "search",
                    "cache_key": "tx|name|university||||0|50|",
                    "hits": 0,
                    "bytes": 31902,
                    "last_hit_at": "2026-10-01T17:18:34+00:00",
                    "expires_at": "2026-10-01T17:45:18+00:00",
                }
            ]
        )[0]
        self.assertEqual(shown["kind_label"], "Search")
        self.assertEqual(shown["title"], "university")
        self.assertEqual(shown["context"], "Texas · Well name")
        self.assertEqual(shown["meta"], "31.2 KB · Oct 1, 17:18 · expires 17:45")

    def test_recent_and_location_are_named(self) -> None:
        shown = describe_cache_entries(
            [
                {
                    "id": 1,
                    "kind": "recent",
                    "cache_key": "searches",
                    "hits": 2,
                    "bytes": 2003,
                    "last_hit_at": "2026-10-01T17:18:33+00:00",
                    "expires_at": "2026-10-08T17:18:33+00:00",
                },
                {
                    "id": 2,
                    "kind": "location",
                    "cache_key": "00300290",
                    "hits": 0,
                    "bytes": 512,
                    "last_hit_at": "2026-10-01T12:00:00+00:00",
                    "expires_at": "2026-10-01T18:00:00+00:00",
                },
            ]
        )
        self.assertEqual(shown[0]["title"], "Recent searches")
        self.assertIn("2 hits", shown[0]["meta"])
        self.assertIn("Oct 8, 17:18", shown[0]["meta"])
        self.assertEqual(shown[1]["title"], "003-00290")
        self.assertEqual(shown[1]["context"], "Wellhead")

    def test_stats_use_a_short_size(self) -> None:
        self.assertEqual(history_stats({"entries": 8, "hits": 0, "bytes": 154214})["size_label"], "150.6 KB")
