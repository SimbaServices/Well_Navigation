import unittest
from pathlib import Path

from starlette.testclient import TestClient

from app import app
from wellnav.offline_tiles import (
    MAX_PACK_TILES,
    count_tiles,
    lat_lon_to_tile,
    pack_zooms,
    tiles_in_bounds,
    usgs_url,
    validate_tile,
)


class OfflineTilesTest(unittest.TestCase):
    def test_midland_tile_is_in_coverage(self) -> None:
        x, y = lat_lon_to_tile(31.997, -102.078, 12)
        self.assertTrue(validate_tile(12, x, y))
        self.assertIn("/tile/12/", usgs_url(12, x, y))
        self.assertTrue(usgs_url(12, x, y).endswith(f"/{y}/{x}"))

    def test_rejects_ocean_and_bad_zoom(self) -> None:
        self.assertFalse(validate_tile(3, 1, 1))
        self.assertFalse(validate_tile(12, 0, 0))
        self.assertFalse(validate_tile(12, -1, 4))

    def test_pack_count_caps_wide_view(self) -> None:
        n = count_tiles(-107.0, 25.5, -93.0, 36.6, 10, 14)
        self.assertGreater(n, MAX_PACK_TILES)

    def test_small_field_view_is_downloadable(self) -> None:
        west, south, east, north = -102.2, 31.85, -101.95, 32.1
        min_z, max_z = pack_zooms(13)
        n = count_tiles(west, south, east, north, min_z, max_z)
        self.assertLessEqual(n, MAX_PACK_TILES)
        self.assertGreater(n, 10)
        self.assertEqual(len(tiles_in_bounds(west, south, east, north, 12)), count_tiles(west, south, east, north, 12, 12))


class OfflineRoutesTest(unittest.TestCase):
    def test_store_webview_does_not_keep_a_service_worker(self) -> None:
        root = Path(__file__).resolve().parents[1]
        boot = (root / "templates" / "partials" / "store_boot.js").read_text(encoding="utf-8")
        self.assertIn("wn-store-sw-reset", boot)
        self.assertIn("serviceWorker.register", boot)
        self.assertIn("withCredentials", boot)
        client = TestClient(app, follow_redirects=False)
        login = client.get("/login")
        self.assertEqual(login.status_code, 200)
        self.assertIn("wn-store-sw-reset", login.text)
        self.assertIn("navigator.serviceWorker.register = function", login.text)
        self.assertNotIn("navigator.serviceWorker.register = function () {&amp;", login.text)
        index = (root / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("partials/store_boot.html", index)
        self.assertIn("keyup[this.value.trim().length>=2]", index)
        worker = (root / "static" / "js" / "sw.js").read_text(encoding="utf-8")
        self.assertIn('pathname === "/sw.js"', worker)
        self.assertIn("wellnav-shell-v6", worker)
        self.assertIn('name="scope"', index)
        self.assertIn('name="state"', index)
        self.assertNotIn('name="scope" value="wells"', index)
        register = (root / "static" / "js" / "sw-register.js").read_text(encoding="utf-8")
        self.assertIn("isStoreClient", register)
        android = (root / "android" / "app" / "src" / "main" / "java" / "services" / "simba" / "wellnav" / "MainActivity.kt").read_text(encoding="utf-8")
        ios = (root / "ios" / "WellNavigation" / "WebContainer.swift").read_text(encoding="utf-8")
        self.assertIn("wn-store-sw-reset", android)
        self.assertIn("setAcceptThirdPartyCookies", android)
        self.assertIn("wn-store-sw-reset", ios)
        self.assertNotIn("returnCacheDataElseLoad", ios)
        self.assertIn("reloadRevalidatingCacheData", ios)

    def test_service_worker_is_public(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/sw.js")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers.get("service-worker-allowed"), "/")
        self.assertIn("wellnav-shell", resp.text)
        manifest = client.get("/manifest.webmanifest")
        self.assertEqual(manifest.status_code, 200)
        home = (Path(__file__).resolve().parents[1] / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("offline-save", home)
        self.assertIn("/static/js/offline-map.js", home)
        self.assertIn("/static/vendor/leaflet/leaflet.js", home)
        js = client.get("/static/js/offline-map.js?v=1")
        self.assertEqual(js.status_code, 200)
        self.assertIn("downloadArea", js.text)

    def test_tile_proxy_is_not_an_open_proxy(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/offline/tiles/12/1/1")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/login"))


if __name__ == "__main__":
    unittest.main()
