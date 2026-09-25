"""Geodesic distance, bearing, interpolation, and the simulation API."""

from __future__ import annotations

import time
import unittest

from pyproj import Geod

from route_sim.geo import (
    bearing_deg,
    equator_degree_m,
    geodesic_distance_m,
    position_after,
)
from route_sim.simulator import DEFAULT_ACCURACY_M, DEFAULT_SPEED_KMH, TICK_SECONDS, Simulator

_GEOD = Geod(ellps="WGS84")
_FIX_FIELDS = {
    "latitude",
    "longitude",
    "heading",
    "speedMps",
    "speedKmh",
    "accuracyMeters",
    "timestamp",
    "distanceMeters",
    "totalMeters",
    "fraction",
}


def _north(lng: float, lat: float, meters: float) -> list[float]:
    lon2, lat2, _back = _GEOD.fwd(lng, lat, 0, meters)
    return [lon2, lat2]


def _east(lng: float, lat: float, meters: float) -> list[float]:
    lon2, lat2, _back = _GEOD.fwd(lng, lat, 90, meters)
    return [lon2, lat2]


class GeodesicTest(unittest.TestCase):
    def test_equator_degree_matches_wgs84(self) -> None:
        distance = geodesic_distance_m([0.0, 0.0], [1.0, 0.0])
        self.assertAlmostEqual(distance, equator_degree_m(), delta=0.01)
        self.assertAlmostEqual(distance, 111319.490793, delta=0.01)

    def test_antimeridian_east_is_two_equator_degrees(self) -> None:
        distance = geodesic_distance_m([179.0, 0.0], [-179.0, 0.0])
        self.assertAlmostEqual(distance, 2 * equator_degree_m(), delta=0.05)

    def test_cardinal_bearings(self) -> None:
        self.assertAlmostEqual(bearing_deg([0.0, 0.0], [0.0, 1.0]), 0.0, delta=0.01)
        self.assertAlmostEqual(bearing_deg([0.0, 0.0], [1.0, 0.0]), 90.0, delta=0.01)
        self.assertAlmostEqual(bearing_deg([0.0, 1.0], [0.0, 0.0]), 180.0, delta=0.01)
        self.assertAlmostEqual(bearing_deg([1.0, 0.0], [0.0, 0.0]), 270.0, delta=0.01)

    def test_identical_points_have_zero_distance_and_bearing(self) -> None:
        self.assertEqual(geodesic_distance_m([10.0, 20.0], [10.0, 20.0]), 0.0)
        self.assertEqual(bearing_deg([10.0, 20.0], [10.0, 20.0]), 0.0)


