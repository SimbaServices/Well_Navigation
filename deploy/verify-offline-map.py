"""Check live offline-map assets, auth gate, and a logged-in tile fetch."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request

from wellnav.accounts import USERS
from wellnav.offline_tiles import lat_lon_to_tile, usgs_url, validate_tile

BASE = "http://127.0.0.1:5050"
EMAIL = "uicheck.offline@wellnav.test"
PASSWORD = "OfflineCheck12"


def fetch(url: str, *, data: bytes | None = None, headers: dict | None = None, cookie: str = "") -> tuple[int, dict, bytes]:
    req = urllib.request.Request(url, data=data, method="POST" if data is not None else "GET")
    req.add_header("User-Agent", "wellnav-offline-check")
    if cookie:
        req.add_header("Cookie", cookie)
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)
    try:
        with opener.open(req, timeout=25) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def cookie_from(headers: dict) -> str:
    raw = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
    return raw.split(";", 1)[0]


def require(cond: bool, message: str) -> None:
    if not cond:
        raise SystemExit(f"FAIL {message}")
    print("OK", message)


def cleanup() -> None:
    from wellnav.db import connect

    conn = connect()
    cur = conn.execute("DELETE FROM users WHERE username LIKE 'uicheck.offline%'")
    conn.commit()
    print("cleanup", cur.rowcount)


def main() -> None:
    health = urllib.request.urlopen(f"{BASE}/healthz", timeout=10).read().decode()
    require(health.strip() == "ok", f"health {health!r}")

    code, headers, body = fetch(f"{BASE}/sw.js")
    text = body.decode("utf-8", "replace")
    require(code == 200 and "wellnav-shell" in text, f"sw.js {code}")
    require(headers.get("Service-Worker-Allowed") == "/" or headers.get("service-worker-allowed") == "/", "sw allowed /")

    code, _, body = fetch(f"{BASE}/static/js/offline-map.js?v=1")
    require(code == 200 and b"downloadArea" in body, f"offline-map.js {code}")

    code, _, body = fetch(f"{BASE}/static/vendor/leaflet/leaflet.js")
    require(code == 200 and b"TileLayer" in body, f"leaflet.js {code}")

    code, _, body = fetch(f"{BASE}/static/vendor/htmx.min.js")
    require(code == 200 and b"htmx" in body, f"htmx {code}")

    code, _, body = fetch(f"{BASE}/login")
    login = body.decode("utf-8", "replace")
    require(code == 200 and "/static/vendor/htmx.min.js" in login, f"login vendor {code}")
    require("unpkg.com" not in login, "login not using unpkg")

    code, _, body = fetch(f"{BASE}/privacy")
    privacy = body.decode("utf-8", "replace")
    require(code == 200 and "Save this view" in privacy, f"privacy offline copy {code}")

    code, headers, _ = fetch(f"{BASE}/offline/tiles/12/1/1")
    loc = headers.get("Location") or headers.get("location") or ""
    require(code in {301, 302, 303, 307, 308} and "/login" in loc, f"tile gate {code} {loc}")

    x, y = lat_lon_to_tile(31.997, -102.078, 12)
    require(validate_tile(12, x, y), f"midland tile {x},{y}")
    require("/tile/12/" in usgs_url(12, x, y), "usgs url")

    cleanup()
    user, error = USERS.register(EMAIL, PASSWORD)
    require(user is not None and not error, f"register {error}")

    try:
        payload = urllib.parse.urlencode({"email": EMAIL, "password": PASSWORD, "next": "/"}).encode()
        code, headers, _ = fetch(f"{BASE}/login", data=payload, headers={"Content-Type": "application/x-www-form-urlencoded"})
        cookie = cookie_from(headers)
        require(code in {303, 302} and cookie.startswith("wellnav="), f"login {code} cookie={bool(cookie)}")

        code, _, body = fetch(f"{BASE}/", cookie=cookie)
        home = body.decode("utf-8", "replace")
        require(code == 200 and 'id="offline-save"' in home, f"home offline button {code}")
        require("offline-map.js" in home and "/static/vendor/leaflet/leaflet.js" in home, "home vendor scripts")
        require("Save this view" in home, "home save label")

        code, headers, tile = fetch(f"{BASE}/offline/tiles/12/{y}/{x}", cookie=cookie)
        ctype = headers.get("Content-Type") or headers.get("content-type") or ""
        require(
            code == 200 and len(tile) > 500 and ("image" in ctype or tile[:2] == b"\xff\xd8"),
            f"tile fetch {code} {ctype} {len(tile)}",
        )
        require(len(tile) > 500, f"tile size {len(tile)}")
        print("OK midland tile bytes", len(tile), ctype)
    finally:
        cleanup()

    print("ALL_OK")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            cleanup()
        except Exception:
            pass
        raise
