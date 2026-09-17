"""Confirm the container can read the live MP Solutions seat Price."""

from __future__ import annotations

import os

import stripe

from wellnav.billing import billing_enforced, seat_price_label, stripe_configured

secret = (os.environ.get("WELLNAV_STRIPE_SECRET") or "").strip()
price_id = (os.environ.get("WELLNAV_STRIPE_PRICE_ID") or "").strip()
if not secret.startswith("sk_live_"):
    raise SystemExit("secret_not_live")
if not price_id:
    raise SystemExit("price_missing")

stripe.api_key = secret
price = stripe.Price.retrieve(price_id)
print(f"price_id={price.id}")
print(f"livemode={bool(price.livemode)}")
print(f"unit_amount={int(price.unit_amount or 0)}")
print(f"lookup_key={price.lookup_key}")
print(f"configured={stripe_configured()}")
print(f"enforced={billing_enforced()}")
print(f"label={seat_price_label()}")
if int(price.unit_amount or 0) != 1500 or not price.livemode:
    raise SystemExit("price_mismatch")
print("stripe_ok")
