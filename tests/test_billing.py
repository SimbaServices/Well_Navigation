from __future__ import annotations

import os
import sqlite3
import unittest
from unittest.mock import patch

from wellnav.accounts import UserStore
from wellnav.auth import is_public_path
from wellnav.billing import (
    apply_subscription,
    clamp_seats,
    complimentary_email,
    create_checkout_session,
    handle_webhook,
    is_billing_path,
    is_store_client,
    reset_price_cache,
    seat_price_label,
    seated_ids,
    workspace_for,
)
from starlette.testclient import TestClient

from app import app
from wellnav.db import init_schema


class BillingHelperTests(unittest.TestCase):
    def test_simba_accounts_are_complimentary(self) -> None:
        self.assertTrue(complimentary_email("sam@simba.services"))
        self.assertTrue(complimentary_email("ops@mail.simba.services"))
        self.assertFalse(complimentary_email("sam@example.com"))
        self.assertFalse(complimentary_email("simba.services@gmail.com"))

    def test_admins_take_seats_first(self) -> None:
        members = [
            {"id": 1, "is_admin": False, "created_at": "2026-01-01"},
            {"id": 2, "is_admin": True, "created_at": "2026-06-01"},
            {"id": 3, "is_admin": False, "created_at": "2026-02-01"},
        ]
        self.assertEqual(seated_ids(members, 1, complimentary=False), {2})
        self.assertEqual(seated_ids(members, 2, complimentary=False), {2, 1})
        self.assertEqual(seated_ids(members, 9, complimentary=True), {1, 2, 3})

    def test_price_is_fifteen_per_person(self) -> None:
        env = {
            "WELLNAV_STRIPE_SECRET": "",
            "STRIPE_SECRET_KEY": "",
            "WELLNAV_STRIPE_PRICE_ID": "",
            "STRIPE_PRICE_ID": "",
            "WELLNAV_SEAT_PRICE_CENTS": "",
        }
        with patch.dict(os.environ, env, clear=False):
            reset_price_cache()
            self.assertEqual(seat_price_label(), "$15 / person / month")

    def test_billing_template_shows_price_and_success(self) -> None:
        from app import templates

        workspace = {
            "org": {"domain": "acme.test"},
            "org_name": "Acme",
            "can_manage": True,
            "paid": False,
            "complimentary": False,
            "reason": "unpaid",
            "stripe_ready": True,
            "suggested_seats": 1,
            "price_label": seat_price_label(),
            "allowed": False,
        }
        unpaid = templates.get_template("partials/billing.html").render(
            workspace=workspace, success=False
        )
        self.assertIn("$15 / person / month", unpaid)
        self.assertIn("Workspace billing", unpaid)
        self.assertIn("Continue to checkout", unpaid)
        self.assertNotIn("You're set", unpaid)
        store = templates.get_template("partials/billing.html").render(
            workspace=workspace, success=False, store_client=True
        )
        self.assertIn("does not sell subscriptions", store)
        self.assertIn("external_browser=1", store)
        self.assertNotIn("Continue to checkout", store)
        self.assertNotIn("$15 / person / month", store)

        paid = dict(workspace)
        paid.update(
            {
                "allowed": True,
                "paid": True,
                "seat_count": 1,
                "seated_count": 1,
                "member_count": 1,
                "status": "active",
            }
        )
        success = templates.get_template("partials/billing.html").render(
            workspace=paid, success=True
        )
        self.assertIn("You're set", success)
        self.assertIn("Seats are active", success)

    def test_clamp_and_paths(self) -> None:
        self.assertEqual(clamp_seats("4"), 4)
        self.assertEqual(clamp_seats("0"), 1)
        self.assertEqual(clamp_seats("999"), 200)
        self.assertTrue(is_billing_path("/billing"))
        self.assertTrue(is_billing_path("/org/members/3/role"))
        self.assertTrue(is_billing_path("/account/delete"))
        self.assertTrue(is_billing_path("/ux/recordings"))
        self.assertFalse(is_billing_path("/search"))
        self.assertTrue(is_public_path("/billing/webhook"))
        self.assertTrue(is_public_path("/billing/success"))
        self.assertFalse(is_public_path("/billing"))
        self.assertTrue(is_store_client("Mozilla/5.0 WellNavigation/1.0 (iOS; store)"))
        self.assertTrue(is_store_client("Mozilla/5.0 WellNavigation/1.0 (Android; store)"))
        self.assertFalse(is_store_client("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Safari/604.1"))
        self.assertFalse(is_store_client("WellNavigation/1.0 (iOS)"))


