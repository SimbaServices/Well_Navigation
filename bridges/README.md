# Device bridges

Developer test doubles for a navigation app. They copy the route simulator's GPS fix into the app. They do not hide that fact or bypass location-integrity checks.

The simulator's `GET /api/location` returns `{"status":"idle"|"playing"|"paused","fix":null|Fix}`. A fix includes latitude, longitude, heading, speed in m/s (`speedMps`), accuracy in meters, and an ISO-8601 timestamp.

## Browser

Set the simulator origin first when it is not the page origin, then paste `geolocation-override.js` into the devtools console or load it before the app reads `navigator.geolocation`:

```html
<script>
  window.ROUTE_SIM_BASE_URL = "http://127.0.0.1:8787";
</script>
<script src="./bridges/geolocation-override.js"></script>
```

Leave `ROUTE_SIM_BASE_URL` unset to poll same-origin `/api/location`. The script polls about once a second and answers `getCurrentPosition` and `watchPosition` with `coords.latitude`, `coords.longitude`, `coords.accuracy`, `coords.speed` (m/s), and `coords.heading`. `clearWatch` stops one watch. If the simulator is idle or down, the page keeps running and those calls report position unavailable. A different origin only works if the simulator allows that page's origin.

## Android emulator

Start the emulator, then point the watcher at the simulator:

```sh
node bridges/adb-geo.mjs --base http://127.0.0.1:8787 --watch
```

When a fix exists, each pass runs `adb emu geo fix <longitude> <latitude>` (longitude first). Idle responses are skipped. Omit `--watch` to push one sample. If `adb` is missing or the command fails, the process prints the reason and exits non-zero. `ROUTE_SIM_BASE_URL` overrides the default `http://127.0.0.1:8787`. Importing `bridges/adb-geo.mjs` does not run adb.
