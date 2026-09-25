"""GPX 1.1 export of the current route."""

from __future__ import annotations

from collections.abc import Sequence


def route_gpx(coordinates: Sequence[Sequence[float]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx version="1.1" creator="route-simulator" xmlns="http://www.topografix.com/GPX/1/1">',
        "  <trk>",
        "    <name>Route</name>",
        "    <trkseg>",
    ]
    for point in coordinates:
        lng, lat = float(point[0]), float(point[1])
        lines.append(f'      <trkpt lat="{lat:.7f}" lon="{lng:.7f}"></trkpt>')
    lines.extend(["    </trkseg>", "  </trk>", "</gpx>", ""])
    return "\n".join(lines)