class BillingAccessTests(unittest.TestCase):
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

    def test_simba_org_is_complimentary(self) -> None:
        user, error = self.users.register("sam@simba.services", "password12")
        self.assertIsNone(error)
        org = self.users.org(user["org_id"])
        self.assertEqual(org["billing_status"], "complimentary")
        self.assertEqual(org["name"], "Simba Services")
        workspace = workspace_for(user, org, self.users.org_members(user["org_id"]))
        self.assertTrue(workspace["complimentary"])
        self.assertTrue(workspace["allowed"])
        self.assertEqual(workspace["reason"], "complimentary")

    def test_company_org_starts_unpaid(self) -> None:
        user, error = self.users.register("pat@acme.test", "password12")
        self.assertIsNone(error)
        org = self.users.org(user["org_id"])
        self.assertEqual(org["billing_status"], "none")
        self.assertEqual(org["seat_count"], 0)

    @patch("wellnav.billing.billing_enforced", lambda: True)
    def test_simba_stays_open_when_billing_is_on(self) -> None:
        user, _ = self.users.register("sam@simba.services", "password12")
        org = self.users.org(user["org_id"])
        workspace = workspace_for(user, org, self.users.org_members(user["org_id"]))
        self.assertTrue(workspace["allowed"])

    @patch("wellnav.billing.billing_enforced", lambda: True)
    def test_unpaid_company_is_blocked(self) -> None:
        user, _ = self.users.register("pat@acme.test", "password12")
        org = self.users.org(user["org_id"])
        workspace = workspace_for(user, org, self.users.org_members(user["org_id"]))
        self.assertFalse(workspace["allowed"])
        self.assertEqual(workspace["reason"], "unpaid")

    @patch("wellnav.billing.billing_enforced", lambda: True)
    def test_extra_member_needs_a_seat(self) -> None:
        admin, _ = self.users.register("admin@acme.test", "password12")
        member, _ = self.users.register("hand@acme.test", "password12")
        org = self.users.org(admin["org_id"])
        self.conn.execute(
            "UPDATE organizations SET billing_status = 'active', seat_count = 1 WHERE id = ?",
            (org["id"],),
        )
        self.conn.commit()
        org = self.users.org(admin["org_id"])
        members = self.users.org_members(admin["org_id"])
        admin_ws = workspace_for(admin, org, members)
        member_ws = workspace_for(member, org, members)
        self.assertTrue(admin_ws["allowed"])
        self.assertFalse(member_ws["allowed"])
        self.assertEqual(member_ws["reason"], "no_seat")
        self.assertEqual(admin_ws["needed"], 1)