class InterpolationTest(unittest.TestCase):
    def test_position_after_seconds_on_equator(self) -> None:
        route = [[0.0, 0.0], [1.0, 0.0]]
        degree = geodesic_distance_m(route[0], route[1])
        pose = position_after(route, speed_kmh=360, elapsed_s=100)

        self.assertAlmostEqual(geodesic_distance_m([0.0, 0.0], [pose.longitude, pose.latitude]), 10000, delta=0.05)
        self.assertAlmostEqual(pose.longitude, 10000 / degree, delta=1e-7)
        self.assertAlmostEqual(pose.latitude, 0.0, delta=1e-8)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.01)
        self.assertAlmostEqual(pose.distance_m, 10000, delta=0.05)
        self.assertAlmostEqual(pose.total_m, degree, delta=0.01)
        self.assertAlmostEqual(pose.fraction, 10000 / degree, delta=1e-9)

    def test_position_after_seconds_follows_polyline_not_chord(self) -> None:
        start = [-97.0, 30.0]
        corner = _north(start[0], start[1], 1000)
        end = _east(corner[0], corner[1], 1000)
        route = [start, corner, end]
        pose = position_after(route, speed_kmh=36, elapsed_s=150)

        expected = _east(corner[0], corner[1], 500)
        self.assertLess(geodesic_distance_m([pose.longitude, pose.latitude], expected), 0.05)
        self.assertAlmostEqual(pose.distance_m, 1500, delta=0.05)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.05)
        self.assertAlmostEqual(pose.fraction, 0.75, delta=1e-6)

        chord = [start[0] + 0.75 * (end[0] - start[0]), start[1] + 0.75 * (end[1] - start[1])]
        self.assertGreater(geodesic_distance_m([pose.longitude, pose.latitude], chord), 100)

    def test_vertex_uses_outgoing_bearing(self) -> None:
        start = [-97.0, 30.0]
        corner = _north(start[0], start[1], 1000)
        end = _east(corner[0], corner[1], 1000)
        pose = position_after([start, corner, end], speed_kmh=36, elapsed_s=100)

        self.assertLess(geodesic_distance_m([pose.longitude, pose.latitude], corner), 0.05)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.05)
        self.assertAlmostEqual(pose.distance_m, 1000, delta=0.05)

    def test_clamps_at_end(self) -> None:
        pose = position_after([[0.0, 0.0], [1.0, 0.0]], speed_kmh=360, elapsed_s=1e9)
        self.assertEqual(pose.longitude, 1.0)
        self.assertEqual(pose.latitude, 0.0)
        self.assertEqual(pose.fraction, 1.0)
        self.assertAlmostEqual(pose.distance_m, geodesic_distance_m([0.0, 0.0], [1.0, 0.0]), delta=0.01)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.01)

    def test_loop_wraps_distance(self) -> None:
        route = [[0.0, 0.0], [1.0, 0.0]]
        total = geodesic_distance_m(route[0], route[1])
        elapsed = (total + 10000) / 100.0
        pose = position_after(route, speed_kmh=360, elapsed_s=elapsed, loop=True)
        direct = position_after(route, speed_kmh=360, elapsed_s=100, loop=False)
        self.assertAlmostEqual(pose.distance_m, 10000, delta=0.05)
        self.assertLess(geodesic_distance_m([pose.longitude, pose.latitude], [direct.longitude, direct.latitude]), 0.05)

    def test_zero_speed_stays_at_start_with_route_bearing(self) -> None:
        pose = position_after([[0.0, 0.0], [0.0, 0.0], [1.0, 0.0]], speed_kmh=0, elapsed_s=50)
        self.assertEqual(pose.distance_m, 0.0)
        self.assertEqual(pose.longitude, 0.0)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.01)

    def test_elapsed_zero_is_the_start(self) -> None:
        pose = position_after([[-97.0, 0.0], [-96.0, 0.0]], speed_kmh=50, elapsed_s=0)
        self.assertEqual(pose.latitude, 0.0)
        self.assertEqual(pose.longitude, -97.0)
        self.assertEqual(pose.fraction, 0.0)
        self.assertAlmostEqual(pose.heading, 90.0, delta=0.05)


class SimulatorClockTest(unittest.TestCase):
    def test_position_after_n_seconds_matches_geodesic(self) -> None:
        start = [0.0, 0.0]
        end = [1.0, 0.0]
        sim = Simulator()
        sim.set_route([start, end])
        sim.set_speed(360)
        sim.play()
        sim._t0 = time.monotonic() - 100

        payload, changed = sim.capture()
        pose = position_after([start, end], speed_kmh=360, elapsed_s=100)
        fix = payload["fix"]

        self.assertFalse(changed)
        self.assertEqual(payload["status"], "playing")
        self.assertLess(
            geodesic_distance_m([fix["longitude"], fix["latitude"]], [pose.longitude, pose.latitude]),
            0.05,
        )
        self.assertAlmostEqual(fix["heading"], pose.heading, delta=1e-6)
        self.assertAlmostEqual(fix["distanceMeters"], pose.distance_m, delta=0.05)
        self.assertAlmostEqual(fix["totalMeters"], pose.total_m, delta=1e-6)
        self.assertAlmostEqual(fix["fraction"], pose.fraction, delta=1e-6)
        self.assertAlmostEqual(fix["speedMps"], 100, delta=1e-9)
        self.assertEqual(fix["speedKmh"], 360)
        self.assertEqual(fix["accuracyMeters"], DEFAULT_ACCURACY_M)

    def test_playback_pauses_at_the_end(self) -> None:
        start = [-97.0, 30.0]
        end = _north(start[0], start[1], 1000)
        sim = Simulator()
        sim.set_route([start, end])
        sim.set_speed(36)
        sim.play()
        sim._t0 = time.monotonic() - 200

        payload, changed = sim.capture()
        self.assertTrue(changed)
        self.assertEqual(payload["status"], "paused")
        self.assertAlmostEqual(payload["fix"]["fraction"], 1.0)
        self.assertAlmostEqual(payload["fix"]["distanceMeters"], 1000, delta=0.05)
        self.assertFalse(sim.playing)

    def test_loop_keeps_playing_past_the_end(self) -> None:
        start = [-97.0, 30.0]
        end = _north(start[0], start[1], 1000)
        sim = Simulator()
        sim.set_route([start, end])
        sim.set_speed(36)
        sim.set_loop(True)
        sim.play()
        sim._t0 = time.monotonic() - 150

        payload, changed = sim.capture()
        self.assertFalse(changed)
        self.assertEqual(payload["status"], "playing")
        self.assertAlmostEqual(payload["fix"]["distanceMeters"], 500, delta=0.05)

    def test_defaults(self) -> None:
        self.assertEqual(DEFAULT_SPEED_KMH, 50)
        self.assertEqual(DEFAULT_ACCURACY_M, 5)
        self.assertEqual(TICK_SECONDS, 1)


