"""Routes from the user to pinned map locations for an offline pack.

Driving geometry comes from the public OSRM demo when it answers. A direct
line is always available, including when that router is down. Tile lists stay
inside the USGS pack cap and the mapped states.
"""

from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from wellnav.offline_tiles import (
    MAX_PACK_TILES,
    EAST,
    NORTH,
    SOUTH,
    WEST,
    lat_lon_to_tile,
    validate_tile,
)

MAX_DESTINATIONS = 12
MAX_ROUTE_POINTS = 400
EARTH_M = 6_371_000.0
OSRM_TEMPLATE = (
    "https://router.project-osrm.org/route/v1/driving/"
    "{lon1},{lat1};{lon2},{lat2}?overview=full&geometries=geojson"
)
_ID = re.compile(r"^[A-Za-z0-9:._-]{1,80}$")
_KINDS = {"well", "pin", "pipeline", "disposal"}
CORRIDOR_ZOOMS = ((11, 1), (12, 1), (13, 1), (14, 0))
ENDPOINT_ZOOM = 15


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_M * math.asin(min(1.0, math.sqrt(max(0.0, a))))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlmb = math.radians(lon2 - lon1)
    y = math.sin(dlmb) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlmb)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def in_coverage(lat: float, lon: float) -> bool:
    return SOUTH <= lat <= NORTH and WEST <= lon <= EAST


def direct_line(lon1: float, lat1: float, lon2: float, lat2: float) -> list[tuple[float, float]]:
    dist = haversine_m(lat1, lon1, lat2, lon2)
    if dist < 1:
        return [(lon1, lat1), (lon2, lat2)]
    steps = max(1, min(64, int(math.ceil(dist / 400.0))))
    coords: list[tuple[float, float]] = []
    for i in range(steps + 1):
        t = i / steps
        coords.append((lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t))
    return coords


def simplify_line(coords: list[tuple[float, float]], max_points: int = MAX_ROUTE_POINTS) -> list[tuple[float, float]]:
    if len(coords) <= max_points:
        return list(coords)
    if max_points < 2:
        return [coords[0], coords[-1]]
    step = (len(coords) - 1) / (max_points - 1)
    picked = [coords[min(len(coords) - 1, int(round(i * step)))] for i in range(max_points)]
    picked[-1] = coords[-1]
    return picked


def parse_osrm(payload: object) -> dict | None:
    if not isinstance(payload, dict) or payload.get("code") != "Ok":
        return None
    routes = payload.get("routes")
    if not isinstance(routes, list) or not routes or not isinstance(routes[0], dict):
        return None
    route = routes[0]
    geom = route.get("geometry")
    if not isinstance(geom, dict) or geom.get("type") != "LineString":
        return None
    raw = geom.get("coordinates")
    if not isinstance(raw, list) or len(raw) < 2:
        return None
    coords: list[tuple[float, float]] = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None
        try:
            lon, lat = float(pair[0]), float(pair[1])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(lon) or not math.isfinite(lat):
            return None
        coords.append((lon, lat))
    try:
        distance_m = float(route.get("distance"))
    except (TypeError, ValueError):
        distance_m = 0.0
    duration_raw = route.get("duration")
    try:
        duration_s = None if duration_raw is None else float(duration_raw)
    except (TypeError, ValueError):
        duration_s = None
    return {
        "coordinates": simplify_line(coords),
        "distance_m": distance_m,
        "duration_s": duration_s,
    }


def fetch_osrm(lat1: float, lon1: float, lat2: float, lon2: float) -> dict | None:
    url = OSRM_TEMPLATE.format(
        lon1=f"{lon1:.6f}",
        lat1=f"{lat1:.6f}",
        lon2=f"{lon2:.6f}",
        lat2=f"{lat2:.6f}",
    )
    resp = requests.get(
        url,
        timeout=4,
        headers={"User-Agent": "WellNavigation/1.0 (offline routes; wellnav@simba.services)"},
    )
    if resp.status_code != 200:
        return None
    return parse_osrm(resp.json())


