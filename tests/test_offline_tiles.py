import unittest
from pathlib import Path

from starlette.testclient import TestClient

from app import app
from wellnav.offline_routes import (
    MAX_DESTINATIONS,
    bearing_deg,
    direct_line,
    haversine_m,
    parse_osrm,
    parse_pack_request,
    plan_offline_routes,
    simplify_line,
)
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
        self.assertIn("wellnav-shell-v27", worker)
        self.assertIn('name="scope"', index)
        self.assertIn('name="state"', index)
        self.assertNotIn('name="scope" value="wells"', index)
        register = (root / "static" / "js" / "sw-register.js").read_text(encoding="utf-8")
        self.assertIn("isStoreClient", register)
        android = (root / "android" / "app" / "src" / "main" / "java" / "services" / "simba" / "wellnav" / "MainActivity.kt").read_text(encoding="utf-8")
        ios = (root / "ios" / "WellNavigation" / "WebContainer.swift").read_text(encoding="utf-8")
        self.assertIn("wn-store-sw-reset", android)
        self.assertIn("setAcceptThirdPartyCookies", android)
        self.assertIn("CookieManager.getInstance().flush()", android)
        self.assertIn("httpCookieStore.getAllCookies", ios)
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
        self.assertIn("Save for offline", home)
        self.assertIn("offline-pin", home)
        self.assertIn("/static/js/offline-map.js", home)
        self.assertIn("/static/vendor/leaflet/leaflet.js", home)
        js = client.get("/static/js/offline-map.js?v=3")
        self.assertEqual(js.status_code, 200)
        self.assertIn("downloadArea", js.text)
        self.assertIn("downloadTiles", js.text)
        self.assertIn("saveRoutes", js.text)
        self.assertIn("directRoutes", js.text)
        self.assertIn("routeProgress", js.text)
        self.assertIn('return key === "usgs"', js.text)
        script = (Path(__file__).resolve().parents[1] / "static" / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("selectedBasemap()", script)
        self.assertIn("cacheableBasemap(basemap)", script)
        self.assertIn("Choose USGS Topo, then save again.", script)

    def test_tile_proxy_is_not_an_open_proxy(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.get("/offline/tiles/12/1/1")
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/login"))

    def test_route_pack_requires_sign_in(self) -> None:
        client = TestClient(app, follow_redirects=False)
        resp = client.post("/offline/routes", json={"origin": {"lat": 31.9, "lon": -102.1}, "destinations": []})
        self.assertEqual(resp.status_code, 303)
        self.assertTrue(resp.headers["location"].startswith("/login"))


class OfflineRoutePlanTest(unittest.TestCase):
    def test_direct_line_distance_and_bearing(self) -> None:
        # About 4.8 km due north of Midland.
        distance = haversine_m(31.997, -102.078, 32.04, -102.078)
        self.assertGreater(distance, 4500)
        self.assertLess(distance, 5200)
        self.assertLess(bearing_deg(31.997, -102.078, 32.04, -102.078), 5)
        line = direct_line(-102.078, 31.997, -102.078, 32.04)
        self.assertGreater(len(line), 2)
        self.assertEqual(line[0], (-102.078, 31.997))
        self.assertEqual(line[-1], (-102.078, 32.04))

    def test_simplify_keeps_ends(self) -> None:
        coords = [(float(i), 31.0) for i in range(1000)]
        short = simplify_line(coords, max_points=40)
        self.assertEqual(len(short), 40)
        self.assertEqual(short[0], coords[0])
        self.assertEqual(short[-1], coords[-1])

    def test_parse_osrm_geometry(self) -> None:
        parsed = parse_osrm(
            {
                "code": "Ok",
                "routes": [
                    {
                        "distance": 3200.2,
                        "duration": 240.4,
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[-102.08, 31.99], [-102.05, 32.01]],
                        },
                    }
                ],
            }
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed["coordinates"][0], (-102.08, 31.99))
        self.assertAlmostEqual(parsed["distance_m"], 3200.2)
        self.assertIsNone(parse_osrm({"code": "NoRoute"}))
        self.assertIsNone(parse_osrm({"code": "Ok", "routes": []}))

    def test_pack_request_rejects_bad_pins(self) -> None:
        with self.assertRaises(ValueError):
            parse_pack_request({"origin": {"lat": 31.9, "lon": -102.0}, "destinations": []})
        with self.assertRaises(ValueError):
            parse_pack_request(
                {
                    "origin": {"lat": 10.0, "lon": -102.0},
                    "destinations": [{"id": "pin:a", "lat": 31.9, "lon": -102.0}],
                }
            )
        too_many = [
            {"id": f"pin:{i}", "lat": 31.9, "lon": -102.0, "label": "Spot"}
            for i in range(MAX_DESTINATIONS + 1)
        ]
        with self.assertRaises(ValueError):
            parse_pack_request({"origin": {"lat": 31.9, "lon": -102.0}, "destinations": too_many})
        with self.assertRaises(ValueError):
            parse_pack_request(
                {
                    "origin": {"lat": 31.9, "lon": -102.0},
                    "destinations": [{"id": "bad id", "lat": 31.9, "lon": -102.0}],
                }
            )

    def test_short_hop_does_not_call_router(self) -> None:
        calls = {"n": 0}

        def fetch(*_args):
            calls["n"] += 1
            raise AssertionError("router should not be called")

        origin = {"lat": 31.997, "lon": -102.078}
        dest = [{"id": "pin:near", "label": "Pad", "kind": "pin", "lat": 31.9973, "lon": -102.078}]
        pack = plan_offline_routes(origin, dest, fetch_driving=fetch)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(pack["routes"][0]["mode"], "direct")
        self.assertGreaterEqual(len(pack["tiles"]), 1)
        self.assertLessEqual(len(pack["tiles"]), MAX_PACK_TILES)
        self.assertTrue(all(validate_tile(tile["z"], tile["x"], tile["y"]) for tile in pack["tiles"]))

    def test_router_failure_falls_back_to_direct_line(self) -> None:
        calls = {"n": 0}

        def fetch(*_args):
            calls["n"] += 1
            raise TimeoutError("router down")

        origin = {"lat": 31.997, "lon": -102.078}
        dest = [{"id": "well:4200300290", "label": "University", "kind": "well", "lat": 32.04, "lon": -102.02}]
        pack = plan_offline_routes(origin, dest, fetch_driving=fetch)
        self.assertEqual(calls["n"], 1)
        route = pack["routes"][0]
        self.assertEqual(route["mode"], "direct")
        self.assertGreater(route["distance_m"], 1000)
        self.assertTrue(pack["tiles"])
        self.assertLessEqual(len(pack["tiles"]), MAX_PACK_TILES)

    def test_driving_route_covers_the_detour(self) -> None:
        def fetch(lat1, lon1, lat2, lon2):
            return {
                "coordinates": [(lon1, lat1), (-102.078, 32.2), (lon2, lat2)],
                "distance_m": 18000,
                "duration_s": 900,
            }

        origin = {"lat": 31.997, "lon": -102.078}
        dest = [{"id": "pin:east", "label": "Tank battery", "kind": "pin", "lat": 32.01, "lon": -101.95}]
        pack = plan_offline_routes(origin, dest, fetch_driving=fetch)
        route = pack["routes"][0]
        self.assertEqual(route["mode"], "driving")
        self.assertEqual(route["duration_s"], 900)
        north_x, north_y = lat_lon_to_tile(32.2, -102.078, 12)
        covered = {(tile["z"], tile["x"], tile["y"]) for tile in pack["tiles"]}
        self.assertIn((12, north_x, north_y), covered)

    def test_several_pins_keep_separate_routes(self) -> None:
        def fetch(lat1, lon1, lat2, lon2):
            return {
                "coordinates": [(lon1, lat1), (lon2, lat2)],
                "distance_m": haversine_m(lat1, lon1, lat2, lon2),
                "duration_s": 600,
            }

        origin = {"lat": 31.8, "lon": -102.1}
        dests = [
            {"id": "well:a", "label": "A", "kind": "well", "lat": 31.9, "lon": -102.0},
            {"id": "pin:b", "label": "B", "kind": "pin", "lat": 31.7, "lon": -102.2},
            {"id": "disposal:c", "label": "C", "kind": "disposal", "lat": 31.85, "lon": -101.9},
        ]
        pack = plan_offline_routes(origin, dests, fetch_driving=fetch)
        self.assertEqual([route["id"] for route in pack["routes"]], ["well:a", "pin:b", "disposal:c"])
        self.assertTrue(all(route["mode"] == "driving" for route in pack["routes"]))
        self.assertTrue(all(route["origin"] == {"lat": 31.8, "lon": -102.1} for route in pack["routes"]))
        self.assertLessEqual(len(pack["tiles"]), MAX_PACK_TILES)


if __name__ == "__main__":
    unittest.main()
