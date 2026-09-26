"""Playback state for a route. Movement is geodesic and time-based."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from route_sim.geo import route_length_m, sample_at, speed_mps

DEFAULT_SPEED_KMH = 50.0
DEFAULT_ACCURACY_M = 5.0
TICK_SECONDS = 1.0
_MAX_SPEED_KMH = 2000.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Simulator:
    def __init__(self) -> None:
        self.coordinates: list[list[float]] = []
        self.speed_kmh = DEFAULT_SPEED_KMH
        self.loop = False
        self.playing = False
        self._along_m = 0.0
        self._t0 = time.monotonic()

    def reset_all(self) -> None:
        self.coordinates = []
        self.speed_kmh = DEFAULT_SPEED_KMH
        self.loop = False
        self.playing = False
        self._along_m = 0.0
        self._t0 = time.monotonic()

    def total_m(self) -> float:
        return route_length_m(self.coordinates)

    def set_route(self, coordinates: list[list[float]]) -> None:
        self.coordinates = [[float(lng), float(lat)] for lng, lat in coordinates]
        self.playing = False
        self._along_m = 0.0
        self._t0 = time.monotonic()

    def _raw_along(self) -> float:
        if not self.playing:
            return self._along_m
        return self._along_m + speed_mps(self.speed_kmh) * (time.monotonic() - self._t0)

    def display_distance(self) -> float:
        total = self.total_m()
        raw = self._raw_along()
        if total <= 0:
            return 0.0
        if self.playing and self.loop:
            return raw % total
        if raw >= total:
            return total
        if raw < 0:
            return 0.0
        return raw

    def finish_if_complete(self) -> bool:
        if not self.playing or self.loop:
            return False
        total = self.total_m()
        if total <= 0:
            self.playing = False
            self._along_m = 0.0
            return True
        if self._raw_along() >= total:
            self._along_m = total
            self.playing = False
            return True
        return False

    def _rebase(self, along_m: float) -> None:
        self._along_m = along_m
        self._t0 = time.monotonic()

    def set_speed(self, speed_kmh: float) -> None:
        if isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float)):
            raise ValueError("speedKmh must be a number")
        speed = float(speed_kmh)
        if speed != speed or speed < 0 or speed > _MAX_SPEED_KMH:
            raise ValueError("speedKmh must be between 0 and 2000")
        along = self.display_distance()
        self.speed_kmh = speed
        self._rebase(along)

    def set_loop(self, loop: bool) -> None:
        if not isinstance(loop, bool):
            raise ValueError("loop must be a boolean")
        along = self.display_distance()
        self.loop = loop
        self._rebase(along)

    def play(self) -> None:
        total = self.total_m()
        if len(self.coordinates) < 2 or total <= 0:
            raise ValueError("Route needs at least two distinct points before playing.")
        if self.playing:
            self._along_m = self.display_distance()
        elif not self.loop and self._along_m >= total:
            self._along_m = 0.0
        self._t0 = time.monotonic()
        self.playing = True

    def pause(self) -> None:
        if not self.coordinates:
            self.playing = False
            return
        self._along_m = self.display_distance()
        self.playing = False

    def reset(self) -> None:
        self.playing = False
        self._along_m = 0.0
        self._t0 = time.monotonic()

    def apply(self, action: str, speed_kmh: float | None = None, loop: bool | None = None) -> None:
        if action not in ("play", "pause", "reset"):
            raise ValueError('action must be "play", "pause", or "reset"')
        if speed_kmh is not None:
            if isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float)):
                raise ValueError("speedKmh must be a number")
            speed = float(speed_kmh)
            if speed != speed or speed < 0 or speed > _MAX_SPEED_KMH:
                raise ValueError("speedKmh must be between 0 and 2000")
        if loop is not None and not isinstance(loop, bool):
            raise ValueError("loop must be a boolean")
        if action == "play" and (len(self.coordinates) < 2 or self.total_m() <= 0):
            raise ValueError("Route needs at least two distinct points before playing.")
        if speed_kmh is not None:
            self.set_speed(speed_kmh)
        if loop is not None:
            self.set_loop(loop)
        if action == "play":
            self.play()
        elif action == "pause":
            self.pause()
        else:
            self.reset()

    def snapshot(self) -> dict:
        if not self.coordinates:
            return {"status": "idle", "fix": None}
        pose = sample_at(self.coordinates, self.display_distance())
        return {
            "status": "playing" if self.playing else "paused",
            "fix": {
                "latitude": pose.latitude,
                "longitude": pose.longitude,
                "heading": pose.heading,
                "speedMps": speed_mps(self.speed_kmh),
                "speedKmh": self.speed_kmh,
                "accuracyMeters": DEFAULT_ACCURACY_M,
                "timestamp": utc_now(),
                "distanceMeters": pose.distance_m,
                "totalMeters": pose.total_m,
                "fraction": pose.fraction,
            },
        }

    def capture(self) -> tuple[dict, bool]:
        changed = self.finish_if_complete()
        return self.snapshot(), changed

    def route_payload(self) -> dict:
        return {
            "coordinates": [list(pair) for pair in self.coordinates],
            "speedKmh": self.speed_kmh,
            "loop": self.loop,
        }
