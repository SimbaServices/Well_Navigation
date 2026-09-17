"""Organization seat billing. Simba Services accounts are always complimentary.

Checkout stays on the website in the system browser. The iOS and Play Store
apps never load Stripe Checkout or the Customer Portal inside their WebViews.
Stripe handles cards; we never store a full card number. Billing is enforced
only when Stripe is configured, so a deploy without keys does not lock the
workspace.

The live price is $15 / seat / month. The app prefers the Stripe Price with
lookup key ``wellnav_seat_monthly`` so the UI and Checkout stay in lockstep.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from wellnav.auth import email_domain

COMPLIMENTARY_DOMAINS = frozenset({"simba.services"})
COMPLIMENTARY_SEATS = 10_000
MAX_SEATS = 200
DEFAULT_SEAT_CENTS = 1500
SEAT_PRICE_LOOKUP_KEY = "wellnav_seat_monthly"
STRIPE_API_VERSION = "2024-12-18.acacia"
PAID_STATUSES = frozenset({"active", "trialing"})
OPEN_CHECKOUT_STATUSES = frozenset(
    {"", "none", "canceled", "unpaid", "incomplete", "incomplete_expired"}
)
_PRICE_TTL_SECONDS = 300
_price_memo: dict[str, Any] = {"at": 0.0, "id": "", "cents": 0}


def complimentary_domain(domain: str) -> bool:
    text = (domain or "").strip().lower()
    if not text:
        return False
    return text == "simba.services" or text.endswith(".simba.services")


def complimentary_email(email: str) -> bool:
    return complimentary_domain(email_domain(email))


def is_store_client(user_agent: str) -> bool:
    """True for the App Store / Play Store wrappers, not mobile Safari."""
    text = user_agent or ""
    return "WellNavigation/" in text and "store" in text.lower()


def env_flag(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def stripe_secret() -> str:
    return env_flag("WELLNAV_STRIPE_SECRET") or env_flag("STRIPE_SECRET_KEY")


def stripe_price_override() -> str:
    return env_flag("WELLNAV_STRIPE_PRICE_ID") or env_flag("STRIPE_PRICE_ID")


def stripe_lookup_key() -> str:
    return env_flag("WELLNAV_STRIPE_PRICE_LOOKUP_KEY") or SEAT_PRICE_LOOKUP_KEY


def stripe_price_id() -> str:
    return str(load_seat_price().get("id") or "")


def stripe_webhook_secret() -> str:
    return env_flag("WELLNAV_STRIPE_WEBHOOK_SECRET") or env_flag("STRIPE_WEBHOOK_SECRET")


def stripe_configured() -> bool:
    if not stripe_secret():
        return False
    if stripe_price_override():
        return True
    return bool(load_seat_price().get("id"))


def billing_enforced() -> bool:
    raw = env_flag("WELLNAV_REQUIRE_BILLING").lower()
    if raw in {"0", "false", "no"}:
        return False
    if raw in {"1", "true", "yes"}:
        return True
    return stripe_configured()


def public_url() -> str:
    return env_flag("WELLNAV_PUBLIC_URL", "https://wellnav.simba.services").rstrip("/")


def reset_price_cache() -> None:
    _price_memo.update({"at": 0.0, "id": "", "cents": 0})


def _remember_price(price_id: str, cents: int) -> None:
    _price_memo.update({"at": time.monotonic(), "id": price_id, "cents": int(cents)})


def load_seat_price() -> dict[str, Any]:
    """Resolve the seat Price. Env ID wins; otherwise lookup ``wellnav_seat_monthly``."""
    now = time.monotonic()
    cached_id = str(_price_memo.get("id") or "")
    if cached_id and now - float(_price_memo.get("at") or 0) < _PRICE_TTL_SECONDS:
        return {"id": cached_id, "unit_amount": int(_price_memo.get("cents") or DEFAULT_SEAT_CENTS)}
    found_id = stripe_price_override()
    cents = DEFAULT_SEAT_CENTS
    raw = env_flag("WELLNAV_SEAT_PRICE_CENTS")
    if raw.isdigit():
        cents = int(raw)
    if stripe_secret():
        try:
            stripe = _stripe()
            price = None
            if found_id:
                price = stripe.Price.retrieve(found_id)
            else:
                listed = stripe.Price.list(active=True, limit=1, lookup_keys=[stripe_lookup_key()])
                rows = _pick(listed, "data") or []
                price = rows[0] if rows else None
            if price is not None:
                found_id = str(_pick(price, "id") or found_id)
                amount = _pick(price, "unit_amount")
                if amount is not None:
                    cents = int(amount)
        except Exception:
            if cached_id:
                return {
                    "id": cached_id,
                    "unit_amount": int(_price_memo.get("cents") or cents),
                }
    if found_id:
        _remember_price(found_id, cents)
    return {"id": found_id, "unit_amount": cents}


def seat_price_cents() -> int:
    loaded = load_seat_price()
    if loaded.get("id") and loaded.get("unit_amount"):
        return int(loaded["unit_amount"])
    raw = env_flag("WELLNAV_SEAT_PRICE_CENTS")
    if raw.isdigit():
        return int(raw)
    return DEFAULT_SEAT_CENTS


def seat_price_label() -> str:
    cents = seat_price_cents()
    dollars = cents / 100
    if dollars == int(dollars):
        return f"${int(dollars)} / person / month"
    return f"${dollars:.2f} / person / month"


def org_display_name(org: dict | None) -> str:
    if not org:
        return "Your workspace"
    domain = (org.get("domain") or "").strip().lower()
    if complimentary_domain(domain):
        return "Simba Services"
    name = (org.get("name") or domain or "Organization").strip()
    if "@" in name:
        return name
    if "." in name:
        head = name.split(".", 1)[0]
        return head.replace("-", " ").title() if head else name
    return name


def _truthy_status(status: str) -> str:
    text = (status or "none").strip().lower() or "none"
    return text


def is_complimentary_org(org: dict | None) -> bool:
    if not org:
        return False
    if _truthy_status(org.get("billing_status") or "") == "complimentary":
        return True
    return complimentary_domain(org.get("domain") or "")


def seated_ids(members: list[dict], seat_count: int, *, complimentary: bool) -> set[int]:
    if complimentary:
        return {int(member["id"]) for member in members if member.get("id")}
    count = max(0, int(seat_count or 0))
    admins = sorted(
        [member for member in members if member.get("is_admin")],
        key=lambda member: str(member.get("created_at") or ""),
    )
    others = sorted(
        [member for member in members if not member.get("is_admin")],
        key=lambda member: str(member.get("created_at") or ""),
    )
    return {int(member["id"]) for member in (admins + others)[:count] if member.get("id")}


def annotate_members(members: list[dict], seat_ids: set[int]) -> list[dict]:
    rows = []
    for member in members:
        item = dict(member)
        item["seated"] = int(member["id"]) in seat_ids if member.get("id") else False
        rows.append(item)
    return rows


def workspace_for(user: dict | None, org: dict | None, members: list[dict] | None = None) -> dict:
    people = list(members or [])
    complimentary = complimentary_email((user or {}).get("email") or "") or is_complimentary_org(org)
    status = _truthy_status((org or {}).get("billing_status") or "")
    if complimentary:
        status = "complimentary"
    seats = COMPLIMENTARY_SEATS if complimentary else int((org or {}).get("seat_count") or 0)
    ids = seated_ids(people, seats, complimentary=complimentary)
    annotated = annotate_members(people, ids)
    paid = status in PAID_STATUSES
    member_count = len(annotated)
    needed = 0 if complimentary else max(0, member_count - seats)
    allowed = True
    reason = "ok"
    if complimentary:
        reason = "complimentary"
    elif not billing_enforced():
        reason = "open"
    elif not org:
        allowed = False
        reason = "unpaid"
    elif status == "past_due":
        allowed = bool(user and user.get("id") in ids)
        reason = "past_due" if allowed else "unpaid"
    elif not paid:
        allowed = False
        reason = "unpaid"
    elif user and user.get("id") not in ids:
        allowed = False
        reason = "no_seat"
    return {
        "org": org,
        "org_name": org_display_name(org),
        "members": annotated,
        "member_count": member_count,
        "seat_count": 0 if complimentary else seats,
        "seated_count": member_count if complimentary else min(member_count, seats),
        "needed": needed,
        "suggested_seats": max(member_count, 1),
        "status": status,
        "complimentary": complimentary,
        "paid": paid or complimentary,
        "allowed": allowed,
        "reason": reason,
        "enforced": billing_enforced(),
        "stripe_ready": stripe_configured(),
        "can_manage": bool(user and user.get("is_admin")),
        "price_label": seat_price_label(),
        "period_end": (org or {}).get("current_period_end") or "",
    }


def is_billing_path(path: str) -> bool:
    if path in {"/billing", "/account", "/account/delete", "/org", "/logout"}:
        return True
    return (
        path.startswith("/billing/")
        or path.startswith("/org/")
        or path.startswith("/account/")
        or path.startswith("/ux/")
    )


def clamp_seats(raw: object) -> int:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        value = 1
    return max(1, min(MAX_SEATS, value))


def _db():
    from wellnav.repository import REPO

    return REPO.conn


def _org_row(org_id: int) -> dict | None:
    conn = _db()
    row = conn.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    return dict(row) if row else None


def _org_id_for_customer(customer_id: str) -> int | None:
    if not customer_id:
        return None
    conn = _db()
    row = conn.execute(
        "SELECT id FROM organizations WHERE stripe_customer_id = ?",
        (customer_id,),
    ).fetchone()
    return int(row["id"]) if row else None


def save_org_billing(org_id: int, **fields: Any) -> dict | None:
    if not fields:
        return _org_row(org_id)
    allowed = {
        "stripe_customer_id",
        "stripe_subscription_id",
        "stripe_subscription_item_id",
        "billing_status",
        "seat_count",
        "billing_email",
        "current_period_end",
    }
    sets = []
    values: list[Any] = []
    for key, value in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return _org_row(org_id)
    values.append(org_id)
    conn = _db()
    conn.execute(f"UPDATE organizations SET {', '.join(sets)} WHERE id = ?", values)
    conn.commit()
    return _org_row(org_id)


def mark_complimentary(org_id: int, domain: str = "") -> dict | None:
    if domain and not complimentary_domain(domain):
        return _org_row(org_id)
    return save_org_billing(
        org_id,
        billing_status="complimentary",
        seat_count=COMPLIMENTARY_SEATS,
    )


def _pick(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _unix_date(value: Any) -> str:
    if not value:
        return ""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(value)


def _subscription_item(sub: Any) -> Any:
    items = _pick(sub, "items")
    data = _pick(items, "data") if items is not None else None
    if isinstance(data, list) and data:
        return data[0]
    return None


def _period_end(sub: Any, item: Any) -> str:
    return _unix_date(_pick(sub, "current_period_end") or (item and _pick(item, "current_period_end")))


def _session_email(data: Any) -> str:
    email = _pick(data, "customer_email")
    if email:
        return str(email)
    details = _pick(data, "customer_details")
    return str(_pick(details, "email") or "") if details is not None else ""


def _invoice_subscription_id(invoice: Any) -> Any:
    sub = _pick(invoice, "subscription")
    if sub:
        return sub
    parent = _pick(invoice, "parent")
    details = _pick(parent, "subscription_details") if parent is not None else None
    return _pick(details, "subscription") if details is not None else None


def _map_billing_status(status: str) -> str:
    text = (status or "none").strip().lower() or "none"
    if text in {"canceled", "unpaid", "incomplete_expired"}:
        return "canceled" if text == "canceled" else "unpaid"
    if text == "past_due":
        return "past_due"
    if text in PAID_STATUSES:
        return text
    return text


def apply_subscription(org_id: int, sub: Any, *, customer_id: str = "", email: str = "") -> dict | None:
    org = _org_row(org_id)
    if org and is_complimentary_org(org):
        return org
    item = _subscription_item(sub)
    quantity = int(_pick(item, "quantity") or 0) if item is not None else 0
    return save_org_billing(
        org_id,
        stripe_customer_id=customer_id or str(_pick(sub, "customer") or "") or None,
        stripe_subscription_id=str(_pick(sub, "id") or "") or None,
        stripe_subscription_item_id=str(_pick(item, "id") or "") if item is not None else None,
        billing_status=_map_billing_status(str(_pick(sub, "status") or "none")),
        seat_count=quantity,
        billing_email=email or None,
        current_period_end=_period_end(sub, item) or None,
    )


def _stripe():
    import stripe

    key = stripe_secret()
    if not key:
        raise RuntimeError("Stripe is not configured.")
    stripe.api_key = key
    stripe.api_version = STRIPE_API_VERSION
    return stripe


def ensure_seat_price() -> dict[str, Any]:
    """Create or reuse the $15 / seat / month Price. Used by deploy, not the web process."""
    stripe = _stripe()
    key = stripe_lookup_key()
    listed = stripe.Price.list(active=True, limit=1, lookup_keys=[key])
    rows = list(_pick(listed, "data") or [])
    price = rows[0] if rows else None
    if price is not None and int(_pick(price, "unit_amount") or 0) == DEFAULT_SEAT_CENTS:
        price_id = str(_pick(price, "id"))
        _remember_price(price_id, DEFAULT_SEAT_CENTS)
        return {
            "id": price_id,
            "unit_amount": DEFAULT_SEAT_CENTS,
            "product": str(_pick(price, "product") or ""),
            "lookup_key": key,
        }
    kwargs: dict[str, Any] = {
        "currency": "usd",
        "unit_amount": DEFAULT_SEAT_CENTS,
        "recurring": {"interval": "month", "usage_type": "licensed"},
        "lookup_key": key,
        "transfer_lookup_key": True,
        "nickname": "$15 / seat / month",
        "tax_behavior": "exclusive",
    }
    if price is not None:
        kwargs["product"] = _pick(price, "product")
    else:
        kwargs["product_data"] = {
            "name": "Well Navigation seat",
            "unit_label": "seat",
            "statement_descriptor": "WELLNAV SEAT",
            "tax_code": "txcd_10103001",
        }
    created = stripe.Price.create(**kwargs)
    price_id = str(_pick(created, "id"))
    _remember_price(price_id, DEFAULT_SEAT_CENTS)
    return {
        "id": price_id,
        "unit_amount": DEFAULT_SEAT_CENTS,
        "product": str(_pick(created, "product") or ""),
        "lookup_key": key,
    }


WEBHOOK_EVENTS = (
    "checkout.session.completed",
    "checkout.session.async_payment_succeeded",
    "checkout.session.async_payment_failed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.paid",
    "invoice.payment_failed",
)


def ensure_webhook_endpoint() -> dict[str, Any]:
    """Create or update the public billing webhook. Secret is only returned on create."""
    stripe = _stripe()
    url = public_url() + "/billing/webhook"
    listed = stripe.WebhookEndpoint.list(limit=100)
    existing = None
    for row in _pick(listed, "data") or []:
        if str(_pick(row, "url") or "").rstrip("/") == url:
            existing = row
            break
    if existing is not None:
        endpoint = stripe.WebhookEndpoint.modify(
            str(_pick(existing, "id")),
            disabled=False,
            enabled_events=list(WEBHOOK_EVENTS),
        )
        return {
            "id": str(_pick(endpoint, "id")),
            "url": url,
            "secret": "",
            "created": False,
        }
    endpoint = stripe.WebhookEndpoint.create(
        url=url,
        enabled_events=list(WEBHOOK_EVENTS),
        api_version=STRIPE_API_VERSION,
        description="Well Navigation seat billing",
    )
    return {
        "id": str(_pick(endpoint, "id")),
        "url": url,
        "secret": str(_pick(endpoint, "secret") or ""),
        "created": True,
    }


def ensure_stripe_customer(org: dict, user: dict) -> str:
    existing = (org.get("stripe_customer_id") or "").strip()
    if existing:
        return existing
    stripe = _stripe()
    customer = stripe.Customer.create(
        email=user.get("email") or None,
        name=org_display_name(org),
        metadata={"org_id": str(org["id"])},
        idempotency_key=f"wellnav-customer-org-{org['id']}",
    )
    customer_id = str(_pick(customer, "id") or "")
    if not customer_id:
        raise RuntimeError("Stripe did not return a customer.")
    save_org_billing(
        int(org["id"]),
        stripe_customer_id=customer_id,
        billing_email=user.get("email") or None,
    )
    org["stripe_customer_id"] = customer_id
    return customer_id


def create_checkout_session(org: dict, user: dict, seats: int) -> str:
    if is_complimentary_org(org) or complimentary_email(user.get("email") or ""):
        raise RuntimeError("Checkout is not available for this workspace.")
    if not stripe_configured():
        raise RuntimeError("Billing is not connected yet. Add a Stripe price in the server environment.")
    status = _truthy_status(org.get("billing_status") or "")
    if (org.get("stripe_subscription_item_id") or "").strip() and status not in OPEN_CHECKOUT_STATUSES:
        raise RuntimeError("This workspace already has a subscription. Update seats or manage payment.")
    stripe = _stripe()
    quantity = clamp_seats(seats)
    price_id = stripe_price_id()
    if not price_id:
        raise RuntimeError("Billing is not connected yet. Add a Stripe price in the server environment.")
    customer = ensure_stripe_customer(org, user)
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer,
        client_reference_id=str(org["id"]),
        line_items=[{"price": price_id, "quantity": quantity}],
        success_url=public_url() + "/billing/success?session_id={CHECKOUT_SESSION_ID}",
        cancel_url=public_url() + "/org",
        allow_promotion_codes=True,
        billing_address_collection="required",
        customer_update={"address": "auto", "name": "auto"},
        automatic_tax={"enabled": True},
        metadata={"org_id": str(org["id"]), "user_id": str(user.get("id") or "")},
        subscription_data={"metadata": {"org_id": str(org["id"])}},
        idempotency_key=f"wellnav-checkout-org-{org['id']}-seats-{quantity}-{int(time.time()) // 10}",
    )
    url = _pick(session, "url")
    if not url:
        raise RuntimeError("Stripe did not return a checkout URL.")
    return str(url)


def create_portal_session(org: dict) -> str:
    customer = (org.get("stripe_customer_id") or "").strip()
    if not customer:
        raise RuntimeError("Add seats once to open the billing portal.")
    stripe = _stripe()
    session = stripe.billing_portal.Session.create(
        customer=customer,
        return_url=public_url() + "/org",
    )
    url = _pick(session, "url")
    if not url:
        raise RuntimeError("Stripe did not return a portal URL.")
    return str(url)


def update_subscription_seats(org: dict, seats: int) -> dict | None:
    item_id = (org.get("stripe_subscription_item_id") or "").strip()
    if not item_id:
        raise RuntimeError("Start a subscription before changing seats.")
    stripe = _stripe()
    quantity = clamp_seats(seats)
    stripe.SubscriptionItem.modify(
        item_id,
        quantity=quantity,
        proration_behavior="create_prorations",
        idempotency_key=f"wellnav-seats-{item_id}-{quantity}",
    )
    return save_org_billing(org["id"], seat_count=quantity)


def _retrieve_subscription(stripe: Any, sub: Any) -> Any:
    if isinstance(sub, str) and sub:
        return stripe.Subscription.retrieve(sub)
    return sub


def _apply_checkout_object(data: Any, stripe: Any) -> dict | None:
    org_id = _pick(data, "client_reference_id") or _pick(_pick(data, "metadata"), "org_id")
    if not org_id:
        return None
    sub = _retrieve_subscription(stripe, _pick(data, "subscription"))
    if not sub:
        return None
    return apply_subscription(
        int(org_id),
        sub,
        customer_id=str(_pick(data, "customer") or ""),
        email=_session_email(data),
    )


def sync_checkout_session(session_id: str) -> dict | None:
    stripe = _stripe()
    session = stripe.checkout.Session.retrieve(session_id, expand=["subscription"])
    return _apply_checkout_object(session, stripe)


def _apply_invoice(data: Any, stripe: Any) -> dict | None:
    customer = str(_pick(data, "customer") or "")
    org_id = _pick(_pick(data, "metadata"), "org_id") or _org_id_for_customer(customer)
    if not org_id:
        return None
    sub = _retrieve_subscription(stripe, _invoice_subscription_id(data))
    if not sub:
        return None
    return apply_subscription(int(org_id), sub, customer_id=customer)


def handle_webhook(payload: bytes, signature: str) -> str:
    secret = stripe_webhook_secret()
    if not secret:
        raise RuntimeError("Stripe webhook secret is not configured.")
    stripe = _stripe()
    event = stripe.Webhook.construct_event(payload, signature, secret)
    kind = str(_pick(event, "type") or "")
    data = _pick(_pick(event, "data"), "object")
    if kind in {
        "checkout.session.completed",
        "checkout.session.async_payment_succeeded",
        "checkout.session.async_payment_failed",
    }:
        _apply_checkout_object(data, stripe)
        return kind
    if kind in {
        "customer.subscription.updated",
        "customer.subscription.deleted",
        "customer.subscription.created",
    }:
        org_id = _pick(_pick(data, "metadata"), "org_id") or _org_id_for_customer(
            str(_pick(data, "customer") or "")
        )
        if org_id:
            apply_subscription(int(org_id), data, customer_id=str(_pick(data, "customer") or ""))
        return kind
    if kind in {"invoice.paid", "invoice.payment_failed"}:
        _apply_invoice(data, stripe)
        return kind
    return kind
