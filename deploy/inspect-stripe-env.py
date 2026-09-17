"""Print Stripe env presence without leaking secrets."""

from pathlib import Path

ENV = Path("/home/wellnav/Well_Navigation/.env")
keys = {}
for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
    text = line.strip()
    if not text or text.startswith("#") or "=" not in text:
        continue
    key, value = text.split("=", 1)
    keys[key.strip()] = value.strip().strip("'").strip('"')


def show(name: str) -> str:
    value = keys.get(name) or ""
    if not value:
        return "missing"
    if value.startswith("sk_live_"):
        return "sk_live"
    if value.startswith("sk_test_"):
        return "sk_test"
    if value.startswith("whsec_"):
        return "whsec_set"
    if value.startswith("price_"):
        return value
    return "set"


for name in (
    "WELLNAV_STRIPE_SECRET",
    "STRIPE_SECRET_KEY",
    "WELLNAV_STRIPE_WEBHOOK_SECRET",
    "WELLNAV_STRIPE_PRICE_ID",
    "WELLNAV_STRIPE_PRICE_LOOKUP_KEY",
    "WELLNAV_SEAT_PRICE_CENTS",
    "WELLNAV_REQUIRE_BILLING",
    "WELLNAV_PUBLIC_URL",
):
    print(f"{name}={show(name) if name.endswith('SECRET') or name.endswith('KEY') else (keys.get(name) or 'missing')}")
