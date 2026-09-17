"""USGS tile math and validation for offline map packs.

Esri and public OSM tile servers are not used here. Their terms do not allow
bulk caching. USGS The National Map imagery/topo tiles are public domain.
"""

from __future__ import annotations

import math

USGS_TILE_TEMPLATE = (
    "https://basemap.nationalmap.gov/arcgis/rest/services/"
    "USGSImageryTopo/MapServer/tile/{z}/{y}/{x}"
)
MIN_ZOOM = 6
MAX_ZOOM = 16
MAX_PACK_TILES = 1800
# Coverage we actually serve (TX · NM · OK · LA plus a small pad).
WEST, SOUTH, EAST, NORTH = -110.0, 25.0, -88.0, 37.5


def lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat = min(85.05112878, max(-85.05112878, lat))
    n = 2**zoom
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    lat_rad = math.radians(lat)
    y = int(
        math.floor(
            (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n
        )
    )
    return max(0, min(n - 1, x)), max(0, min(n - 1, y))


def tiles_in_bounds(
    west: float, south: float, east: float, north: float, zoom: int
) -> list[tuple[int, int, int]]:
    if east < west:
        west, east = east, west
    if north < south:
        south, north = north, south
    x0, y0 = lat_lon_to_tile(north, west, zoom)
    x1, y1 = lat_lon_to_tile(south, east, zoom)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    tiles: list[tuple[int, int, int]] = []
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            tiles.append((zoom, x, y))
    return tiles


def count_tiles(
    west: float, south: float, east: float, north: float, min_z: int, max_z: int
) -> int:
    total = 0
    for zoom in range(min_z, max_z + 1):
        total += len(tiles_in_bounds(west, south, east, north, zoom))
    return total


def pack_zooms(view_zoom: int) -> tuple[int, int]:
    z = int(view_zoom)
    return max(MIN_ZOOM, z - 2), min(MAX_ZOOM, max(z + 2, 13))


def validate_tile(z: int, x: int, y: int) -> bool:
    if z < MIN_ZOOM or z > MAX_ZOOM:
        return False
    n = 2**z
    if x < 0 or y < 0 or x >= n or y >= n:
        return False
    west, north = _tile_nw(x, y, z)
    east, south = _tile_nw(x + 1, y + 1, z)
    return not (east < WEST or west > EAST or north < SOUTH or south > NORTH)


def _tile_nw(x: int, y: int, z: int) -> tuple[float, float]:
    n = 2**z
    lon = x / n * 360.0 - 180.0
    lat_rad = math.atan(math.sinh(math.pi * (1 - 2 * y / n)))
    return lon, math.degrees(lat_rad)


def usgs_url(z: int, x: int, y: int) -> str:
    return USGS_TILE_TEMPLATE.format(z=z, x=x, y=y)