def _finite(value: object) -> float | None:
    try:
        num = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not math.isfinite(num):
        return None
    return num


def _point(raw: object, label: str) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError(f"{label} needs a location.")
    lat = _finite(raw.get("lat"))
    lon = _finite(raw.get("lon"))
    if lat is None or lon is None or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise ValueError(f"{label} needs a valid location.")
    return {"lat": lat, "lon": lon}


def parse_pack_request(body: object) -> tuple[dict[str, float], list[dict]]:
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object.")
    origin = _point(body.get("origin"), "Your location")
    if not in_coverage(origin["lat"], origin["lon"]):
        raise ValueError("Your location is outside Texas, New Mexico, Oklahoma, and Louisiana.")
    raw = body.get("destinations")
    if not isinstance(raw, list) or not raw:
        raise ValueError("Pin at least one location.")
    if len(raw) > MAX_DESTINATIONS:
        raise ValueError(f"Pin up to {MAX_DESTINATIONS} locations for one offline pack.")
    destinations: list[dict] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("Each pin needs a location.")
        ident = str(item.get("id") or "").strip()
        if not _ID.match(ident):
            raise ValueError("A pin id is not valid.")
        if ident in seen:
            continue
        seen.add(ident)
        point = _point(item, "A pin")
        if not in_coverage(point["lat"], point["lon"]):
            raise ValueError("A pin is outside the mapped states.")
        kind = str(item.get("kind") or "pin").strip().lower()
        if kind not in _KINDS:
            kind = "pin"
        label = str(item.get("label") or "Pinned location").replace("\n", " ").strip() or "Pinned location"
        destinations.append(
            {
                "id": ident,
                "label": label[:80],
                "kind": kind,
                "lat": point["lat"],
                "lon": point["lon"],
            }
        )
    if not destinations:
        raise ValueError("Pin at least one location.")
    return origin, destinations


def _tile_span_m(lat: float, zoom: int) -> float:
    clamped = max(-85.0, min(85.0, lat))
    return 40_075_016.686 * math.cos(math.radians(clamped)) / (2**zoom)


def _tiles_near(lat: float, lon: float, zoom: int, pad: int) -> set[tuple[int, int, int]]:
    x, y = lat_lon_to_tile(lat, lon, zoom)
    n = 2**zoom
    found: set[tuple[int, int, int]] = set()
    for dx in range(-pad, pad + 1):
        for dy in range(-pad, pad + 1):
            xx, yy = x + dx, y + dy
            if 0 <= xx < n and 0 <= yy < n:
                found.add((zoom, xx, yy))
    return found


def _sample_line(line: list[tuple[float, float]], step_m: float) -> list[tuple[float, float]]:
    if len(line) < 2:
        return list(line)
    samples = [line[0]]
    for (lat1, lon1), (lat2, lon2) in zip(line, line[1:]):
        dist = haversine_m(lat1, lon1, lat2, lon2)
        if dist <= step_m:
            samples.append((lat2, lon2))
            continue
        steps = int(math.ceil(dist / step_m))
        for i in range(1, steps + 1):
            t = i / steps
            samples.append((lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t))
    return samples


def _tiles_along(line: list[tuple[float, float]], zoom: int, pad: int) -> set[tuple[int, int, int]]:
    if not line:
        return set()
    step = max(150.0, _tile_span_m(line[0][0], zoom) / 2.0)
    found: set[tuple[int, int, int]] = set()
    for lat, lon in _sample_line(line, step):
        found.update(_tiles_near(lat, lon, zoom, pad))
    return found


def trim_tiles(tiles: list[tuple[int, int, int]], limit: int = MAX_PACK_TILES) -> tuple[list[tuple[int, int, int]], bool]:
    kept = list(tiles)
    truncated = False
    while len(kept) > limit:
        top = max(item[0] for item in kept)
        truncated = True
        if top <= 11:
            kept.sort()
            return kept[:limit], True
        kept = [item for item in kept if item[0] != top]
    return kept, truncated