class BillingFulfillmentTests(unittest.TestCase):
    def test_apply_subscription_maps_active_seats(self) -> None:
        saved: dict = {}

        def fake_save(org_id: int, **fields):
            saved.update(fields)
            saved["id"] = org_id
            return saved

        with (
            patch("wellnav.billing._org_row", lambda org_id: {"id": org_id, "domain": "acme.test", "billing_status": "none"}),
            patch("wellnav.billing.save_org_billing", fake_save),
        ):
            apply_subscription(
                7,
                {
                    "id": "sub_1",
                    "status": "active",
                    "customer": "cus_1",
                    "current_period_end": 1_800_000_000,
                    "items": {"data": [{"id": "si_1", "quantity": 4}]},
                },
                customer_id="cus_1",
                email="pat@acme.test",
            )
        self.assertEqual(saved["billing_status"], "active")
        self.assertEqual(saved["seat_count"], 4)
        self.assertEqual(saved["stripe_subscription_item_id"], "si_1")
        self.assertEqual(saved["current_period_end"], "2027-01-15")

    def test_apply_subscription_reads_period_from_item(self) -> None:
        saved: dict = {}

        def fake_save(org_id: int, **fields):
            saved.update(fields)
            return saved

        with (
            patch("wellnav.billing._org_row", lambda org_id: {"id": org_id, "domain": "acme.test"}),
            patch("wellnav.billing.save_org_billing", fake_save),
        ):
            apply_subscription(
                3,
                {
                    "id": "sub_2",
                    "status": "active",
                    "items": {"data": [{"id": "si_2", "quantity": 2, "current_period_end": 1_800_000_000}]},
                },
            )
        self.assertEqual(saved["current_period_end"], "2027-01-15")

    def test_incomplete_subscription_does_not_unlock(self) -> None:
        org = {
            "id": 1,
            "domain": "acme.test",
            "billing_status": "incomplete",
            "seat_count": 2,
        }
        user = {"id": 1, "email": "pat@acme.test", "is_admin": True}
        with patch("wellnav.billing.billing_enforced", lambda: True):
            workspace = workspace_for(user, org, [user])
        self.assertFalse(workspace["allowed"])
        self.assertEqual(workspace["reason"], "unpaid")

    def test_complimentary_org_is_not_overwritten(self) -> None:
        org = {"id": 2, "domain": "simba.services", "billing_status": "complimentary", "seat_count": 10000}
        with (
            patch("wellnav.billing._org_row", lambda org_id: org),
            patch("wellnav.billing.save_org_billing") as save,
        ):
            result = apply_subscription(2, {"id": "sub_x", "status": "active", "items": {"data": [{"quantity": 1}]}})
        save.assert_not_called()
        self.assertEqual(result, org)

    def test_checkout_creates_customer_and_uses_price(self) -> None:
        created: dict = {}

        class FakeStripe:
            class Customer:
                @staticmethod
                def create(**kwargs):
                    created["customer"] = kwargs
                    return {"id": "cus_new"}

            class checkout:
                class Session:
                    @staticmethod
                    def create(**kwargs):
                        created["session"] = kwargs
                        return {"url": "https://checkout.stripe.com/c/pay/cs_test"}

        org = {"id": 4, "domain": "acme.test", "billing_status": "none"}
        user = {"id": 1, "email": "pat@acme.test"}
        with (
            patch("wellnav.billing.stripe_configured", lambda: True),
            patch("wellnav.billing.stripe_price_id", lambda: "price_seat"),
            patch("wellnav.billing._stripe", lambda: FakeStripe),
            patch("wellnav.billing.save_org_billing", lambda *args, **kwargs: org),
        ):
            url = create_checkout_session(org, user, 3)
        self.assertEqual(url, "https://checkout.stripe.com/c/pay/cs_test")
        self.assertEqual(created["customer"]["email"], "pat@acme.test")
        self.assertEqual(created["session"]["customer"], "cus_new")
        self.assertEqual(created["session"]["line_items"][0]["price"], "price_seat")
        self.assertEqual(created["session"]["line_items"][0]["quantity"], 3)
        self.assertEqual(created["session"]["automatic_tax"], {"enabled": True})
        self.assertEqual(created["session"]["billing_address_collection"], "required")

    def test_invoice_paid_retrieves_subscription(self) -> None:
        saved: dict = {}

        def fake_save(org_id: int, **fields):
            saved.update(fields)
            saved["id"] = org_id
            return saved

        class FakeStripe:
            class Webhook:
                @staticmethod
                def construct_event(payload, signature, secret):
                    return {
                        "type": "invoice.paid",
                        "data": {
                            "object": {
                                "customer": "cus_9",
                                "subscription": "sub_9",
                                "metadata": {"org_id": "9"},
                            }
                        },
                    }

            class Subscription:
                @staticmethod
                def retrieve(sub_id):
                    return {
                        "id": sub_id,
                        "status": "active",
                        "customer": "cus_9",
                        "items": {"data": [{"id": "si_9", "quantity": 5}]},
                    }

        with (
            patch("wellnav.billing.stripe_webhook_secret", lambda: "whsec_test"),
            patch("wellnav.billing._stripe", lambda: FakeStripe),
            patch("wellnav.billing._org_row", lambda org_id: {"id": org_id, "domain": "acme.test"}),
            patch("wellnav.billing.save_org_billing", fake_save),
        ):
            kind = handle_webhook(b"{}", "sig")
        self.assertEqual(kind, "invoice.paid")
        self.assertEqual(saved["billing_status"], "active")
        self.assertEqual(saved["seat_count"], 5)


class BillingRouteTests(unittest.TestCase):
    def test_webhook_is_public_and_billing_is_gated(self) -> None:
        client = TestClient(app, follow_redirects=False)
        hook = client.post("/billing/webhook", content=b"{}", headers={"stripe-signature": "bad"})
        self.assertEqual(hook.status_code, 400)
        self.assertEqual(hook.text, "invalid webhook")
        billing = client.get("/billing")
        self.assertEqual(billing.status_code, 303)
        self.assertTrue(billing.headers["location"].startswith("/login"))
        success = client.get("/billing/success")
        self.assertEqual(success.status_code, 200)
        self.assertIn("Return to the app", success.text)
        self.assertIn("not an App Store or Play Store purchase", success.text)


if __name__ == "__main__":
    unittest.main()
