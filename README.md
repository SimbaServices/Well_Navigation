# Route simulator

Local map and API that move a GPS fix along a route for navigation tests. No API keys.

## Install

```bash
python -m pip install -r requirements.txt
```

## Run

```bash
python -m route_sim
```

The server listens on http://127.0.0.1:8765 and serves the map at `/`. `PORT` or `--port` changes the port. `HOST` or `--host` changes the bind address.

## API

`GET /api/location` returns `{"status":"idle"|"playing"|"paused","fix":null|Fix}`. A fix includes latitude, longitude, heading, speed in m/s and km/h, accuracy in meters, an ISO-8601 timestamp, and distance along the route. `POST /api/route` accepts a GeoJSON LineString or `{"coordinates":[[lng,lat],...]}`. `POST /api/simulation` takes `{"action":"play"|"pause"|"reset"}` and optional `speedKmh` and `loop`. `GET /api/route` returns the current route, `GET /api/route.gpx` downloads GPX 1.1, and WebSocket `/ws` sends the same JSON as `GET /api/location` about once a second. The default speed is 50 km/h.

The server package is described in [route_sim/README.md](route_sim/README.md). Browser and Android emulator test doubles are described in [bridges/README.md](bridges/README.md).