class ApiTest(unittest.TestCase):
    def setUp(self) -> None:
        from starlette.testclient import TestClient

        from route_sim.app import SIM, app

        self.sim = SIM
        self.client = TestClient(app)
        self.client.__enter__()
        self.sim.reset_all()

    def tearDown(self) -> None:
        self.sim.reset_all()
        self.client.__exit__(None, None, None)

    def test_idle_until_a_route_is_set(self) -> None:
        location = self.client.get("/api/location")
        route = self.client.get("/api/route")
        self.assertEqual(location.status_code, 200)
        self.assertEqual(location.json(), {"status": "idle", "fix": None})
        self.assertEqual(route.json(), {"coordinates": [], "speedKmh": 50, "loop": False})

    def test_route_round_trip_and_paused_fix(self) -> None:
        posted = self.client.post(
            "/api/route",
            json={"type": "LineString", "coordinates": [[10.0, 20.0], [11.0, 20.0]]},
        )
        self.assertEqual(posted.status_code, 200)
        body = posted.json()
        self.assertEqual(set(body), {"coordinates", "speedKmh", "loop"})
        self.assertEqual(body["coordinates"], [[10.0, 20.0], [11.0, 20.0]])
        self.assertEqual(body["speedKmh"], 50)
        self.assertFalse(body["loop"])
        self.assertEqual(self.client.get("/api/route").json(), body)

        location = self.client.get("/api/location").json()
        self.assertEqual(location["status"], "paused")
        fix = location["fix"]
        self.assertEqual(set(fix), _FIX_FIELDS)
        self.assertEqual(fix["longitude"], 10.0)
        self.assertEqual(fix["latitude"], 20.0)
        self.assertAlmostEqual(fix["heading"], bearing_deg([10.0, 20.0], [11.0, 20.0]), delta=1e-6)
        self.assertEqual(fix["distanceMeters"], 0)
        self.assertEqual(fix["fraction"], 0)
        self.assertGreater(fix["totalMeters"], 0)
        self.assertEqual(fix["accuracyMeters"], 5)
        self.assertAlmostEqual(fix["speedKmh"], 50)
        self.assertAlmostEqual(fix["speedMps"], 50 * 1000 / 3600)
        self.assertRegex(fix["timestamp"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_coordinates_object_and_play_pause_reset(self) -> None:
        self.client.post("/api/route", json={"coordinates": [[0.0, 0.0], [1.0, 0.0]]})
        played = self.client.post("/api/simulation", json={"speedKmh": 36, "action": "play", "loop": True})
        self.assertEqual(played.status_code, 200)
        self.assertEqual(played.json()["status"], "playing")
        self.assertEqual(played.json()["fix"]["speedKmh"], 36)
        self.assertAlmostEqual(played.json()["fix"]["speedMps"], 10)
        self.assertTrue(self.client.get("/api/route").json()["loop"])
        self.assertLess(played.json()["fix"]["fraction"], 0.001)

        paused = self.client.post("/api/simulation", json={"action": "pause"})
        self.assertEqual(paused.json()["status"], "paused")
        self.assertIsNotNone(paused.json()["fix"])

        reset = self.client.post("/api/simulation", json={"action": "reset"})
        self.assertEqual(reset.json()["status"], "paused")
        self.assertEqual(reset.json()["fix"]["fraction"], 0)
        self.assertEqual(reset.json()["fix"]["distanceMeters"], 0)
        self.assertTrue(self.client.get("/api/route").json()["loop"])

    def test_play_without_route_stays_idle(self) -> None:
        response = self.client.post("/api/simulation", json={"action": "play"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        self.assertEqual(self.client.get("/api/location").json(), {"status": "idle", "fix": None})

    def test_invalid_route_and_action(self) -> None:
        bad = self.client.post("/api/route", json={"type": "Polygon", "coordinates": []})
        self.assertEqual(bad.status_code, 400)
        out = self.client.post("/api/route", json={"coordinates": [[0, 91]]})
        self.assertEqual(out.status_code, 400)
        missing = self.client.post("/api/simulation", json={"speedKmh": 10})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(self.client.get("/api/route").json()["speedKmh"], 50)

    def test_empty_route_returns_to_idle(self) -> None:
        self.client.post("/api/route", json={"coordinates": [[0.0, 0.0], [1.0, 0.0]]})
        cleared = self.client.post("/api/route", json={"coordinates": []})
        self.assertEqual(cleared.json()["coordinates"], [])
        self.assertEqual(self.client.get("/api/location").json(), {"status": "idle", "fix": None})

    def test_gpx_contains_track_points(self) -> None:
        self.client.post("/api/route", json={"coordinates": [[-97.5, 30.25], [-97.4, 30.3]]})
        response = self.client.get("/api/route.gpx")
        self.assertEqual(response.status_code, 200)
        self.assertIn("application/gpx+xml", response.headers["content-type"])
        text = response.text
        self.assertIn('version="1.1"', text)
        self.assertIn('lat="30.2500000"', text)
        self.assertIn('lon="-97.5000000"', text)
        self.assertIn('lat="30.3000000"', text)
        self.assertIn('lon="-97.4000000"', text)
        self.assertIn("<trkpt", text)

    def test_cors_is_open(self) -> None:
        response = self.client.get("/api/location", headers={"Origin": "http://localhost:3000"})
        self.assertEqual(response.headers.get("access-control-allow-origin"), "*")

    def test_websocket_sends_location_payload(self) -> None:
        with self.client.websocket_connect("/ws") as socket:
            self.assertEqual(socket.receive_json(), {"status": "idle", "fix": None})
        self.client.post("/api/route", json={"coordinates": [[0.0, 0.0], [0.0, 1.0]]})
        with self.client.websocket_connect("/ws") as socket:
            message = socket.receive_json()
        self.assertEqual(message["status"], "paused")
        self.assertAlmostEqual(message["fix"]["heading"], 0.0, delta=0.01)
        self.assertEqual(message["fix"]["latitude"], 0.0)
        self.assertEqual(message["fix"]["longitude"], 0.0)

    def test_replacing_the_route_pauses_at_the_start(self) -> None:
        self.client.post("/api/route", json={"coordinates": [[0.0, 0.0], [1.0, 0.0]]})
        self.client.post("/api/simulation", json={"action": "play"})
        replaced = self.client.post("/api/route", json={"coordinates": [[5.0, 6.0], [5.0, 7.0]]})
        self.assertEqual(replaced.json()["coordinates"][0], [5.0, 6.0])
        location = self.client.get("/api/location").json()
        self.assertEqual(location["status"], "paused")
        self.assertEqual(location["fix"]["longitude"], 5.0)
        self.assertEqual(location["fix"]["latitude"], 6.0)
        self.assertEqual(location["fix"]["fraction"], 0)


if __name__ == "__main__":
    unittest.main()
