"""Create or reuse the $15 / seat / month Stripe Price and billing webhook.

Uses WELLNAV_STRIPE_SECRET (or STRIPE_SECRET_KEY). Safe to run in live or
sandbox: the lookup key wellnav_seat_monthly is transferred onto the $15 price.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _upsert_env(path: Path, updates: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            out.append(line)
            continue
        key = raw.split("=", 1)[0].strip()
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    text = "\n".join(out).rstrip() + "\n"
    path.write_text(text, encoding="utf-8")


def _secret_mode(value: str) -> str:
    if value.startswith("sk_live_"):
        return "live"
    if value.startswith("sk_test_"):
        return "test"
    return "unknown"


def main() -> None:
    env_path = Path("/home/wellnav/Well_Navigation/.env")
    if not env_path.is_file():
        env_path = ROOT / ".env"
    _load_dotenv(env_path)
    from wellnav.billing import (
        DEFAULT_SEAT_CENTS,
        ensure_seat_price,
        ensure_webhook_endpoint,
        seat_price_label,
        stripe_secret,
    )

    secret = stripe_secret()
    if not secret:
        raise SystemExit("WELLNAV_STRIPE_SECRET is not set")
    print(f"mode={_secret_mode(secret)}")
    price = ensure_seat_price()
    print(f"price_id={price['id']}")
    print(f"product={price['product']}")
    print(f"lookup_key={price['lookup_key']}")
    print(f"unit_amount={price['unit_amount']}")
    print(f"label={seat_price_label()}")
    if int(price["unit_amount"]) != DEFAULT_SEAT_CENTS:
        raise SystemExit(f"expected {DEFAULT_SEAT_CENTS} cents, got {price['unit_amount']}")
    hook = ensure_webhook_endpoint()
    print(f"webhook_id={hook['id']}")
    print(f"webhook_url={hook['url']}")
    print("webhook_secret=" + ("written" if hook["created"] and hook["secret"] else "kept"))
    updates = {
        "WELLNAV_SEAT_PRICE_CENTS": str(DEFAULT_SEAT_CENTS),
        "WELLNAV_STRIPE_PRICE_LOOKUP_KEY": str(price["lookup_key"]),
        "WELLNAV_STRIPE_PRICE_ID": str(price["id"]),
    }
    if hook["created"] and hook["secret"]:
        updates["WELLNAV_STRIPE_WEBHOOK_SECRET"] = hook["secret"]
    _upsert_env(env_path, updates)
    print(f"env={env_path}")


if __name__ == "__main__":
    main()
