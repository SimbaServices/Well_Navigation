"""Organization testing-feedback board."""

from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from starlette.templating import Jinja2Templates

from wellnav.accounts import UserStore
from wellnav.db import init_schema
from wellnav.feedback import FEEDBACK, LIST_LIMIT


class FeedbackBoardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.conn.commit()
        self.patcher = patch("wellnav.accounts._conn", lambda: self.conn)
        self.patcher.start()
        self.users = UserStore()
        self.admin, error = self.users.register("lead@acme.test", "password12")
        self.assertIsNone(error)
        self.member, error = self.users.register("tech@acme.test", "password12")
        self.assertIsNone(error)
        self.other, error = self.users.register("sam@other.test", "password12")
        self.assertIsNone(error)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.conn.close()

    def test_notes_stay_inside_the_organization(self) -> None:
        note, error = FEEDBACK.post(
            self.member,
            kind="bug",
            place="Map",
            body="Pipeline overlay did not redraw after search.",
        )
        self.assertIsNone(error)
        self.assertEqual(note["kind_label"], "Bug")
        self.assertTrue(note["mine"])

        mine, truncated = FEEDBACK.list_for(self.admin)
        self.assertFalse(truncated)
        self.assertEqual([item["id"] for item in mine], [note["id"]])
        self.assertFalse(mine[0]["mine"])
        self.assertTrue(mine[0]["can_remove"])

        theirs, _ = FEEDBACK.list_for(self.other)
        self.assertEqual(theirs, [])
        ok, error = FEEDBACK.delete(self.other, note["id"])
        self.assertFalse(ok)
        self.assertIn("organization", error or "")
        still, _ = FEEDBACK.list_for(self.member)
        self.assertEqual(len(still), 1)

    def test_author_and_admin_can_remove_a_note(self) -> None:
        note, error = FEEDBACK.post(self.member, kind="idea", body="Pin the last search on the map.")
        self.assertIsNone(error)
        ok, error = FEEDBACK.delete(self.admin, note["id"])
        self.assertTrue(ok)
        self.assertIsNone(error)
        empty, _ = FEEDBACK.list_for(self.member)
        self.assertEqual(empty, [])

        again, error = FEEDBACK.post(self.admin, kind="question", body="Does cache survive a reload?")
        self.assertIsNone(error)
        ok, error = FEEDBACK.delete(self.member, again["id"])
        self.assertFalse(ok)
        ok, error = FEEDBACK.delete(self.admin, again["id"])
        self.assertTrue(ok)

    def test_rejects_empty_long_and_unknown_notes(self) -> None:
        _, error = FEEDBACK.post(self.member, kind="bug", body="   ")
        self.assertEqual(error, "Write what you found.")
        _, error = FEEDBACK.post(self.member, kind="nope", body="Something broke.")
        self.assertIn("Pick", error or "")
        _, error = FEEDBACK.post(self.member, kind="note", body="x" * 2001)
        self.assertIn("2000", error or "")
        _, error = FEEDBACK.post(self.member, kind="note", place="m" * 81, body="Map label.")
        self.assertIn("80", error or "")
        lone = dict(self.member)
        lone["org_id"] = None
        _, error = FEEDBACK.post(lone, kind="note", body="Hello")
        self.assertIn("organization", error or "")

    def test_list_keeps_the_latest_hundred(self) -> None:
        for index in range(LIST_LIMIT + 3):
            _, error = FEEDBACK.post(self.member, kind="note", body=f"Note {index}")
            self.assertIsNone(error)
        notes, truncated = FEEDBACK.list_for(self.member)
        self.assertTrue(truncated)
        self.assertEqual(len(notes), LIST_LIMIT)
        self.assertEqual(notes[0]["body"], f"Note {LIST_LIMIT + 2}")

    def test_template_escapes_note_text(self) -> None:
        templates = Jinja2Templates(directory="templates")
        html = templates.get_template("partials/feedback.html").render(
            {
                "org": {"name": "Acme"},
                "workspace": {"org_name": "Acme"},
                "user": self.member,
                "messages": [
                    {
                        "id": 1,
                        "author": "tech@acme.test",
                        "kind_label": "Bug",
                        "place": "<map>",
                        "body": "<script>alert(1)</script>",
                        "created_at": "2026-09-25 16:00 UTC",
                        "mine": True,
                        "can_remove": True,
                    }
                ],
                "truncated": False,
                "kinds": {"bug": "Bug"},
                "error": None,
                "draft": {},
            }
        )
        self.assertIn("Testing feedback", html)
        self.assertIn("Acme", html)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", html)
        self.assertNotIn("<script>alert", html)
        self.assertIn("/feedback/1/delete", html)


class FeedbackRouteTests(unittest.TestCase):
    def test_feedback_requires_sign_in(self) -> None:
        from starlette.testclient import TestClient

        from app import app

        client = TestClient(app, follow_redirects=False)
        page = client.get("/feedback")
        self.assertEqual(page.status_code, 303)
        self.assertTrue(page.headers["location"].startswith("/login"))
        posted = client.post("/feedback", data={"kind": "bug", "body": "Broken search"})
        self.assertEqual(posted.status_code, 303)
        self.assertTrue(posted.headers["location"].startswith("/login"))

    def test_signed_in_org_can_post_and_only_that_org_sees_it(self) -> None:
        from starlette.testclient import TestClient

        from app import app

        conn = sqlite3.connect(":memory:", check_same_thread=False)
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        conn.commit()
        users = UserStore()
        with patch("wellnav.accounts._conn", lambda: conn):
            users.register("lead@acme.test", "password12")
            users.register("sam@other.test", "password12")
            acme = TestClient(app, follow_redirects=False)
            other = TestClient(app, follow_redirects=False)
            signed_in = acme.post(
                "/login",
                data={"email": "lead@acme.test", "password": "password12", "next": "/feedback"},
            )
            self.assertEqual(signed_in.status_code, 303)
            board = acme.get("/feedback")
            self.assertEqual(board.status_code, 200)
            self.assertIn("Testing feedback", board.text)
            self.assertIn(">Feedback<", board.text)
            self.assertIn("Acme", board.text)
            empty = acme.post("/feedback", data={"kind": "bug", "body": "   "})
            self.assertEqual(empty.status_code, 200)
            self.assertIn("Write what you found", empty.text)
            posted = acme.post(
                "/feedback",
                data={
                    "kind": "bug",
                    "place": "Search",
                    "body": "Filter chips overlap on a phone.",
                },
            )
            self.assertEqual(posted.status_code, 200)
            self.assertIn("Filter chips overlap on a phone.", posted.text)
            self.assertIn("Search", posted.text)
            self.assertIn("lead@acme.test", posted.text)
            other.post(
                "/login",
                data={"email": "sam@other.test", "password": "password12"},
            )
            hidden = other.get("/feedback")
            self.assertEqual(hidden.status_code, 200)
            self.assertNotIn("Filter chips overlap", hidden.text)
        conn.close()
