/* Offline USGS tile + overlay store for the iOS / Play Store WebViews.
   Esri and public OSM tiles are never written here (provider terms). */
(function (global) {
  const DB_NAME = "wellnav-offline";
  const DB_VERSION = 2;
  const TILES = "tiles";
  const OVERLAYS = "overlays";
  const ROUTES = "routes";
  const MIN_Z = 6;
  const MAX_Z = 16;
  const MAX_PACK_TILES = 1800;
  const WEST = -110;
  const SOUTH = 25;
  const EAST = -88;
  const NORTH = 37.5;

  let dbPromise = null;
  let downloadAbort = null;

  function openDb() {
    if (dbPromise) return dbPromise;
    dbPromise = new Promise((resolve, reject) => {
      const req = indexedDB.open(DB_NAME, DB_VERSION);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains(TILES)) {
          db.createObjectStore(TILES, { keyPath: "key" });
        }
        if (!db.objectStoreNames.contains(OVERLAYS)) {
          db.createObjectStore(OVERLAYS, { keyPath: "id" });
        }
        if (!db.objectStoreNames.contains(ROUTES)) {
          db.createObjectStore(ROUTES, { keyPath: "id" });
        }
      };
      req.onsuccess = () => resolve(req.result);
      req.onerror = () => reject(req.error);
    });
    return dbPromise;
  }

  function txDone(tx) {
    return new Promise((resolve, reject) => {
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
      tx.onabort = () => reject(tx.error);
    });
  }

  function latLonToTile(lat, lon, zoom) {
    const clamped = Math.min(85.05112878, Math.max(-85.05112878, lat));
    const n = 2 ** zoom;
    const x = Math.floor(((lon + 180) / 360) * n);
    const latRad = (clamped * Math.PI) / 180;
    const y = Math.floor(
      ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n
    );
    return [Math.max(0, Math.min(n - 1, x)), Math.max(0, Math.min(n - 1, y))];
  }

  function tilesInBounds(west, south, east, north, zoom) {
    if (east < west) {
      const t = west;
      west = east;
      east = t;
    }
    if (north < south) {
      const t = south;
      south = north;
      north = t;
    }
    let [x0, y0] = latLonToTile(north, west, zoom);
    let [x1, y1] = latLonToTile(south, east, zoom);
    if (x1 < x0) {
      const t = x0;
      x0 = x1;
      x1 = t;
    }
    if (y1 < y0) {
      const t = y0;
      y0 = y1;
      y1 = t;
    }
    const tiles = [];
    for (let x = x0; x <= x1; x += 1) {
      for (let y = y0; y <= y1; y += 1) {
        tiles.push({ z: zoom, x, y });
      }
    }
    return tiles;
  }

  function countTiles(west, south, east, north, minZ, maxZ) {
    let total = 0;
    for (let z = minZ; z <= maxZ; z += 1) {
      total += tilesInBounds(west, south, east, north, z).length;
    }
    return total;
  }

  function packZooms(viewZoom) {
    const z = Math.round(Number(viewZoom) || 12);
    return [Math.max(MIN_Z, z - 2), Math.min(MAX_Z, Math.max(z + 2, 13))];
  }

  function tileKey(z, x, y) {
    return `usgs/${z}/${x}/${y}`;
  }

  async function getTile(key) {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const req = db.transaction(TILES, "readonly").objectStore(TILES).get(key);
      req.onsuccess = () => resolve(req.result ? req.result.blob : null);
      req.onerror = () => reject(req.error);
    });
  }

  async function putTile(key, blob) {
    const db = await openDb();
    const tx = db.transaction(TILES, "readwrite");
    tx.objectStore(TILES).put({ key, blob, savedAt: Date.now() });
    await txDone(tx);
  }

  async function tileCount() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const req = db.transaction(TILES, "readonly").objectStore(TILES).count();
      req.onsuccess = () => resolve(req.result || 0);
      req.onerror = () => reject(req.error);
    });
  }

  async function clear() {
    const db = await openDb();
    const tx = db.transaction([TILES, OVERLAYS, ROUTES], "readwrite");
    tx.objectStore(TILES).clear();
    tx.objectStore(OVERLAYS).clear();
    tx.objectStore(ROUTES).clear();
    await txDone(tx);
  }

  async function saveRoutes(routes) {
    const db = await openDb();
    const tx = db.transaction(ROUTES, "readwrite");
    const store = tx.objectStore(ROUTES);
    store.clear();
    (routes || []).forEach((route, order) => {
      if (!route || !route.id) return;
      store.put({ ...route, order, savedAt: Date.now() });
    });
    await txDone(tx);
  }

  async function loadRoutes() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const req = db.transaction(ROUTES, "readonly").objectStore(ROUTES).getAll();
      req.onsuccess = () => {
        const rows = req.result || [];
        rows.sort((a, b) => (a.order ?? 0) - (b.order ?? 0));
        resolve(rows);
      };
      req.onerror = () => reject(req.error);
    });
  }

  async function deleteRoute(id) {
    const db = await openDb();
    const tx = db.transaction(ROUTES, "readwrite");
    tx.objectStore(ROUTES).delete(id);
    await txDone(tx);
  }

  function distanceMeters(lat1, lon1, lat2, lon2) {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const phi1 = toRad(lat1);
    const phi2 = toRad(lat2);
    const dphi = toRad(lat2 - lat1);
    const dlmb = toRad(lon2 - lon1);
    const a =
      Math.sin(dphi / 2) ** 2 +
      Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlmb / 2) ** 2;
    return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(Math.max(0, a))));
  }

  function bearingDeg(lat1, lon1, lat2, lon2) {
    const toRad = (deg) => (deg * Math.PI) / 180;
    const phi1 = toRad(lat1);
    const phi2 = toRad(lat2);
    const dlmb = toRad(lon2 - lon1);
    const y = Math.sin(dlmb) * Math.cos(phi2);
    const x =
      Math.cos(phi1) * Math.sin(phi2) -
      Math.sin(phi1) * Math.cos(phi2) * Math.cos(dlmb);
    return (Math.atan2(y, x) * 180) / Math.PI;
  }

  function directCoordinates(lat1, lon1, lat2, lon2) {
    const dist = distanceMeters(lat1, lon1, lat2, lon2);
    const steps = dist < 1 ? 1 : Math.max(1, Math.min(64, Math.ceil(dist / 400)));
    const coords = [];
    for (let i = 0; i <= steps; i += 1) {
      const t = i / steps;
      coords.push([lon1 + (lon2 - lon1) * t, lat1 + (lat2 - lat1) * t]);
    }
    return coords;
  }

  function routeProgress(coordinates, lat, lon) {
    const line = [];
    (coordinates || []).forEach((pair) => {
      if (!Array.isArray(pair) || pair.length < 2) return;
      const pointLon = Number(pair[0]);
      const pointLat = Number(pair[1]);
      if (Number.isFinite(pointLat) && Number.isFinite(pointLon)) {
        line.push({ lat: pointLat, lon: pointLon });
      }
    });
    if (!line.length || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
    if (line.length === 1) {
      const off = distanceMeters(lat, lon, line[0].lat, line[0].lon);
      return {
        remainingM: off,
        alongM: 0,
        totalM: 0,
        offRouteM: off,
        snapLat: line[0].lat,
        snapLon: line[0].lon,
        guideLat: line[0].lat,
        guideLon: line[0].lon,
        snapIndex: 0,
      };
    }
    const mLat = 111320;
    const mLon = 111320 * Math.cos((lat * Math.PI) / 180);
    const xy = (point) => [(point.lon - lon) * mLon, (point.lat - lat) * mLat];
    let bestDist = Infinity;
    let bestAlong = 0;
    let bestSnap = line[0];
    let bestIndex = 0;
    let traveled = 0;
    for (let i = 0; i < line.length - 1; i += 1) {
      const a = xy(line[i]);
      const b = xy(line[i + 1]);
      const abx = b[0] - a[0];
      const aby = b[1] - a[1];
      const len2 = abx * abx + aby * aby;
      const len = Math.sqrt(len2);
      let t = 0;
      if (len2 > 0) {
        t = (-a[0] * abx + -a[1] * aby) / len2;
        t = Math.max(0, Math.min(1, t));
      }
      const sx = a[0] + abx * t;
      const sy = a[1] + aby * t;
      const dist = Math.sqrt(sx * sx + sy * sy);
      if (dist < bestDist) {
        bestDist = dist;
        bestAlong = traveled + len * t;
        bestIndex = i;
        bestSnap = { lat: lat + sy / mLat, lon: lon + sx / mLon };
      }
      traveled += len;
    }
    const total = traveled;
    const targetAlong = Math.min(total, bestAlong + 70);
    let guide = line[line.length - 1];
    let walked = 0;
    for (let i = 0; i < line.length - 1; i += 1) {
      const seg = distanceMeters(line[i].lat, line[i].lon, line[i + 1].lat, line[i + 1].lon);
      if (walked + seg >= targetAlong || i === line.length - 2) {
        const t = seg > 0 ? Math.max(0, Math.min(1, (targetAlong - walked) / seg)) : 0;
        guide = {
          lat: line[i].lat + (line[i + 1].lat - line[i].lat) * t,
          lon: line[i].lon + (line[i + 1].lon - line[i].lon) * t,
        };
        break;
      }
      walked += seg;
    }
    return {
      remainingM: Math.max(0, total - bestAlong),
      alongM: bestAlong,
      totalM: total,
      offRouteM: bestDist,
      snapLat: bestSnap.lat,
      snapLon: bestSnap.lon,
      guideLat: guide.lat,
      guideLon: guide.lon,
      snapIndex: bestIndex,
    };
  }

  function directRoutes(origin, destinations) {
    return (destinations || []).map((dest) => {
      const lat = Number(dest.lat);
      const lon = Number(dest.lon);
      return {
        id: dest.id,
        label: dest.label || "Pinned location",
        kind: dest.kind || "pin",
        lat,
        lon,
        origin: { lat: origin.lat, lon: origin.lon },
        coordinates: directCoordinates(origin.lat, origin.lon, lat, lon),
        distance_m: distanceMeters(origin.lat, origin.lon, lat, lon),
        duration_s: null,
        bearing: bearingDeg(origin.lat, origin.lon, lat, lon),
        mode: "direct",
      };
    });
  }

  async function routeCount() {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const req = db.transaction(ROUTES, "readonly").objectStore(ROUTES).count();
      req.onsuccess = () => resolve(req.result || 0);
      req.onerror = () => reject(req.error);
    });
  }

  async function usage() {
    const tiles = await tileCount();
    let routes = 0;
    try {
      routes = await routeCount();
    } catch {
      routes = 0;
    }
    let bytes = tiles * 28000;
    if (navigator.storage && navigator.storage.estimate) {
      try {
        const est = await navigator.storage.estimate();
        if (est && est.usage) bytes = est.usage;
      } catch {
        /* ignore */
      }
    }
    return { tiles, routes, bytes };
  }

  async function putOverlay(kind, payload, bounds) {
    const db = await openDb();
    const tx = db.transaction(OVERLAYS, "readwrite");
    tx.objectStore(OVERLAYS).put({
      id: kind,
      kind,
      payload,
      west: bounds.west,
      south: bounds.south,
      east: bounds.east,
      north: bounds.north,
      savedAt: Date.now(),
    });
    await txDone(tx);
  }

  async function getOverlay(kind, bounds) {
    const db = await openDb();
    const row = await new Promise((resolve, reject) => {
      const req = db.transaction(OVERLAYS, "readonly").objectStore(OVERLAYS).get(kind);
      req.onsuccess = () => resolve(req.result || null);
      req.onerror = () => reject(req.error);
    });
    if (!row || !row.payload) return null;
    if (!bounds) return row.payload;
    const overlap = !(
      bounds.east < row.west ||
      bounds.west > row.east ||
      bounds.north < row.south ||
      bounds.south > row.north
    );
    return overlap ? row.payload : null;
  }

  function cancelDownload() {
    if (downloadAbort) downloadAbort.abort();
  }

  async function downloadArea(bounds, opts) {
    const west = Number(bounds.west);
    const south = Number(bounds.south);
    const east = Number(bounds.east);
    const north = Number(bounds.north);
    if (![west, south, east, north].every(Number.isFinite)) {
      throw new Error("Invalid map bounds.");
    }
    const viewZ = opts && opts.zoom != null ? opts.zoom : 12;
    const [minZ, maxZ] = packZooms(viewZ);
    const n = countTiles(west, south, east, north, minZ, maxZ);
    if (n < 1) throw new Error("Nothing to save in this view.");
    if (n > MAX_PACK_TILES) {
      throw new Error(`This view needs ${n.toLocaleString()} tiles. Zoom in and save a smaller area.`);
    }
    downloadAbort = new AbortController();
    const signal = downloadAbort.signal;
    const onProgress = opts && opts.onProgress ? opts.onProgress : () => {};
    let done = 0;
    try {
      for (let z = minZ; z <= maxZ; z += 1) {
        const tiles = tilesInBounds(west, south, east, north, z);
        for (const tile of tiles) {
          if (signal.aborted) throw new DOMException("Aborted", "AbortError");
          const key = tileKey(tile.z, tile.x, tile.y);
          const have = await getTile(key);
          if (!have) {
            const url = `/offline/tiles/${tile.z}/${tile.y}/${tile.x}`;
            const resp = await fetch(url, { signal, credentials: "same-origin" });
            if (!resp.ok) throw new Error(`Tile download failed (${resp.status}).`);
            await putTile(key, await resp.blob());
          }
          done += 1;
          onProgress({ done, total: n, zoom: z });
        }
      }
    } finally {
      downloadAbort = null;
    }
    return { tiles: n, minZ, maxZ };
  }

  async function downloadTiles(tiles, opts) {
    const list = Array.isArray(tiles) ? tiles : [];
    if (!list.length) return { tiles: 0 };
    downloadAbort = new AbortController();
    const signal = downloadAbort.signal;
    const onProgress = opts && opts.onProgress ? opts.onProgress : () => {};
    let done = 0;
    try {
      for (const tile of list) {
        if (signal.aborted) throw new DOMException("Aborted", "AbortError");
        const z = Number(tile.z);
        const x = Number(tile.x);
        const y = Number(tile.y);
        if (![z, x, y].every(Number.isFinite)) {
          done += 1;
          continue;
        }
        const key = tileKey(z, x, y);
        const have = await getTile(key);
        if (!have) {
          const url = `/offline/tiles/${z}/${y}/${x}`;
          const resp = await fetch(url, { signal, credentials: "same-origin" });
          if (resp.status === 404) {
            done += 1;
            onProgress({ done, total: list.length, zoom: z });
            continue;
          }
          if (!resp.ok) throw new Error(`Tile download failed (${resp.status}).`);
          await putTile(key, await resp.blob());
        }
        done += 1;
        onProgress({ done, total: list.length, zoom: z });
      }
    } finally {
      downloadAbort = null;
    }
    return { tiles: list.length };
  }

  function isOnline() {
    return navigator.onLine !== false;
  }

  function cacheableBasemap(key) {
    return key === "usgs";
  }

  global.WellnavOffline = {
    MIN_Z,
    MAX_Z,
    MAX_PACK_TILES,
    WEST,
    SOUTH,
    EAST,
    NORTH,
    tileKey,
    tilesInBounds,
    countTiles,
    packZooms,
    getTile,
    putTile,
    usage,
    clear,
    saveRoutes,
    loadRoutes,
    deleteRoute,
    directRoutes,
    routeProgress,
    distanceMeters,
    bearingDeg,
    putOverlay,
    getOverlay,
    downloadArea,
    downloadTiles,
    cancelDownload,
    isOnline,
    cacheableBasemap,
  };
})(window);