def tiles_for_routes(origin: dict[str, float], routes: list[dict]) -> tuple[list[dict], bool]:
    lines: list[list[tuple[float, float]]] = []
    endpoints = [(float(origin["lat"]), float(origin["lon"]))]
    for route in routes:
        line: list[tuple[float, float]] = []
        for pair in route.get("coordinates") or []:
            if not isinstance(pair, (list, tuple)) or len(pair) < 2:
                continue
            lon, lat = float(pair[0]), float(pair[1])
            line.append((lat, lon))
        if not line:
            continue
        lines.append(line)
        endpoints.append((float(route["lat"]), float(route["lon"])))
        endpoints.append(line[0])
        endpoints.append(line[-1])
    found: set[tuple[int, int, int]] = set()
    for zoom, pad in CORRIDOR_ZOOMS:
        for line in lines:
            found.update(_tiles_along(line, zoom, pad))
    for lat, lon in endpoints:
        found.update(_tiles_near(lat, lon, ENDPOINT_ZOOM, 1))
    valid = [(z, x, y) for z, x, y in found if validate_tile(z, x, y)]
    kept, truncated = trim_tiles(valid)
    payload = [{"z": z, "x": x, "y": y} for z, x, y in sorted(kept)]
    return payload, truncated


def _route_record(origin: dict[str, float], dest: dict, coords: list[tuple[float, float]], distance_m: float, duration_s: float | None, mode: str) -> dict:
    return {
        "id": dest["id"],
        "label": dest["label"],
        "kind": dest["kind"],
        "lat": dest["lat"],
        "lon": dest["lon"],
        "origin": {"lat": origin["lat"], "lon": origin["lon"]},
        "coordinates": [[lon, lat] for lon, lat in coords],
        "distance_m": round(distance_m, 1),
        "duration_s": None if duration_s is None else round(float(duration_s), 1),
        "bearing": round(bearing_deg(origin["lat"], origin["lon"], dest["lat"], dest["lon"]), 1),
        "mode": mode,
    }


def _direct_record(origin: dict[str, float], dest: dict) -> dict:
    distance = haversine_m(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
    coords = direct_line(origin["lon"], origin["lat"], dest["lon"], dest["lat"])
    return _route_record(origin, dest, coords, distance, None, "direct")


def _driven_record(origin: dict[str, float], dest: dict, driven: dict) -> dict:
    coords = simplify_line(list(driven["coordinates"]))
    distance = float(driven.get("distance_m") or 0.0)
    if distance <= 0:
        distance = haversine_m(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
    return _route_record(origin, dest, coords, distance, driven.get("duration_s"), "driving")


def plan_offline_routes(origin: dict[str, float], destinations: list[dict], *, fetch_driving=None) -> dict:
    """Build one route per pin and the USGS tiles that cover those paths."""
    if fetch_driving is None:
        fetch_driving = fetch_osrm

    probe = next(
        (
            dest
            for dest in destinations
            if haversine_m(origin["lat"], origin["lon"], dest["lat"], dest["lon"]) >= 80
        ),
        None,
    )
    cached: dict[str, dict] = {}
    router_alive = False
    if fetch_driving and probe is not None:
        try:
            sample = fetch_driving(origin["lat"], origin["lon"], probe["lat"], probe["lon"])
        except Exception:
            sample = None
        if sample and sample.get("coordinates"):
            router_alive = True
            cached[probe["id"]] = sample

    def build(dest: dict) -> dict:
        distance = haversine_m(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
        driven = cached.get(dest["id"])
        if driven is None and router_alive and fetch_driving and distance >= 80:
            try:
                driven = fetch_driving(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
            except Exception:
                driven = None
        if driven and driven.get("coordinates"):
            return _driven_record(origin, dest, driven)
        return _direct_record(origin, dest)

    if router_alive and len(destinations) > 1:
        workers = min(4, len(destinations))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            routes = list(pool.map(build, destinations))
    else:
        routes = [build(dest) for dest in destinations]

    tiles, truncated = tiles_for_routes(origin, routes)
    return {"routes": routes, "tiles": tiles, "truncated": truncated}
