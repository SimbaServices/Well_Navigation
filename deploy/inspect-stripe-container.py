"""Print Stripe env inside the web container without leaking secrets."""

import os

secret = os.environ.get("WELLNAV_STRIPE_SECRET") or os.environ.get("STRIPE_SECRET_KEY") or ""
if secret.startswith("sk_live_"):
    mode = "sk_live"
elif secret.startswith("sk_test_"):
    mode = "sk_test"
elif secret:
    mode = "set"
else:
    mode = "missing"
print(f"container_secret={mode}")
print(f"container_price={os.environ.get('WELLNAV_STRIPE_PRICE_ID') or 'missing'}")
print(f"container_lookup={os.environ.get('WELLNAV_STRIPE_PRICE_LOOKUP_KEY') or 'missing'}")
print(f"container_cents={os.environ.get('WELLNAV_SEAT_PRICE_CENTS') or 'missing'}")
print(f"container_webhook={'set' if os.environ.get('WELLNAV_STRIPE_WEBHOOK_SECRET') else 'missing'}")
