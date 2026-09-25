"""WGS84 geodesic distance, bearing, and polyline interpolation.

Coordinates are ``[lng, lat]`` pairs.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from pyproj import Geod

_GEOD = Geod(ellps="WGS84")
_EARTH_EQUATOR_M = 6378137.0


def speed_mps(speed_kmh: float) -> float:
    return (float(speed_kmh) * 1000.0) / 3600.0


def geodesic_distance_m(a: Sequence[float], b: Sequence[float]) -> float:
    """Distance in meters between two ``[lng, lat]`` points."""
    lng1, lat1 = float(a[0]), float(a[1])
    lng2, lat2 = float(b[0]), float(b[1])
    _forward, _back, distance = _GEOD.inv(lng1, lat1, lng2, lat2)
    if not math.isfinite(distance) or distance < 0:
        return 0.0
    return float(distance)


def bearing_deg(a: Sequence[float], b: Sequence[float]) -> float:
    """Initial bearing in degrees, clockwise from north, in ``[0, 360)``."""
    lng1, lat1 = float(a[0]), float(a[1])
    lng2, lat2 = float(b[0]), float(b[1])
    if geodesic_distance_m(a, b) < 1e-6:
        return 0.0
    azimuth, _back, distance = _GEOD.inv(lng1, lat1, lng2, lat2)
    if not math.isfinite(azimuth) or distance < 1e-6:
        return 0.0
    bearing = float(azimuth) % 360.0
    if bearing >= 360.0:
        return 0.0
    return bearing


def destination(lng: float, lat: float, bearing: float, distance_m: float) -> tuple[float, float]:
    """Point reached by traveling ``distance_m`` from ``(lng, lat)`` on ``bearing``."""
    if distance_m <= 0:
        return float(lng), float(lat)
    lng2, lat2, _back = _GEOD.fwd(float(lng), float(lat), float(bearing), float(distance_m))
    return float(lng2), float(lat2)


def cumulative_distances(coordinates: Sequence[Sequence[float]]) -> list[float]:
    totals = [0.0]
    for index in range(1, len(coordinates)):
        totals.append(totals[-1] + geodesic_distance_m(coordinates[index - 1], coordinates[index]))
    return totals


def route_length_m(coordinates: Sequence[Sequence[float]]) -> float:
    if len(coordinates) < 2:
        return 0.0
    return cumulative_distances(coordinates)[-1]


def _segment_bearing(coordinates: Sequence[Sequence[float]], start: int, step: int) -> float:
    index = start
    while 0 <= index + step < len(coordinates):
        nxt = index + step
        if geodesic_distance_m(coordinates[index], coordinates[nxt]) > 1e-3:
            origin, dest = (index, nxt) if step > 0 else (nxt, index)
            return bearing_deg(coordinates[origin], coordinates[dest])
        index += step
    return 0.0


@dataclass(frozen=True)
class Pose:
    latitude: float
    longitude: float
    heading: float
    distance_m: float
    total_m: float
    fraction: float


def sample_at(coordinates: Sequence[Sequence[float]], distance_m: float) -> Pose:
    """Pose at ``distance_m`` along the polyline, clamped to the route."""
    if not coordinates:
        raise ValueError("Route has no points")
    lng0, lat0 = float(coordinates[0][0]), float(coordinates[0][1])
    total = route_length_m(coordinates)
    if len(coordinates) == 1 or total <= 0 or distance_m <= 0:
        heading = _segment_bearing(coordinates, 0, 1) if total > 0 else 0.0
        return Pose(lat0, lng0, heading, 0.0, total, 0.0)

    dist = min(float(distance_m), total)
    if dist >= total:
        lng, lat = float(coordinates[-1][0]), float(coordinates[-1][1])
        heading = _segment_bearing(coordinates, len(coordinates) - 1, -1)
        return Pose(lat, lng, heading, total, total, 1.0)

    totals = cumulative_distances(coordinates)
    segment = len(coordinates) - 1
    for index in range(1, len(totals)):
        if dist < totals[index] or index == len(totals) - 1:
            segment = index
            break
    along = dist - totals[segment - 1]
    lng1, lat1 = float(coordinates[segment - 1][0]), float(coordinates[segment - 1][1])
    lng2, lat2 = float(coordinates[segment][0]), float(coordinates[segment][1])
    seg_len = totals[segment] - totals[segment - 1]
    if seg_len <= 1e-6:
        heading = _segment_bearing(coordinates, segment, 1)
        return Pose(lat1, lng1, heading, dist, total, dist / total)
    heading = bearing_deg((lng1, lat1), (lng2, lat2))
    if along <= 1e-6:
        return Pose(lat1, lng1, heading, dist, total, dist / total)
    lng, lat = destination(lng1, lat1, heading, min(along, seg_len))
    return Pose(lat, lng, heading, dist, total, dist / total)


def position_after(
    coordinates: Sequence[Sequence[float]],
    speed_kmh: float,
    elapsed_s: float,
    *,
    loop: bool = False,
) -> Pose:
    """Position after traveling ``elapsed_s`` seconds at a constant ``speed_kmh``."""
    if isinstance(speed_kmh, bool) or not isinstance(speed_kmh, (int, float)):
        raise ValueError("speed_kmh must be a number")
    if isinstance(elapsed_s, bool) or not isinstance(elapsed_s, (int, float)):
        raise ValueError("elapsed_s must be a number")
    if float(elapsed_s) < 0:
        raise ValueError("elapsed_s must be >= 0")
    if float(speed_kmh) < 0 or not math.isfinite(float(speed_kmh)):
        raise ValueError("speed_kmh must be >= 0")
    total = route_length_m(coordinates)
    travelled = speed_mps(float(speed_kmh)) * float(elapsed_s)
    if total <= 0 or travelled <= 0:
        distance = 0.0
    elif loop:
        distance = travelled % total
    else:
        distance = min(travelled, total)
    return sample_at(coordinates, distance)


def equator_degree_m() -> float:
    """Equatorial arc of one degree of longitude on the WGS84 ellipsoid."""
    return _EARTH_EQUATOR_M * math.pi / 180.0
