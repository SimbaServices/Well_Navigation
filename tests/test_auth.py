from __future__ import annotations

import re
import sqlite3
import unittest
from unittest.mock import patch

from wellnav.accounts import UserStore
from wellnav.auth import (
    is_public_path,
    mask_phone,
    normalize_phone,
    safe_next,
    session_matches,
    validate_phone,
)
from wellnav.db import init_schema
from wellnav.phone_auth import PhoneAuth
from wellnav.sms import _smtp_usernames, set_sender, sms_gateway_addresses


def _code(body: str) -> str:
    match = re.search(r"code: (\d{6})", body)
    if not match:
        raise AssertionError(f"no code in {body!r}")
    return match.group(1)


class PhoneHelperTests(unittest.TestCase):
    def test_normalize_us_numbers(self) -> None:
        self.assertEqual(normalize_phone("(432) 555-0100"), "+14325550100")
        self.assertEqual(normalize_phone("4325550100"), "+14325550100")
        self.assertEqual(normalize_phone("1-432-555-0100"), "+14325550100")
        self.assertEqual(normalize_phone("+14325550100"), "+14325550100")

    def test_reject_short_number(self) -> None:
        self.assertIsNone(normalize_phone("5550100"))
        phone, error = validate_phone("555")
        self.assertIsNone(phone)
        self.assertIsNotNone(error)

    def test_mask_and_paths(self) -> None:
        self.assertEqual(mask_phone("+14325550100"), "the number ending in 0100")
        self.assertTrue(is_public_path("/login"))
        self.assertTrue(is_public_path("/register/verify"))
        self.assertTrue(is_public_path("/privacy"))
        self.assertTrue(is_public_path("/terms"))
        self.assertTrue(is_public_path("/static/css/app.css"))
        self.assertTrue(is_public_path("/sw.js"))
        self.assertTrue(is_public_path("/manifest.webmanifest"))
        self.assertTrue(is_public_path("/billing/webhook"))
        self.assertTrue(is_public_path("/billing/success"))
        self.assertFalse(is_public_path("/billing"))
        self.assertFalse(is_public_path("/offline/tiles/12/1/1"))
        self.assertFalse(is_public_path("/"))
        self.assertFalse(is_public_path("/search"))
        self.assertFalse(is_public_path("/account/delete"))
        self.assertEqual(safe_next("/well/00300290"), "/well/00300290")
        self.assertEqual(safe_next("https://evil.example"), "/")
        self.assertEqual(safe_next("//evil.example"), "/")

    def test_sms_gateway_addresses(self) -> None:
        addresses = sms_gateway_addresses("+14325550100")
        self.assertTrue(all(item.startswith("4325550100@") for item in addresses))
        self.assertIn("4325550100@vtext.com", addresses)
        self.assertIn("4325550100@tmomail.net", addresses)
        with self.assertRaises(ValueError):
            sms_gateway_addresses("5550100")
        self.assertEqual(
            _smtp_usernames("wellnav@simba.services"),
            ["wellnav@simba.services", "wellnav"],
        )
        self.assertEqual(_smtp_usernames("wellnav"), ["wellnav"])


class PhoneAuthFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.conn.commit()
        self.messages: list[tuple[str, str]] = []
        set_sender(lambda phone, body: self.messages.append((phone, body)))
        self.patcher = patch("wellnav.accounts._conn", lambda: self.conn)
        self.patcher.start()
        self.users = UserStore()
        self.auth = PhoneAuth(self.users)

    def tearDown(self) -> None:
        self.patcher.stop()
        set_sender(None)
        self.conn.close()

    def last_code(self) -> str:
        return _code(self.messages[-1][1])

    def test_signup_does_not_create_user_until_code(self) -> None:
        challenge, error = self.auth.start_signup("sam@example.com", "password12")
        self.assertIsNone(error)
        self.assertIsNotNone(challenge)
        self.assertEqual(len(self.messages), 1)
        self.assertEqual(self.messages[0][0], "sam@example.com")
        self.assertIsNone(self.users.by_username("sam@example.com"))
        user, error = self.auth.verify(challenge["id"], self.last_code())
        self.assertIsNone(error)
        self.assertEqual(user["username"], "sam@example.com")
        self.assertEqual(user["email"], "sam@example.com")

    def test_wrong_and_expired_codes(self) -> None:
        challenge, error = self.auth.start_signup("sam@example.com", "password12")
        self.assertIsNone(error)
        user, error = self.auth.verify(challenge["id"], "000000")
        self.assertIsNone(user)
        self.assertEqual(error, "That code is incorrect.")
        self.conn.execute(
            "UPDATE otp_challenges SET expires_at = '2000-01-01T00:00:00' WHERE id = ?",
            (challenge["id"],),
        )
        self.conn.commit()
        user, error = self.auth.verify(challenge["id"], self.last_code())
        self.assertIsNone(user)
        self.assertIn("expired", error or "")

    def test_login_is_password_only(self) -> None:
        created, error = self.users.register("sam@example.com", "password12")
        self.assertIsNone(error)
        self.assertIsNotNone(created)
        self.messages.clear()
        user, error = self.auth.start_login("sam@example.com", "password12")
        self.assertIsNone(error)
        self.assertEqual(user["id"], created["id"])
        self.assertEqual(len(self.messages), 0)

    def test_login_rejects_bad_password(self) -> None:
        self.users.register("sam@example.com", "password12")
        user, error = self.auth.start_login("sam@example.com", "nope-nope")
        self.assertIsNone(user)
        self.assertEqual(error, "Email or password is incorrect.")

    def test_duplicate_email(self) -> None:
        self.users.register("sam@example.com", "password12")
        _, error = self.auth.start_signup("sam@example.com", "password99")
        self.assertEqual(error, "That email is already registered.")


class GateTests(unittest.TestCase):
    def test_home_redirects_to_login(self) -> None:
        from starlette.testclient import TestClient

        from app import app

        client = TestClient(app, follow_redirects=False)
        home = client.get("/")
        self.assertEqual(home.status_code, 303)
        self.assertTrue(home.headers["location"].startswith("/login"))
        health = client.get("/healthz")
        self.assertEqual(health.status_code, 200)
        login = client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("Sign in", login.text)
        self.assertIn("/static/vendor/htmx.min.js", login.text)
        self.assertNotIn("unpkg.com", login.text)
        self.assertNotIn("id=\"search-form\"", login.text)
        self.assertIn("work email", login.text)
        privacy = client.get("/privacy")
        self.assertEqual(privacy.status_code, 200)
        self.assertIn("Account deletion", privacy.text)
        self.assertIn("Privacy", privacy.text)
        terms = client.get("/terms")
        self.assertEqual(terms.status_code, 200)
        self.assertIn("Terms and conditions", terms.text)
        self.assertIn("811", terms.text)


class AccountDeletionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.conn.commit()
        self.patcher = patch("wellnav.accounts._conn", lambda: self.conn)
        self.patcher.start()
        self.users = UserStore()

    def tearDown(self) -> None:
        self.patcher.stop()
        self.conn.close()

    def test_delete_account_removes_user_and_saved_wells(self) -> None:
        user, error = self.users.register("ops@example.com", "password12")
        self.assertIsNone(error)
        other, error = self.users.register("keep@other.test", "password12")
        self.assertIsNone(error)
        self.conn.execute(
            """
            INSERT INTO saved_wells(user_id, state, api8, well_name, saved_at)
            VALUES (?, 'tx', '00300290', 'TEST', '2026-01-01T00:00:00')
            """,
            (user["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO saved_wells(user_id, state, api8, well_name, saved_at)
            VALUES (?, 'tx', '00300291', 'KEEP', '2026-01-01T00:00:00')
            """,
            (other["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO user_cache(
                user_id, kind, cache_key, payload, hits, bytes,
                created_at, last_hit_at, expires_at
            ) VALUES (?, 'search', 'q', '{}', 0, 2, '2026-01-01', '2026-01-01', '2099-01-01')
            """,
            (user["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO otp_challenges(
                id, purpose, pending_id, user_id, phone, code_hash,
                expires_at, attempts, sent_at, created_at
            ) VALUES ('c1', 'recovery', NULL, ?, ?, 'hash', '2099-01-01', 0, '2026-01-01', '2026-01-01')
            """,
            (user["id"], "ops@example.com"),
        )
        self.conn.execute(
            """
            INSERT INTO pending_signups(id, username, password_hash, phone, created_at)
            VALUES ('p1', 'ops@example.com', 'hash', 'ops@example.com', '2026-01-01')
            """
        )
        self.conn.execute(
            "UPDATE organizations SET billing_email = ?, name = ? WHERE id = ?",
            ("ops@example.com", "ops@example.com", user["org_id"]),
        )
        self.conn.commit()
        catalog_before = self.conn.execute(
            "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name LIKE 'wells_%'"
        ).fetchone()["n"]

        ok, error = self.users.delete_account(user, "wrong-pass")
        self.assertFalse(ok)
        self.assertIn("incorrect", error or "")

        with patch("wellnav.recordings.delete_recordings_for_user") as scrub:
            ok, error = self.users.delete_account(user, "password12")
            scrub.assert_called_once_with(user["id"])
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertIsNone(self.users.by_username("ops@example.com"))
        leftover = self.conn.execute(
            "SELECT COUNT(*) AS n FROM saved_wells WHERE user_id = ?",
            (user["id"],),
        ).fetchone()["n"]
        self.assertEqual(leftover, 0)
        kept = self.conn.execute(
            "SELECT api8 FROM saved_wells WHERE user_id = ?",
            (other["id"],),
        ).fetchone()
        self.assertEqual(kept["api8"], "00300291")
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM user_cache WHERE user_id = ?",
                (user["id"],),
            ).fetchone()["n"],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM otp_challenges WHERE user_id = ? OR phone = ?",
                (user["id"], "ops@example.com"),
            ).fetchone()["n"],
            0,
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM pending_signups WHERE username = ?",
                ("ops@example.com",),
            ).fetchone()["n"],
            0,
        )
        org = self.conn.execute(
            "SELECT name, billing_email, stripe_customer_id FROM organizations WHERE id = ?",
            (user["org_id"],),
        ).fetchone()
        self.assertIsNotNone(org)
        self.assertEqual(org["name"], "")
        self.assertIsNone(org["billing_email"])
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) AS n FROM sqlite_master WHERE type = 'table' AND name LIKE 'wells_%'"
            ).fetchone()["n"],
            catalog_before,
        )
        self.assertIsNotNone(self.users.by_username("keep@other.test"))

    def test_delete_account_keeps_shared_org_and_other_member_wells(self) -> None:
        admin, error = self.users.register("admin@acme.test", "password12")
        self.assertIsNone(error)
        member, error = self.users.register("member@acme.test", "password12")
        self.assertIsNone(error)
        self.assertEqual(admin["org_id"], member["org_id"])
        self.conn.execute(
            "UPDATE organizations SET billing_email = ?, stripe_customer_id = ? WHERE id = ?",
            ("admin@acme.test", "cus_shared", admin["org_id"]),
        )
        self.conn.execute(
            """
            INSERT INTO saved_wells(user_id, state, api8, well_name, saved_at)
            VALUES (?, 'tx', '11111111', 'ADMIN', '2026-01-01T00:00:00')
            """,
            (admin["id"],),
        )
        self.conn.execute(
            """
            INSERT INTO saved_wells(user_id, state, api8, well_name, saved_at)
            VALUES (?, 'tx', '22222222', 'MEMBER', '2026-01-01T00:00:00')
            """,
            (member["id"],),
        )
        self.conn.commit()
        with patch("wellnav.recordings.delete_recordings_for_user"):
            ok, error = self.users.delete_account(admin, "password12")
        self.assertTrue(ok)
        self.assertIsNone(error)
        org = self.conn.execute(
            "SELECT name, billing_email, stripe_customer_id FROM organizations WHERE id = ?",
            (admin["org_id"],),
        ).fetchone()
        self.assertIsNotNone(org)
        self.assertEqual(org["name"], "acme.test")
        self.assertIsNone(org["billing_email"])
        self.assertEqual(org["stripe_customer_id"], "cus_shared")
        kept = self.conn.execute(
            "SELECT api8 FROM saved_wells WHERE user_id = ?",
            (member["id"],),
        ).fetchone()
        self.assertEqual(kept["api8"], "22222222")
        self.assertIsNotNone(self.users.by_username("member@acme.test"))


class RecordingCleanupTests(unittest.TestCase):
    def test_delete_recordings_for_user_removes_only_that_user(self) -> None:
        import json
        import tempfile
        from pathlib import Path

        from wellnav.recordings import delete_recordings_for_user

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for rid, uid in (("aaaaaaaaaaaaaaaa", 1), ("bbbbbbbbbbbbbbbb", 2)):
                (folder / f"{rid}.json").write_text(
                    json.dumps({"id": rid, "user_id": uid, "email": f"u{uid}@x.test"}),
                    encoding="utf-8",
                )
                (folder / f"{rid}.jsonl").write_text("{}\n", encoding="utf-8")
            with patch("wellnav.recordings.recordings_dir", lambda: folder):
                removed = delete_recordings_for_user(1)
            self.assertEqual(removed, 1)
            self.assertFalse((folder / "aaaaaaaaaaaaaaaa.json").exists())
            self.assertFalse((folder / "aaaaaaaaaaaaaaaa.jsonl").exists())
            self.assertTrue((folder / "bbbbbbbbbbbbbbbb.json").exists())
            self.assertTrue((folder / "bbbbbbbbbbbbbbbb.jsonl").exists())


class SingleSessionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        init_schema(self.conn)
        self.conn.commit()
        self.patcher = patch("wellnav.accounts._conn", lambda: self.conn)
        self.patcher.start()
        self.users = UserStore()
        self.user, error = self.users.register("pat@acme.test", "password12")
        self.assertIsNone(error)

    def tearDown(self) -> None:
        self.patcher.stop()
        self.conn.close()

    def _sign_in(self, client):
        return client.post(
            "/login",
            data={"email": "pat@acme.test", "password": "password12"},
        )

    def test_rotate_and_cookie_must_match(self) -> None:
        first = self.users.rotate_session_version(self.user["id"])
        second = self.users.rotate_session_version(self.user["id"])
        self.assertEqual(first, 1)
        self.assertEqual(second, 2)
        live = self.users.get(self.user["id"])
        self.assertEqual(live["session_version"], 2)
        fake_ok = type("R", (), {"session": {"sv": 2}})()
        fake_old = type("R", (), {"session": {"sv": 1}})()
        fake_missing = type("R", (), {"session": {}})()
        self.assertTrue(session_matches(fake_ok, live))
        self.assertFalse(session_matches(fake_old, live))
        self.assertFalse(session_matches(fake_missing, live))

    def test_second_login_signs_out_the_first_device(self) -> None:
        from starlette.testclient import TestClient

        from app import app

        first = TestClient(app, follow_redirects=False)
        second = TestClient(app, follow_redirects=False)
        self.assertEqual(self._sign_in(first).status_code, 303)
        opened = first.get("/")
        self.assertTrue(
            opened.status_code == 200
            or (opened.status_code == 303 and not opened.headers["location"].startswith("/login"))
        )
        self.assertEqual(self._sign_in(second).status_code, 303)
        stale = first.get("/")
        self.assertEqual(stale.status_code, 303)
        self.assertTrue(stale.headers["location"].startswith("/login"))
        still = second.get("/")
        self.assertTrue(
            still.status_code == 200
            or (still.status_code == 303 and not still.headers["location"].startswith("/login"))
        )
