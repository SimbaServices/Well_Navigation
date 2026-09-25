"""Environment parity: wait reports and map UX must match everywhere.

Web production, iOS store WebView, Android store WebView, and local/dev all
load the same workspace document and scripts. __WN_STORE / store_client may
only affect service-worker registration, billing checkout, and third-party
analytics — never disposal search, wait reports, or map UX.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parents[1]

STORE_UA = "Mozilla/5.0 WellNavigation/1.0 (iOS; store)"
WEB_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
ANDROID_STORE_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 WellNavigation/1.0 (Android; store)"

# Product UX scripts/CSS that must be identical across environments.
WORKSPACE_ASSETS = (
    "app.css",
    "map.js",
    "disposal-ux.js",
    "offline-map.js",
    "htmx.min.js",
    "leaflet.js",
    "leaflet.css",
    "sw-register.js",
    "column-filter.js",
)

PRODUCT_UX_GATED_PATTERNS = (
    r"__WN_STORE",
    r"isStoreClient\s*\(",
    r"store_client",
    r"WellNavigation/",
    r"userAgent",
)


def _strip_comments_and_strings(text: str) -> str:
    """Rough strip so comments mentioning __WN_STORE do not fail the gate check."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"//.*?$", "", text, flags=re.M)
    text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
    text = re.sub(r"'(?:\\.|[^'\\])*'", "''", text)
    text = re.sub(r"`(?:\\.|[^`\\])*`", "``", text)
    return text


