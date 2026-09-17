from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wellnav.recordings import (
    append_chunk,
    can_record,
    clarity_project_id,
    contentsquare_tag_id,
    finish_recording,
    hotjar_site_id,
    list_recordings,
    recording_file,
    start_recording,
)


class RecordingHelperTests(unittest.TestCase):
    def test_only_simba_can_record(self) -> None:
        self.assertTrue(can_record({"email": "sam@simba.services"}))
        self.assertTrue(can_record({"email": "ops@mail.simba.services"}))
        self.assertFalse(can_record({"email": "pat@acme.test"}))
        self.assertFalse(can_record(None))

    def test_hotjar_id_must_be_digits(self) -> None:
        with patch.dict(os.environ, {"WELLNAV_HOTJAR_ID": "1234567", "HOTJAR_ID": ""}):
            self.assertEqual(hotjar_site_id(), "1234567")
        with patch.dict(os.environ, {"WELLNAV_HOTJAR_ID": "not-an-id", "HOTJAR_ID": ""}):
            self.assertEqual(hotjar_site_id(), "")

    def test_hotjar_id_reads_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("WELLNAV_HOTJAR_ID=7654321\n", encoding="utf-8")
            with (
                patch.dict(os.environ, {"WELLNAV_HOTJAR_ID": "", "HOTJAR_ID": ""}),
                patch("wellnav.recordings.ROOT", Path(tmp)),
            ):
                self.assertEqual(hotjar_site_id(), "7654321")

    def test_clarity_tag_from_env(self) -> None:
        with patch.dict(
            os.environ,
            {"WELLNAV_CLARITY_ID": "ebdac936045ea", "WELLNAV_HOTJAR_ID": "", "HOTJAR_ID": "", "CLARITY_ID": ""},
        ):
            self.assertEqual(clarity_project_id(), "ebdac936045ea")
            self.assertEqual(hotjar_site_id(), "")

    def test_contentsquare_tag_from_env(self) -> None:
        with patch.dict(os.environ, {"WELLNAV_CONTENTSQUARE_ID": "ebdac936045ea", "CONTENTSQUARE_ID": ""}):
            self.assertEqual(contentsquare_tag_id(), "ebdac936045ea")
        with patch.dict(os.environ, {"WELLNAV_CONTENTSQUARE_ID": "not-an-id", "CONTENTSQUARE_ID": ""}):
            self.assertEqual(contentsquare_tag_id(), "")

    def test_chunk_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WELLNAV_RECORDINGS_DIR": tmp}):
                user = {"id": 9, "email": "sam@simba.services"}
                meta = start_recording(user, "WellNavigation/1.0")
                append_chunk(meta["id"], user, b"abc")
                append_chunk(meta["id"], user, b"def")
                done = finish_recording(meta["id"], user)
                self.assertEqual(done["bytes"], 8)
                more = append_chunk(meta["id"], user, b"ghi")
                self.assertEqual(more["bytes"], 12)
                self.assertFalse(more.get("finished_at"))
                found = recording_file(meta["id"])
                self.assertIsNotNone(found)
                path, _ = found
                self.assertTrue(path.name.endswith(".jsonl"))
                self.assertEqual(path.read_bytes(), b"abc\ndef\nghi\n")
                rows = list_recordings()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["email"], "sam@simba.services")
                self.assertEqual(rows[0].get("kind"), "session")

    def test_rejects_other_user_and_bad_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WELLNAV_RECORDINGS_DIR": tmp}):
                owner = {"id": 1, "email": "sam@simba.services"}
                other = {"id": 2, "email": "ops@simba.services"}
                meta = start_recording(owner)
                with self.assertRaises(ValueError):
                    append_chunk(meta["id"], other, b"x")
                with self.assertRaises(ValueError):
                    append_chunk("../etc/passwd", owner, b"x")
                with self.assertRaises(PermissionError):
                    start_recording({"id": 3, "email": "pat@acme.test"})


class RecordingRouteTests(unittest.TestCase):
    def test_hotjar_stays_off_for_store_clients(self) -> None:
        from app import templates

        html = templates.get_template("partials/hotjar.html").render(
            hotjar_id="1234567",
            clarity_id="ebdac936045ea",
            contentsquare_id="ebdac936045ea",
            store_client=True,
        )
        self.assertNotIn("static.hotjar.com", html)
        self.assertNotIn("clarity.ms", html)
        self.assertNotIn("contentsquare.net", html)
        web = templates.get_template("partials/hotjar.html").render(
            hotjar_id="1234567",
            clarity_id="ebdac936045ea",
            contentsquare_id="ebdac936045ea",
            store_client=False,
        )
        self.assertIn("static.hotjar.com", web)
        self.assertIn("1234567", web)
        self.assertIn("clarity.ms/tag/", web)
        self.assertIn("t.contentsquare.net/uxa/ebdac936045ea.js", web)
        self.assertIn("ebdac936045ea", web)

    def test_workspace_auto_records_without_a_button(self) -> None:
        root = Path(__file__).resolve().parents[1]
        index = (root / "templates" / "index.html").read_text(encoding="utf-8")
        capture = (root / "templates" / "partials" / "ux_capture.html").read_text(encoding="utf-8")
        js = (root / "static" / "js" / "ux-record.js").read_text(encoding="utf-8")
        self.assertNotIn("Record UX", index)
        self.assertNotIn("data-ux-start", index)
        self.assertIn("ux_capture.html", index)
        self.assertIn("ux-record.js", capture)
        self.assertIn("rrweb.record", js)
        self.assertNotIn("getDisplayMedia", js)


if __name__ == "__main__":
    unittest.main()
