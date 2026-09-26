"""Parse a posted route into ``[lng, lat]`` coordinates."""

from __future__ import annotations

import math


def parse_coordinates(payload: dict) -> list[list[float]]:
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    kind = payload.get("type")
    if kind is not None and kind != "LineString":
        raise ValueError("GeoJSON type must be LineString")
    raw = payload.get("coordinates")
    if not isinstance(raw, list):
        raise ValueError('Expected a GeoJSON LineString or {"coordinates": [[lng, lat], ...]}')
    points: list[list[float]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            raise ValueError("Each coordinate must be [lng, lat]")
        if isinstance(item[0], bool) or isinstance(item[1], bool):
            raise ValueError("Each coordinate must be [lng, lat]")
        try:
            lng = float(item[0])
            lat = float(item[1])
        except (TypeError, ValueError) as exc:
            raise ValueError("Each coordinate must be [lng, lat]") from exc
        if not math.isfinite(lng) or not math.isfinite(lat):
            raise ValueError("Coordinates must be finite numbers")
        if not -180 <= lng <= 180 or not -90 <= lat <= 90:
            raise ValueError(f"Coordinate out of range: [{lng}, {lat}]")
        points.append([lng, lat])
    return points
