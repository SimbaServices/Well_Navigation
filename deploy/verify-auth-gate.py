"""Check that guests are gated and the users table has phone columns."""

from __future__ import annotations

import urllib.error
import urllib.request

from wellnav.db import connect


def _status(url: str) -> tuple[int, str, str]:
    request = urllib.request.Request(url, method="GET")
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
    # Do not follow redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(request, timeout=10) as response:
            body = response.read().decode("utf-8", "replace")
            return response.status, response.headers.get("Location") or "", body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, exc.headers.get("Location") or "", body


def main() -> None:
    health = urllib.request.urlopen("http://127.0.0.1:5050/healthz", timeout=10).read().decode()
    if health.strip() != "ok":
        raise SystemExit(f"FAIL health {health!r}")
    print("OK health")

    code, location, _ = _status("http://127.0.0.1:5050/")
    if code not in {301, 302, 303, 307, 308} or not location.endswith("/login"):
        raise SystemExit(f"FAIL home {code} {location}")
    print("OK home_redirect", code, location)

    code, location, _ = _status("http://127.0.0.1:5050/search")
    if code not in {301, 302, 303, 307, 308} or "/login" not in location:
        raise SystemExit(f"FAIL search {code} {location}")
    print("OK search_redirect")

    code, _, body = _status("http://127.0.0.1:5050/login")
    if code != 200 or "Sign in" not in body or "search-form" in body:
        raise SystemExit(f"FAIL login page {code} has_search={'search-form' in body}")
    if "6-digit" not in body:
        raise SystemExit("FAIL login missing OTP copy")
    print("OK login_page")

    code, _, body = _status("http://127.0.0.1:5050/register")
    if code != 200 or "Mobile number" not in body:
        raise SystemExit(f"FAIL register page {code}")
    print("OK register_page")

    conn = connect()
    cols = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
    if "phone" not in cols or "phone_verified_at" not in cols:
        raise SystemExit(f"FAIL users columns {sorted(cols)}")
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "otp_challenges" not in tables or "pending_signups" not in tables:
        raise SystemExit(f"FAIL auth tables {tables}")
    print("OK schema")
    print("ALL_OK")


if __name__ == "__main__":
    main()
