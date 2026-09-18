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
    session_playable,
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

    def test_marks_session_playable_when_snapshot_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WELLNAV_RECORDINGS_DIR": tmp}):
                user = {"id": 9, "email": "sam@simba.services"}
                meta = start_recording(user)
                path, _ = recording_file(meta["id"])
                self.assertFalse(session_playable(path))
                payload = (
                    b'[{"type":4,"data":{"href":"/","width":800,"height":600},"timestamp":1},'
                    b'{"type":2,"data":{"node":{"id":1,"type":0,"childNodes":[]},"initialOffset":{"top":0,"left":0}},"timestamp":2}]'
                )
                append_chunk(meta["id"], user, payload)
                path, info = recording_file(meta["id"])
                self.assertTrue(session_playable(path))
                self.assertTrue(info["playable"])
                self.assertTrue(list_recordings()[0]["playable"])

    def test_local_player_points_at_chosen_folder(self) -> None:
        from wellnav.replay import configure_watch_dir

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"WELLNAV_RECORDINGS_DIR": ""}):
                path = configure_watch_dir(tmp)
                self.assertEqual(path, Path(tmp).resolve())
                self.assertEqual(os.environ["WELLNAV_RECORDINGS_DIR"], str(path))

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

    def test_store_clients_do_not_see_ux_recordings(self) -> None:
        from app import templates

        html = templates.get_template("partials/account.html").render(
            error=None,
            user={"email": "appreview@simba.services", "is_admin": True, "last_login_at": "", "last_activity_at": ""},
            org={"name": "Simba Services"},
            workspace={"org_name": "Simba Services", "complimentary": True},
            saved_count=0,
            cache_stats=None,
            can_record_ux=False,
            store_client=True,
            recordings=[{"id": "abc", "started_at": "now", "email": "appreview@simba.services", "bytes": 0, "playable": False}],
        )
        self.assertNotIn("UX recordings", html)
        self.assertNotIn("pull-recordings", html)
        self.assertIn("Delete account", html)

        web = templates.get_template("partials/account.html").render(
            error=None,
            user={"email": "sam@simba.services", "is_admin": True, "last_login_at": "", "last_activity_at": ""},
            org={"name": "Simba Services"},
            workspace={"org_name": "Simba Services", "complimentary": True},
            saved_count=0,
            cache_stats=None,
            can_record_ux=True,
            store_client=False,
            recordings=[],
        )
        self.assertIn("UX recordings", web)

    def test_workspace_auto_records_without_a_button(self) -> None:
        root = Path(__file__).resolve().parents[1]
        index = (root / "templates" / "index.html").read_text(encoding="utf-8")
        capture = (root / "templates" / "partials" / "ux_capture.html").read_text(encoding="utf-8")
        js = (root / "static" / "js" / "ux-record.js").read_text(encoding="utf-8")
        self.assertNotIn("Record UX", index)
        self.assertNotIn("data-ux-start", index)
        self.assertIn("ux_capture.html", index)
        self.assertIn("ux-record.js", capture)
        replay = (root / "static" / "js" / "ux-replay.js").read_text(encoding="utf-8")
        self.assertIn("rrweb.record", js)
        self.assertIn("inlineStylesheet", js)
        self.assertIn("takeFullSnapshot", js)
        self.assertIn("/finish", js)
        self.assertNotIn("getDisplayMedia", js)
        self.assertIn("wellnavStartReplay", replay)
        self.assertIn("replayer-mouse-tail", (root / "templates" / "ux_replay.html").read_text(encoding="utf-8"))
        self.assertIn("ux-replay.js", (root / "templates" / "ux_replay.html").read_text(encoding="utf-8"))
        self.assertNotIn("Replay file", (root / "templates" / "partials" / "ux_recordings.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
