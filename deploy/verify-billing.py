"""Check live billing copy, $15 price, and public routes."""

from __future__ import annotations

import urllib.error
import urllib.request

from wellnav.billing import complimentary_email, seat_price_label


def fetch(path: str) -> tuple[int, str]:
    req = urllib.request.Request(f"http://127.0.0.1:5050{path}")

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        loc = exc.headers.get("Location") or ""
        return exc.code, loc or body


def require(cond: bool, message: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {message}")
    print("OK", message)


def main() -> None:
    health = urllib.request.urlopen("http://127.0.0.1:5050/healthz", timeout=10).read().decode()
    require(health.strip() == "ok", f"health {health!r}")
    require(seat_price_label() == "$15 / person / month", seat_price_label())
    require(complimentary_email("ops@simba.services"), "simba complimentary")

    code, body = fetch("/privacy")
    require(code == 200 and "Stripe" in body and "complimentary" not in body.lower(), f"privacy {code}")

    code, body = fetch("/terms")
    require(
        code == 200
        and "Stripe" in body
        and "complimentary" not in body.lower()
        and "do not require payment" not in body.lower(),
        f"terms {code}",
    )

    code, body = fetch("/login")
    require(code == 200 and "complimentary" not in body.lower(), f"login footer {code}")

    code, loc = fetch("/billing")
    require(code in {301, 302, 303, 307, 308} and "/login" in loc, f"billing gate {code} {loc}")

    code, body = fetch("/billing/webhook")
    require(code == 405 or code == 400, f"webhook get {code}")

    print("ALL_OK")


if __name__ == "__main__":
    main()