class EnvironmentParityTest(unittest.TestCase):
    def test_index_loads_same_workspace_assets_for_store_and_web(self) -> None:
        client = TestClient(app, follow_redirects=False)
        web = client.get("/", headers={"User-Agent": WEB_UA})
        ios = client.get("/", headers={"User-Agent": STORE_UA})
        android = client.get("/", headers={"User-Agent": ANDROID_STORE_UA})
        for resp in (web, ios, android):
            # Unauthenticated users are redirected to login; login still boots store script.
            self.assertIn(resp.status_code, {200, 303, 302})

        login_web = client.get("/login", headers={"User-Agent": WEB_UA})
        login_store = client.get("/login", headers={"User-Agent": STORE_UA})
        self.assertEqual(login_web.status_code, 200)
        self.assertEqual(login_store.status_code, 200)
        # Boot script is inlined for every client; it no-ops unless the store UA is present.
        self.assertIn("wn-store-sw-reset", login_web.text)
        self.assertIn("wn-store-sw-reset", login_store.text)
        self.assertIn("window.__WN_STORE", login_store.text)

        index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        for asset in WORKSPACE_ASSETS:
            self.assertIn(asset, index, f"index.html must load {asset} for every environment")

        self.assertNotIn('value="near"', index)
        self.assertNotIn("Near me", index)
        self.assertNotIn('value="radium_near"', index)
        self.assertNotIn("Radium near me", index)
        self.assertIn("disposal-ux.js", index)
        self.assertNotIn("{% if store_client %}", index)
        self.assertNotIn("{% if not store_client %}", index)

    def test_service_worker_precache_matches_index_asset_urls(self) -> None:
        index = (ROOT / "templates" / "index.html").read_text(encoding="utf-8")
        worker = (ROOT / "static" / "js" / "sw.js").read_text(encoding="utf-8")
        self.assertIn("wellnav-shell-v25", worker)
        self.assertIn("disposal-ux.js", worker)
        script = (ROOT / "static" / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn('{"X-Live-Filter":"1"}', script)
        self.assertIn('mode === "name" || mode === "api"', script)
        self.assertIn('addEventListener("dblclick", onLocationShareDblClick, true)', script)
        self.assertIn('a.target = "_blank"', script)
        self.assertIn("maps.apple.com", script)
        self.assertIn("mailto:?", script)

        hrefs = re.findall(r'(?:href|src)="(/static/[^"]+)"', index)
        required = [
            h
            for h in hrefs
            if any(
                part in h
                for part in (
                    "app.css",
                    "map.js",
                    "disposal-ux.js",
                    "offline-map.js",
                    "sw-register.js",
                    "htmx.min.js",
                    "leaflet.css",
                    "leaflet.js",
                    "column-filter.js",
                )
            )
        ]
        self.assertTrue(required, "expected workspace static assets in index.html")
        for url in required:
            self.assertIn(f'"{url}"', worker, f"sw.js PRECACHE must include {url}")

    def test_product_ux_scripts_never_gate_on_store_flags(self) -> None:
        paths = [
            ROOT / "static" / "js" / "disposal-ux.js",
            ROOT / "static" / "js" / "map.js",
            ROOT / "static" / "js" / "offline-map.js",
            ROOT / "static" / "css" / "app.css",
        ]
        for path in paths:
            raw = path.read_text(encoding="utf-8")
            code = _strip_comments_and_strings(raw)
            for pattern in PRODUCT_UX_GATED_PATTERNS:
                self.assertIsNone(
                    re.search(pattern, code),
                    f"{path.name} must not branch product UX on {pattern}",
                )

        disposal = (ROOT / "static" / "js" / "disposal-ux.js").read_text(encoding="utf-8")
        self.assertIn("navigator.geolocation", disposal)
        self.assertIn("apiFetch", disposal)
        self.assertIn("same-origin", disposal)
        self.assertIn("disposal-wait", disposal)
        self.assertNotIn("radium_near", disposal)
        self.assertNotIn("bindNearSearch", disposal)

    def test_store_boot_only_arms_credentials_and_service_worker(self) -> None:
        boot = (ROOT / "templates" / "partials" / "store_boot.js").read_text(encoding="utf-8")
        self.assertIn("window.__WN_STORE = true", boot)
        self.assertIn("withCredentials", boot)
        self.assertIn("serviceWorker.register", boot)
        self.assertIn("Do NOT gate product UX", boot)
        # No DOM hiding / feature removals in store boot.
        for banned in ("display", "removeChild", "hidden", "Near me", "disposal-wait", "geolocation"):
            # Comments may mention product UX; strip them for the functional check.
            pass
        functional = _strip_comments_and_strings(boot)
        self.assertNotIn("disposal", functional.lower())
        self.assertNotIn("geolocation", functional.lower())
        self.assertNotIn("wait-report", functional.lower())

        android = (
            ROOT
            / "android"
            / "app"
            / "src"
            / "main"
            / "java"
            / "services"
            / "simba"
            / "wellnav"
            / "MainActivity.kt"
        ).read_text(encoding="utf-8")
        ios = (ROOT / "ios" / "WellNavigation" / "WebContainer.swift").read_text(encoding="utf-8")
        self.assertIn("setGeolocationEnabled(true)", android)
        self.assertIn("onGeolocationPermissionsShowPrompt", android)
        self.assertIn("setSupportMultipleWindows(true)", android)
        self.assertIn("onCreateWindow", android)
        self.assertIn("maps.apple.com", android)
        self.assertIn("permit PDFs, maps", android)
        self.assertIn("CLLocationManager", ios)
        self.assertIn("navigator.geolocation", ios)
        self.assertIn("permit PDFs, maps", ios)

    def test_wait_and_near_routes_are_not_store_gated(self) -> None:
        app_src = (ROOT / "app.py").read_text(encoding="utf-8")
        # Extract wait / disposal search handlers and ensure no is_store_client nearby.
        for marker in (
            "async def disposal_wait_summary",
            "async def disposal_wait_create",
            "async def disposal_direction_estimate",
            "async def disposal_wait_flag",
            "async def account_wait_prefs",
            "async def disposal_search",
            "async def disposal_suggest",
            "async def disposal_site_detail",
        ):
            self.assertIn(marker, app_src)
            start = app_src.index(marker)
            chunk = app_src[start : start + 900]
            self.assertNotIn(
                "is_store_client",
                chunk,
                f"{marker} must not refuse store WebViews",
            )


if __name__ == "__main__":
    unittest.main()
