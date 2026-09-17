/* Offline USGS tile + overlay store for the iOS / Play Store WebViews.
   Esri and public OSM tiles are never written here (provider terms). */
(function (global) {
  const DB_NAME = "wellnav-offline";
  const DB_VERSION = 1;
  const TILES = "tiles";
  const OVERLAYS = "overlays";
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

  async function usage() {
    const tiles = await tileCount();
    let bytes = tiles * 28000;
    if (navigator.storage && navigator.storage.estimate) {
      try {
        const est = await navigator.storage.estimate();
        if (est && est.usage) bytes = est.usage;
      } catch {
        /* ignore */
      }
    }
    return { tiles, bytes };
  }

  async function clear() {
    const db = await openDb();
    const tx = db.transaction([TILES, OVERLAYS], "readwrite");
    tx.objectStore(TILES).clear();
    tx.objectStore(OVERLAYS).clear();
    await txDone(tx);
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
    putOverlay,
    getOverlay,
    downloadArea,
    cancelDownload,
    isOnline,
    cacheableBasemap,
  };
})(window);
