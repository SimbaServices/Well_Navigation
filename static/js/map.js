const BASE_LAYERS = {
  imagery: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles © Esri", maxZoom: 19 }
    ),
  streets: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles © Esri", maxZoom: 19 }
    ),
  topo: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}",
      { attribution: "Tiles © Esri", maxZoom: 19 }
    ),
  osm: () =>
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "© OpenStreetMap",
      maxZoom: 19,
    }),
  usgs: () =>
    L.tileLayer(
      "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",
      { attribution: "USGS The National Map", maxZoom: 16 }
    ),
};

const BASEMAP_LABELS = {
  imagery: "Esri Imagery",
  streets: "Esri Streets",
  topo: "Esri Topo",
  osm: "OpenStreetMap",
  usgs: "USGS Topo",
};

const STORAGE_KEY = "wellnav.mappedWells";
const ROUTE_PINS_KEY = "wellnav.routePins";
const MAX_ROUTE_PINS = 12;
const ROUTE_COLORS = ["#d4a017", "#7cb36a", "#7aa2e3", "#e0893a", "#c45c3a", "#6ec8d4"];
const VIEW_KEY = "wellnav.mapView";
const PANE_PREF = "wellnav.workspacePane";
const PHONE_PANE = "(max-width: 960px)";
const PIPELINE_PREF = "wellnav.pipelines";
const DISPOSAL_PREF = "wellnav.disposal";
const MAX_WELLS = 500;
const FIT_MAX_ZOOM = 16;
const PIPELINE_COLORS = {
  gas: "#e4c04a",
  crude: "#6fbf73",
  hvl: "#e0893a",
  product: "#7aa2e3",
  co2: "#6ec8d4",
  other: "#b7b7a8",
};

const MAP_CHROME_HTML = `
<div class="map-chrome" data-focus="idle">
  <div class="map-toolbar">
    <div class="map-actions">
      <label class="basemap-picker">Base layer
        <select id="basemap-select"></select>
      </label>
      <span class="overlay-with-tip">
        <label class="overlay-toggle"><input type="checkbox" id="pipeline-toggle"> Pipelines</label>
        <span class="info-tip">
          <button type="button" class="info-tip-btn" aria-expanded="false" aria-label="Pipeline colors">i</button>
          <span class="info-tip-pop" popover="manual" hidden role="tooltip">
            <span class="legend-key">
              <span><i class="swatch gas"></i>Gas</span>
              <span><i class="swatch crude"></i>Crude</span>
              <span><i class="swatch hvl"></i>HVL</span>
              <span><i class="swatch product"></i>Product</span>
              <span><i class="swatch other"></i>Other</span>
            </span>
          </span>
        </span>
      </span>
      <label class="overlay-toggle"><input type="checkbox" id="disposal-toggle"> Waste sites</label>
      <div class="offline-pack">
        <button type="button" class="ghost" id="offline-pin" aria-pressed="false">Pin spot</button>
        <button type="button" class="ghost" id="offline-save">Save for offline</button>
        <span class="info-tip">
          <button type="button" class="info-tip-btn" aria-expanded="false" aria-label="About offline maps">i</button>
          <span class="info-tip-pop" popover="manual" hidden role="tooltip">Pin one or more spots, wells, or sites. Save for offline stores a route from your location to each pin and the base layer selected at that moment. Choose USGS Topo. Esri and OpenStreetMap stay online-only.</span>
        </span>
        <button type="button" class="ghost" id="offline-clear" hidden>Clear saved maps</button>
        <p id="offline-status" class="muted"></p>
      </div>
    </div>
  </div>
  <div id="well-map" class="well-map"></div>
  <div class="map-details">
    <div class="map-head">
      <div>
        <h2 id="map-title">Map</h2>
        <p id="map-sub" class="muted"></p>
      </div>
    </div>
    <p id="pipeline-status" class="muted pipeline-status"></p>
    <div id="pipeline-owners" class="pipeline-owners" hidden></div>
    <p id="disposal-status" class="muted pipeline-status"></p>
    <ul id="mapped-list" class="mapped-list"></ul>
    <ul id="offline-routes" class="offline-routes" hidden aria-label="Routes to pinned locations"></ul>
    <div class="map-footer">
      <div id="map-coords" class="coord-bar"></div>
      <div id="nav-links" class="route-row"></div>
    </div>
  </div>
</div>`;

let map;
let activeBase;
let wellLayer;
let pipelineLayer;
let pipelineAbort;
let pipelineTimer = 0;
let pipelineKey = "";
let pipelineFocus = { p5: "", system: "", operator: "", highlightId: null };
let disposalLayer;
let disposalAbort;
let disposalTimer = 0;
let disposalKey = "";
let disposalFocus = null;
let disposalPopupPinned = false;
let disposalReloading = false;
let ignoreDisposalDismiss = false;
let mapSizeObserver = null;
const overlays = new Map();
let pipelinePin = null;
let pipelinePinMarker = null;
let pinChrome = false;
let routePinLayer = null;
let routeLineLayer = null;
let userLocationMarker = null;
let locationWatchId = null;
let lastUserLatLng = null;
let pinMode = false;
let savedRoutes = [];
let routesReady = null;
let navRouteId = null;
let navFollow = false;
let navPaused = false;
let navUserPicked = false;
let navSnappedToNearest = false;
let navRemainLine = null;
let navJoinLine = null;
let deviceHeading = null;
let deviceHeadingBound = false;
let lastFollowLatLng = null;

function searchInputEl() {
  return document.getElementById("q");
}

function searchInputIsActive() {
  const q = searchInputEl();
  return !!(q && document.activeElement === q);
}

function workspaceRoot() {
  return document.querySelector(".workspace");
}

function isPhoneWorkspace() {
  return window.matchMedia(PHONE_PANE).matches;
}

function showWorkspacePane(pane, { persist = true } = {}) {
  const root = workspaceRoot();
  if (!root) return;
  const next = pane === "map" ? "map" : "search";
  root.dataset.pane = next;
  root.querySelectorAll(".workspace-tab").forEach((tab) => {
    const on = tab.dataset.pane === next;
    tab.setAttribute("aria-selected", on ? "true" : "false");
    tab.tabIndex = on ? 0 : -1;
  });
  if (persist) {
    try {
      sessionStorage.setItem(PANE_PREF, next);
    } catch {
      /* ignore */
    }
  }
  if (next === "map") {
    window.setTimeout(() => {
      if (map) map.invalidateSize();
    }, 80);
  }
}

function revealMapOnPhone() {
  if (!isPhoneWorkspace()) return;
  if (searchInputIsActive()) return;
  showWorkspacePane("map");
}

function syncMapTabCount(store) {
  const count = document.getElementById("map-tab-count");
  if (!count) return;
  const n = store?.order?.length || 0;
  count.textContent = n ? String(n) : "";
  count.hidden = !n;
}

function initWorkspaceTabs() {
  const root = workspaceRoot();
  if (!root || root.dataset.tabsReady) return;
  root.dataset.tabsReady = "1";
  let saved = "search";
  try {
    saved = sessionStorage.getItem(PANE_PREF) || "search";
  } catch {
    saved = "search";
  }
  const requested = new URLSearchParams(window.location.search).get("pane");
  if (requested === "map" || requested === "search") saved = requested;
  if (document.querySelector("#well-map[data-api]")) saved = "map";
  showWorkspacePane(saved, { persist: false });
  root.querySelectorAll(".workspace-tab").forEach((tab) => {
    tab.addEventListener("click", () => showWorkspacePane(tab.dataset.pane));
  });
}

function emptyStore() {
  return { wells: {}, selected: null, order: [] };
}

function parseStore(raw) {
  if (!raw) return null;
  const data = JSON.parse(raw);
  if (!data || typeof data !== "object" || typeof data.wells !== "object" || !data.wells) {
    return null;
  }
  const order = Array.isArray(data.order)
    ? data.order.filter((api) => data.wells[api])
    : Object.keys(data.wells);
  const selected = data.selected && data.wells[data.selected] ? data.selected : null;
  return { wells: data.wells, selected, order };
}

function loadStore() {
  try {
    const sessionRaw = sessionStorage.getItem(STORAGE_KEY);
    if (sessionRaw) {
      const parsed = parseStore(sessionRaw);
      if (parsed) return parsed;
    }
  } catch {
    /* ignore */
  }
  try {
    const persisted = localStorage.getItem(STORAGE_KEY);
    const parsed = parseStore(persisted);
    if (parsed) {
      try {
        sessionStorage.setItem(STORAGE_KEY, persisted);
      } catch {
        /* ignore */
      }
      return parsed;
    }
  } catch {
    /* ignore */
  }
  return emptyStore();
}

function saveStore(store) {
  const raw = JSON.stringify(store);
  try {
    sessionStorage.setItem(STORAGE_KEY, raw);
  } catch {
    /* quota / private mode */
  }
  try {
    localStorage.setItem(STORAGE_KEY, raw);
  } catch {
    /* quota / private mode */
  }
}

function parseCoord(value) {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

function hasToe(well) {
  return well && Number.isFinite(well.toeLat) && Number.isFinite(well.toeLon);
}

function normalizeWell(raw) {
  const toeLat = parseCoord(raw.toeLat);
  const toeLon = parseCoord(raw.toeLon);
  return {
    api: String(raw.api || "").trim(),
    state: raw.state || "tx",
    name: raw.name || "",
    lease: raw.lease || "",
    county: raw.county || "",
    operator: raw.operator || "",
    lat: Number(raw.lat),
    lon: Number(raw.lon),
    toeLat,
    toeLon,
    status: raw.status || "",
  };
}

function wellFromDataset(ds) {
  if (!ds) return null;
  const api = String(ds.api || "").trim();
  const lat = parseCoord(ds.lat);
  const lon = parseCoord(ds.lon);
  if (!api || lat == null || lon == null) return null;
  return normalizeWell({
    api,
    state: ds.state || "tx",
    name: ds.name || ds.label || "",
    lease: ds.lease || "",
    county: ds.county || "",
    operator: ds.operator || "",
    lat,
    lon,
    toeLat: ds.toeLat,
    toeLon: ds.toeLon,
    status: ds.status || "",
  });
}

function wellFromButton(btn) {
  if (!btn) return null;
  return wellFromDataset(btn.dataset) || wellFromRow(btn.closest("tr.well-row"));
}

function wellFromRow(row) {
  return row ? wellFromDataset(row.dataset) : null;
}

function wellFromMapEl(el) {
  return el ? wellFromDataset(el.dataset) : null;
}

const API_PREFIX = { tx: "42", nm: "30", ok: "35", la: "17" };

function formatApi(api, state) {
  const digits = String(api || "").replace(/\D/g, "");
  if (digits.length === 10) return `${digits.slice(0, 2)}-${digits.slice(2, 5)}-${digits.slice(5)}`;
  if (digits.length === 8) {
    const prefix = API_PREFIX[String(state || "tx").toLowerCase()] || "42";
    return `${prefix}-${digits.slice(0, 3)}-${digits.slice(3)}`;
  }
  return String(api || "");
}

function wellSubtitle(well) {
  const parts = [formatApi(well.api, well.state)];
  if (well.lease) parts.push(well.lease);
  if (well.county) {
    const county = well.county.trim();
    parts.push(/county$/i.test(county) ? county : `${county} County`);
  }
  if (well.operator) parts.push(well.operator);
  return parts.join(" · ");
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function markNoCoords(btn) {
  if (!btn) return;
  btn.title = "No coordinates";
  const wasDisabled = btn.disabled;
  btn.disabled = true;
  window.setTimeout(() => {
    btn.disabled = wasDisabled;
  }, 900);
}

function upsertIntoStore(well, { select = false } = {}) {
  const store = loadStore();
  const isNew = !store.wells[well.api];
  if (isNew && store.order.length >= MAX_WELLS) {
    return { store, added: false, isNew: false };
  }
  const next = normalizeWell(well);
  const prev = store.wells[well.api];
  if (!next.status && prev && prev.status) next.status = prev.status;
  store.wells[well.api] = next;
  if (isNew) store.order.push(well.api);
  if (select || !store.selected) store.selected = well.api;
  saveStore(store);
  return { store, added: true, isNew };
}

function removeFromStore(api) {
  const store = loadStore();
  if (!store.wells[api]) return store;
  delete store.wells[api];
  store.order = store.order.filter((id) => id !== api);
  if (store.selected === api) {
    store.selected = store.order.length ? store.order[store.order.length - 1] : null;
  }
  saveStore(store);
  return store;
}

function preferredBasemap() {
  return localStorage.getItem("wellnav.basemap") || "imagery";
}

function selectedBasemap() {
  const select = document.getElementById("basemap-select");
  const key = select && select.value;
  if (key && BASE_LAYERS[key]) return key;
  const saved = preferredBasemap();
  return BASE_LAYERS[saved] ? saved : "usgs";
}

function basemapLabel(key) {
  return BASEMAP_LABELS[key] || "Base layer";
}

function makeUsgsLayer() {
  const offline = window.WellnavOffline;
  if (typeof L === "undefined") return null;
  if (!offline || typeof L.TileLayer !== "function") return BASE_LAYERS.usgs();
  const OfflineLayer = L.TileLayer.extend({
    createTile(coords, done) {
      const img = document.createElement("img");
      img.alt = "";
      const key = offline.tileKey(coords.z, coords.x, coords.y);
      const finish = (src) => {
        img.onload = () => done(null, img);
        img.onerror = () => done(new Error("tile"), img);
        img.src = src;
      };
      offline
        .getTile(key)
        .then((blob) => {
          if (blob) {
            finish(URL.createObjectURL(blob));
            return null;
          }
          if (!offline.isOnline()) {
            done(new Error("offline"), img);
            return null;
          }
          finish(this.getTileUrl(coords));
          return null;
        })
        .catch((err) => done(err, img));
      return img;
    },
  });
  return new OfflineLayer(
    "https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryTopo/MapServer/tile/{z}/{y}/{x}",
    { attribution: "USGS The National Map", maxZoom: 16, minZoom: 5 }
  );
}

function setBasemap(key, { persist = true } = {}) {
  if (!map || !BASE_LAYERS[key]) return;
  const offline = window.WellnavOffline;
  let next = key;
  if (offline && !offline.isOnline() && next !== "usgs") {
    next = "usgs";
  }
  if (activeBase) map.removeLayer(activeBase);
  activeBase = next === "usgs" ? makeUsgsLayer() : BASE_LAYERS[next]();
  if (activeBase) activeBase.addTo(map);
  if (persist && next === key) localStorage.setItem("wellnav.basemap", key);
  const select = document.getElementById("basemap-select");
  if (select && select.value !== next) select.value = next;
}

function persistMapView() {
  if (!map) return;
  try {
    const center = map.getCenter();
    localStorage.setItem(
      VIEW_KEY,
      JSON.stringify({ lat: center.lat, lon: center.lng, z: map.getZoom() })
    );
  } catch {
    /* ignore */
  }
}

function restoreMapView() {
  if (!map) return false;
  try {
    const raw = localStorage.getItem(VIEW_KEY);
    if (!raw) return false;
    const view = JSON.parse(raw);
    const lat = Number(view.lat);
    const lon = Number(view.lon);
    const z = Number(view.z);
    if (![lat, lon, z].every(Number.isFinite)) return false;
    map.setView([lat, lon], z);
    return true;
  } catch {
    return false;
  }
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setOfflineStatus(text) {
  const el = document.getElementById("offline-status");
  if (el) el.textContent = text || "";
}

function offlinePackSummary(info, online) {
  const routes = info && info.routes ? info.routes : 0;
  const tiles = info && info.tiles ? info.tiles : 0;
  if (!routes && !tiles) {
    return online ? "" : "No saved map on this device.";
  }
  const bits = [];
  if (routes) bits.push(`${routes} route${routes === 1 ? "" : "s"}`);
  if (tiles) bits.push(`${tiles.toLocaleString()} USGS tiles (${formatBytes(info.bytes)})`);
  return `${bits.join(" · ")} on this device.`;
}

async function refreshOfflineStatus() {
  const clearBtn = document.getElementById("offline-clear");
  const saveBtn = document.getElementById("offline-save");
  const offline = window.WellnavOffline;
  if (!offline) {
    setOfflineStatus("");
    return;
  }
  if (saveBtn && saveBtn.dataset.busy === "1") return;
  try {
    const info = await offline.usage();
    if (clearBtn) clearBtn.hidden = !(info.tiles || info.routes);
    setOfflineStatus(offlinePackSummary(info, offline.isOnline()));
  } catch {
    setOfflineStatus("");
  }
}

function loadRoutePins() {
  try {
    const raw = localStorage.getItem(ROUTE_PINS_KEY);
    const data = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(data)) return [];
    return data.filter(
      (pin) =>
        pin &&
        pin.id &&
        Number.isFinite(Number(pin.lat)) &&
        Number.isFinite(Number(pin.lon))
    );
  } catch {
    return [];
  }
}

function saveRoutePins(pins) {
  try {
    localStorage.setItem(ROUTE_PINS_KEY, JSON.stringify(pins));
  } catch {
    /* quota / private mode */
  }
}

function safeRouteId(prefix, raw) {
  const body = String(raw || "")
    .replace(/[^A-Za-z0-9._-]/g, "")
    .slice(0, 64);
  return `${prefix}:${body || "spot"}`.slice(0, 80);
}

function inOfflineCoverage(lat, lon) {
  const box = window.WellnavOffline;
  if (!box) return true;
  return lat >= box.SOUTH && lat <= box.NORTH && lon >= box.WEST && lon <= box.EAST;
}

function collectRouteDestinations() {
  const out = [];
  const seen = new Set();
  let omitted = 0;
  const push = (item) => {
    if (!item || seen.has(item.id)) return false;
    const lat = Number(item.lat);
    const lon = Number(item.lon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) return false;
    if (!inOfflineCoverage(lat, lon)) return false;
    seen.add(item.id);
    if (out.length >= MAX_ROUTE_PINS) {
      omitted += 1;
      return false;
    }
    out.push({
      id: item.id,
      kind: item.kind || "pin",
      label: String(item.label || "Pinned location").slice(0, 80),
      lat,
      lon,
    });
    return true;
  };
  const store = loadStore();
  const order = store.selected
    ? [store.selected, ...store.order.filter((id) => id !== store.selected)]
    : store.order.slice();
  order.forEach((api) => {
    const well = store.wells[api];
    if (!well) return;
    push({
      id: safeRouteId("well", api),
      kind: "well",
      label: well.name || formatApi(api),
      lat: well.lat,
      lon: well.lon,
    });
  });
  if (disposalFocus && Number.isFinite(disposalFocus.lat) && Number.isFinite(disposalFocus.lon)) {
    push({
      id: safeRouteId("disposal", disposalFocus.id || `${disposalFocus.lat},${disposalFocus.lon}`),
      kind: "disposal",
      label: disposalFocus.name || "Waste site",
      lat: disposalFocus.lat,
      lon: disposalFocus.lon,
    });
  }
  if (pipelinePin && Number.isFinite(pipelinePin.lat) && Number.isFinite(pipelinePin.lon)) {
    push({
      id: safeRouteId(
        "pipeline",
        `${Number(pipelinePin.lat).toFixed(5)}_${Number(pipelinePin.lon).toFixed(5)}`
      ),
      kind: "pipeline",
      label: pipelinePin.operator || pipelinePin.system || "Pipeline point",
      lat: pipelinePin.lat,
      lon: pipelinePin.lon,
    });
  }
  loadRoutePins().forEach((pin) => {
    push({
      id: pin.id,
      kind: "pin",
      label: pin.label || "Pinned spot",
      lat: pin.lat,
      lon: pin.lon,
    });
  });
  return { destinations: out, omitted };
}

function turnInstruction(relative) {
  const wrapped = ((Number(relative) % 360) + 360) % 360;
  if (wrapped <= 25 || wrapped >= 335) return "Continue straight";
  if (wrapped < 180) return `Turn right ${Math.round(wrapped)}°`;
  return `Turn left ${Math.round(360 - wrapped)}°`;
}

function compassLabel(deg) {
  const names = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  const wrapped = ((Number(deg) % 360) + 360) % 360;
  return names[Math.round(wrapped / 45) % 8];
}

function formatRouteDistance(meters) {
  const m = Number(meters);
  if (!Number.isFinite(m)) return "";
  const miles = m / 1609.344;
  if (miles < 0.1) return `${Math.max(1, Math.round(m * 3.28084))} ft`;
  if (miles < 10) return `${miles.toFixed(1)} mi`;
  return `${Math.round(miles)} mi`;
}

function formatRouteMinutes(seconds) {
  const s = Number(seconds);
  if (!Number.isFinite(s) || s <= 0) return "";
  const mins = Math.max(1, Math.round(s / 60));
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const rem = mins % 60;
  return rem ? `${h}h ${rem}m` : `${h}h`;
}

function userLocationIcon(heading, navigating) {
  const rot = Number.isFinite(heading) ? heading : 0;
  return L.divIcon({
    className: navigating ? "user-location-icon is-nav" : "user-location-icon",
    html: `<span class="user-location-dot" style="transform: rotate(${rot}deg)"></span>`,
    iconSize: navigating ? [28, 28] : [18, 18],
    iconAnchor: navigating ? [14, 14] : [9, 9],
  });
}

function routePinIcon() {
  return L.divIcon({
    className: "route-pin-icon",
    html: '<span class="route-pin-dot"></span>',
    iconSize: [18, 18],
    iconAnchor: [9, 9],
  });
}

function ensureRouteLayers() {
  if (!map) return;
  if (!map.getPane("offline-routes")) {
    map.createPane("offline-routes");
    map.getPane("offline-routes").style.zIndex = 430;
  }
  if (!routeLineLayer) routeLineLayer = L.layerGroup().addTo(map);
  if (!routePinLayer) routePinLayer = L.layerGroup().addTo(map);
}

function currentHeading() {
  const speed = lastUserLatLng && lastUserLatLng.speed;
  const gps = lastUserLatLng && lastUserLatLng.heading;
  if (Number.isFinite(gps) && gps >= 0 && (!Number.isFinite(speed) || speed > 0.8)) return gps;
  if (Number.isFinite(deviceHeading)) return deviceHeading;
  if (Number.isFinite(gps) && gps >= 0) return gps;
  return null;
}

function drawUserLocation() {
  if (!map || !lastUserLatLng || typeof L === "undefined") return;
  const ll = [lastUserLatLng.lat, lastUserLatLng.lon];
  const navigating = !!navRouteId;
  const heading = currentHeading();
  const key = `${navigating ? 1 : 0}:${heading == null ? "x" : Math.round(heading / 5)}`;
  if (!userLocationMarker) {
    userLocationMarker = L.marker(ll, {
      icon: userLocationIcon(heading, navigating),
      interactive: false,
      keyboard: false,
      zIndexOffset: 1200,
    }).addTo(map);
    userLocationMarker._headingKey = key;
  } else {
    userLocationMarker.setLatLng(ll);
    if (userLocationMarker._headingKey !== key) {
      userLocationMarker.setIcon(userLocationIcon(heading, navigating));
      userLocationMarker._headingKey = key;
    }
  }
}

function onLocationFix(pos) {
  const coords = pos && pos.coords ? pos.coords : {};
  lastUserLatLng = {
    lat: coords.latitude,
    lon: coords.longitude,
    heading: Number.isFinite(coords.heading) ? coords.heading : null,
    speed: Number.isFinite(coords.speed) ? coords.speed : null,
    accuracy: Number.isFinite(coords.accuracy) ? coords.accuracy : null,
  };
  drawUserLocation();
  if (!navigationAvailable()) {
    renderOfflineRouteList();
    return;
  }
  if (!navPaused && !navUserPicked && !navSnappedToNearest) {
    navSnappedToNearest = true;
    const nearest = nearestSavedRoute();
    if (nearest) {
      beginNavigation(nearest.id, { user: false });
      return;
    }
  }
  if (navRouteId) updateNavigationFrame();
  else renderOfflineRouteList();
}

function watchUserLocation() {
  if (!navigator.geolocation || locationWatchId != null) return;
  locationWatchId = navigator.geolocation.watchPosition(onLocationFix, onLocationWatchError, {
    enableHighAccuracy: true,
    maximumAge: 2000,
    timeout: 25000,
  });
}

function onLocationWatchError(err) {
  if (!navigationAvailable()) return;
  const detail =
    err && err.code === 1
      ? "Location permission is off."
      : "Waiting for a GPS fix. It still works without a connection.";
  setNavCopy(navRouteLabel() || "Saved route", detail);
}

function stopUserLocation() {
  if (locationWatchId != null && navigator.geolocation) {
    navigator.geolocation.clearWatch(locationWatchId);
  }
  locationWatchId = null;
  lastUserLatLng = null;
  if (userLocationMarker && map) map.removeLayer(userLocationMarker);
  userLocationMarker = null;
}

function navigationAvailable() {
  const offline = window.WellnavOffline;
  return !!(offline && !offline.isOnline() && savedRoutes.length);
}

function routeById(id) {
  return savedRoutes.find((route) => route.id === id) || null;
}

function navRouteLabel() {
  const route = routeById(navRouteId);
  return route ? route.label || "Saved route" : "";
}

function nearestSavedRoute() {
  if (!savedRoutes.length) return null;
  const offline = window.WellnavOffline;
  if (!lastUserLatLng || !offline) return savedRoutes[0];
  let best = savedRoutes[0];
  let bestDistance = Infinity;
  savedRoutes.forEach((route) => {
    const distance = offline.distanceMeters(lastUserLatLng.lat, lastUserLatLng.lon, route.lat, route.lon);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = route;
    }
  });
  return best;
}

function ensureNavHud() {
  if (document.getElementById("route-nav")) return;
  const mapEl = document.getElementById("well-map");
  if (!mapEl) return;
  const bar = document.createElement("div");
  bar.id = "route-nav";
  bar.className = "route-nav";
  bar.hidden = true;
  bar.innerHTML =
    '<div id="route-nav-arrow" class="route-nav-arrow" aria-hidden="true"></div>' +
    '<div class="route-nav-copy">' +
    '<p id="route-nav-title" class="route-nav-title"></p>' +
    '<p id="route-nav-detail" class="route-nav-detail" aria-live="polite"></p>' +
    "</div>" +
    '<div class="route-nav-actions">' +
    '<button type="button" class="ghost" id="route-nav-follow">Follow</button>' +
    '<button type="button" class="ghost" id="route-nav-stop">Stop</button>' +
    "</div>";
  mapEl.insertAdjacentElement("beforebegin", bar);
  bar.querySelector("#route-nav-follow").addEventListener("click", () => {
    requestDeviceHeading();
    navFollow = true;
    lastFollowLatLng = null;
    updateFollowButton();
    updateNavigationFrame(true);
  });
  bar.querySelector("#route-nav-stop").addEventListener("click", () => {
    navPaused = true;
    navUserPicked = true;
    navRouteId = null;
    navFollow = false;
    clearNavGraphics();
    applyNavStyles();
    setNavCopy("Navigation paused", "Choose Go on a saved route.");
    updateFollowButton();
    renderOfflineRouteList();
  });
}

function setNavCopy(title, detail) {
  ensureNavHud();
  const bar = document.getElementById("route-nav");
  const titleEl = document.getElementById("route-nav-title");
  const detailEl = document.getElementById("route-nav-detail");
  if (titleEl) titleEl.textContent = title || "";
  if (detailEl) detailEl.textContent = detail || "";
  if (bar) {
    bar.hidden = false;
    bar.classList.remove("is-off", "is-arrived");
  }
}

function updateFollowButton() {
  const button = document.getElementById("route-nav-follow");
  if (!button) return;
  button.textContent = navFollow ? "Following" : "Follow";
  button.classList.toggle("is-on", navFollow);
  button.hidden = !navRouteId;
}

function requestDeviceHeading() {
  bindDeviceHeading();
  const orientation = window.DeviceOrientationEvent;
  if (orientation && typeof orientation.requestPermission === "function") {
    orientation.requestPermission().catch(() => {});
  }
}

function bindDeviceHeading() {
  if (deviceHeadingBound) return;
  deviceHeadingBound = true;
  let wait = false;
  const onOrientation = (event) => {
    let heading = null;
    if (Number.isFinite(event.webkitCompassHeading)) heading = event.webkitCompassHeading;
    else if (event.absolute && Number.isFinite(event.alpha)) heading = (360 - event.alpha) % 360;
    if (!Number.isFinite(heading)) return;
    deviceHeading = (heading + 360) % 360;
    if (!navRouteId || wait) return;
    wait = true;
    window.setTimeout(() => {
      wait = false;
    }, 250);
    drawUserLocation();
    updateNavArrow();
  };
  window.addEventListener("deviceorientationabsolute", onOrientation, true);
  window.addEventListener("deviceorientation", onOrientation, true);
}

function updateNavArrow(relativeBearing) {
  const arrow = document.getElementById("route-nav-arrow");
  if (!arrow) return;
  const turn = Number.isFinite(relativeBearing) ? relativeBearing : 0;
  arrow.style.transform = `rotate(${turn}deg)`;
}

function clearNavGraphics() {
  if (navRemainLine && map) map.removeLayer(navRemainLine);
  if (navJoinLine && map) map.removeLayer(navJoinLine);
  navRemainLine = null;
  navJoinLine = null;
}

function setNavLine(kind, latlngs, style) {
  if (!map || typeof L === "undefined") return null;
  ensureRouteLayers();
  let line = kind === "remain" ? navRemainLine : navJoinLine;
  if (!latlngs || latlngs.length < 2) {
    if (line && map) map.removeLayer(line);
    if (kind === "remain") navRemainLine = null;
    else navJoinLine = null;
    return null;
  }
  if (!line) {
    line = L.polyline(latlngs, Object.assign({ pane: "offline-routes", interactive: false }, style)).addTo(map);
    if (kind === "remain") navRemainLine = line;
    else navJoinLine = line;
  } else {
    line.setLatLngs(latlngs);
    line.setStyle(style);
  }
  if (typeof line.bringToFront === "function") line.bringToFront();
  return line;
}

function applyNavStyles() {
  savedRoutes.forEach((route) => {
    if (!route._line) return;
    const active = !!navRouteId && route.id === navRouteId;
    route._line.setStyle({
      weight: active ? 6 : 3,
      opacity: !navRouteId || active ? 0.95 : 0.35,
    });
  });
}

function remainingLatLngs(route, progress) {
  const coords = route.coordinates || [];
  const rest = [[progress.snapLat, progress.snapLon]];
  for (let i = progress.snapIndex + 1; i < coords.length; i += 1) {
    const pair = coords[i];
    if (!Array.isArray(pair) || pair.length < 2) continue;
    const lat = Number(pair[1]);
    const lon = Number(pair[0]);
    if (Number.isFinite(lat) && Number.isFinite(lon)) rest.push([lat, lon]);
  }
  return rest;
}

function followUser(force) {
  if (!navFollow || !map || !lastUserLatLng) return;
  if (Number.isFinite(lastUserLatLng.accuracy) && lastUserLatLng.accuracy > 150) return;
  const here = L.latLng(lastUserLatLng.lat, lastUserLatLng.lon);
  if (!force && lastFollowLatLng && map.distance(lastFollowLatLng, here) < 12) return;
  lastFollowLatLng = here;
  const zoom = map.getZoom() >= 13 ? map.getZoom() : 14;
  if (Math.abs(map.getZoom() - zoom) < 0.01) map.panTo(here, { animate: true });
  else map.setView(here, zoom, { animate: true });
}

function onNavDrag() {
  if (!navRouteId) return;
  navFollow = false;
  updateFollowButton();
}

function updateNavigationFrame(forceFollow) {
  const route = routeById(navRouteId);
  const offline = window.WellnavOffline;
  if (!route || !offline || !lastUserLatLng) {
    if (route) setNavCopy(route.label || "Saved route", "Waiting for GPS…");
    return;
  }
  const progress = offline.routeProgress(route.coordinates, lastUserLatLng.lat, lastUserLatLng.lon);
  if (!progress) return;
  const guideBearing = offline.bearingDeg(
    lastUserLatLng.lat,
    lastUserLatLng.lon,
    progress.guideLat,
    progress.guideLon
  );
  const heading = currentHeading();
  const relative = heading == null ? guideBearing : (guideBearing - heading + 360) % 360;
  updateNavArrow(heading == null ? 0 : relative);
  const label = route.label || "Saved route";
  const bar = document.getElementById("route-nav");
  const arrived = progress.remainingM < 45 && progress.offRouteM < 80;
  const off = progress.offRouteM > 75;
  let detail = "";
  if (arrived) {
    detail = "You're at this pin.";
  } else if (off) {
    detail = `${formatRouteDistance(progress.offRouteM)} off the saved route · ${formatRouteDistance(progress.remainingM)} left along it`;
  } else {
    const head = heading == null ? `Head ${compassLabel(guideBearing)}` : turnInstruction(relative);
    detail = `${formatRouteDistance(progress.remainingM)} · ${head}`;
  }
  setNavCopy(arrived ? `Arrived · ${label}` : label, detail);
  if (bar) {
    bar.classList.toggle("is-off", off && !arrived);
    bar.classList.toggle("is-arrived", arrived);
  }
  updateFollowButton();
  const activePick = document.querySelector("#offline-routes .is-navigating .mapped-item-select");
  if (activePick) {
    activePick.textContent = arrived
      ? `${label} — arrived`
      : `${label} — ${formatRouteDistance(progress.remainingM)} · ${compassLabel(guideBearing)}`;
  }
  const color = route.color || "#d4a017";
  setNavLine("remain", remainingLatLngs(route, progress), { color, weight: 6, opacity: 1 });
  setNavLine(
    "join",
    progress.offRouteM > 20
      ? [
          [lastUserLatLng.lat, lastUserLatLng.lon],
          [progress.snapLat, progress.snapLon],
        ]
      : null,
    { color: "#4c8dff", weight: 3, opacity: 0.95, dashArray: "4 6" }
  );
  drawUserLocation();
  followUser(!!forceFollow);
}

function beginNavigation(id, { user = false } = {}) {
  const route = routeById(id);
  if (!route || !navigationAvailable()) return;
  navPaused = false;
  navRouteId = id;
  if (user) navUserPicked = true;
  navFollow = true;
  lastFollowLatLng = null;
  if (map) map.closePopup();
  ensureNavHud();
  applyNavStyles();
  requestDeviceHeading();
  updateNavigationFrame(true);
  renderOfflineRouteList();
}

function stopNavigation() {
  navRouteId = null;
  navFollow = false;
  navPaused = false;
  navUserPicked = false;
  navSnappedToNearest = false;
  clearNavGraphics();
  applyNavStyles();
  const bar = document.getElementById("route-nav");
  if (bar) bar.hidden = true;
}

function syncNavigationMode() {
  const available = navigationAvailable();
  if (!available) {
    clearNavGraphics();
    navRouteId = null;
    navFollow = false;
    navPaused = false;
    navUserPicked = false;
    navSnappedToNearest = false;
    const bar = document.getElementById("route-nav");
    if (bar) bar.hidden = true;
    applyNavStyles();
    renderOfflineRouteList();
    return;
  }
  ensureNavHud();
  watchUserLocation();
  bindDeviceHeading();
  if (navPaused) {
    setNavCopy("Navigation paused", "Choose Go on a saved route.");
    updateFollowButton();
    renderOfflineRouteList();
    return;
  }
  if (!navRouteId || !routeById(navRouteId)) {
    const pick = nearestSavedRoute();
    if (pick) beginNavigation(pick.id, { user: false });
    return;
  }
  updateNavigationFrame();
  renderOfflineRouteList();
}

function currentPosition() {
  return new Promise((resolve, reject) => {
    if (!navigator.geolocation) {
      reject(new Error("Location is not available on this device."));
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
      (err) => {
        const msg =
          err && err.code === 1
            ? "Location permission was denied."
            : "Could not read your current location.";
        reject(new Error(msg));
      },
      { enableHighAccuracy: true, timeout: 20000, maximumAge: 15000 }
    );
  });
}

function drawRoutePins() {
  if (!map || typeof L === "undefined") return;
  ensureRouteLayers();
  routePinLayer.clearLayers();
  loadRoutePins().forEach((pin) => {
    const marker = L.marker([Number(pin.lat), Number(pin.lon)], { icon: routePinIcon(), title: pin.label });
    marker.bindPopup(
      locationPopupHtml({
        title: pin.label || "Pinned spot",
        pointLabel: "Pinned spot",
        showPointLabel: false,
        lat: Number(pin.lat),
        lon: Number(pin.lon),
      }),
      locationPopupOptions({ compact: true })
    );
    routePinLayer.addLayer(marker);
  });
}

function drawOfflineRoutes(routes, { fit = false } = {}) {
  savedRoutes = Array.isArray(routes) ? routes : [];
  if (!map || typeof L === "undefined") return;
  ensureRouteLayers();
  routeLineLayer.clearLayers();
  const all = [];
  savedRoutes.forEach((route, index) => {
    const latlngs = (route.coordinates || [])
      .filter((pair) => Array.isArray(pair) && pair.length >= 2)
      .map((pair) => [Number(pair[1]), Number(pair[0])])
      .filter((pair) => Number.isFinite(pair[0]) && Number.isFinite(pair[1]));
    if (latlngs.length < 2) return;
    const color = ROUTE_COLORS[index % ROUTE_COLORS.length];
    route.color = color;
    const line = L.polyline(latlngs, {
      pane: "offline-routes",
      color,
      weight: 4,
      opacity: 0.92,
      dashArray: route.mode === "direct" ? "7 6" : null,
      interactive: false,
    });
    route._line = line;
    routeLineLayer.addLayer(line);
    all.push(...latlngs);
  });
  if (fit && all.length && map) {
    const bounds = L.latLngBounds(all);
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [36, 36], maxZoom: 14 });
  }
  applyNavStyles();
  renderOfflineRouteList();
}

function routeRows() {
  const byId = new Map();
  loadRoutePins().forEach((pin) => {
    byId.set(pin.id, {
      id: pin.id,
      label: pin.label || "Pinned spot",
      lat: Number(pin.lat),
      lon: Number(pin.lon),
      kind: "pin",
      saved: false,
    });
  });
  savedRoutes.forEach((route) => {
    byId.set(route.id, { ...route, saved: true });
  });
  return [...byId.values()];
}

function renderOfflineRouteList() {
  const list = document.getElementById("offline-routes");
  if (!list) return;
  const rows = routeRows();
  list.replaceChildren();
  list.hidden = !rows.length;
  const offline = window.WellnavOffline;
  rows.forEach((row, index) => {
    const li = document.createElement("li");
    li.className = "mapped-item" + (row.saved && row.id === navRouteId ? " is-navigating" : "");
    const swatch = document.createElement("i");
    swatch.className = "route-swatch";
    swatch.style.background = row.color || ROUTE_COLORS[index % ROUTE_COLORS.length];
    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "mapped-item-select";
    const liveMeters =
      lastUserLatLng && offline
        ? offline.distanceMeters(lastUserLatLng.lat, lastUserLatLng.lon, row.lat, row.lon)
        : null;
    const dist = formatRouteDistance(liveMeters != null ? liveMeters : row.distance_m);
    const heading = compassLabel(
      lastUserLatLng && offline
        ? offline.bearingDeg(lastUserLatLng.lat, lastUserLatLng.lon, row.lat, row.lon)
        : row.bearing
    );
    const minutes = formatRouteMinutes(row.duration_s);
    const detail = [dist, heading, minutes].filter(Boolean).join(" · ");
    pick.textContent = detail ? `${row.label || "Pinned location"} — ${detail}` : row.label || "Pinned location";
    if (!row.saved) pick.title = "Save for offline to keep a route to this pin.";
    else if (row.mode === "direct") pick.title = "Direct line. A road route was not available.";
    else pick.title = "Road route from your location when this pack was saved.";
    pick.dataset.routeId = row.id || "";
    pick.addEventListener("click", () => {
      if (navFollow) {
        navFollow = false;
        updateFollowButton();
      }
      focusRouteRow(row);
    });
    li.append(swatch, pick);
    if (row.saved && navigationAvailable()) {
      const go = document.createElement("button");
      go.type = "button";
      go.className = "route-nav-go";
      go.textContent = row.id === navRouteId ? "Navigating" : "Go";
      go.addEventListener("click", (event) => {
        event.stopPropagation();
        beginNavigation(row.id, { user: true });
      });
      li.appendChild(go);
    }
    if (row.kind === "pin") {
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "mapped-item-remove";
      remove.setAttribute("aria-label", `Remove ${row.label || "pin"}`);
      remove.textContent = "×";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeRoutePin(row.id);
      });
      li.appendChild(remove);
    }
    list.appendChild(li);
  });
}

function focusRouteRow(row) {
  if (!map || !Number.isFinite(Number(row.lat)) || !Number.isFinite(Number(row.lon))) return;
  const points = [[Number(row.lat), Number(row.lon)]];
  (row.coordinates || []).forEach((pair) => {
    if (Array.isArray(pair) && pair.length >= 2) points.push([Number(pair[1]), Number(pair[0])]);
  });
  if (lastUserLatLng) points.push([lastUserLatLng.lat, lastUserLatLng.lon]);
  if (points.length === 1) {
    map.setView(points[0], 14);
    return;
  }
  const bounds = L.latLngBounds(points.filter((pair) => Number.isFinite(pair[0]) && Number.isFinite(pair[1])));
  if (bounds.isValid()) map.fitBounds(bounds, { padding: [36, 36], maxZoom: 14 });
}

function addRoutePin(latlng) {
  if (!latlng) return;
  const lat = Number(latlng.lat);
  const lon = Number(latlng.lng != null ? latlng.lng : latlng.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  if (!inOfflineCoverage(lat, lon)) {
    setOfflineStatus("That spot is outside Texas, New Mexico, Oklahoma, and Louisiana.");
    return;
  }
  const pins = loadRoutePins();
  const offline = window.WellnavOffline;
  const duplicate = pins.some(
    (pin) => offline && offline.distanceMeters(lat, lon, Number(pin.lat), Number(pin.lon)) < 25
  );
  if (duplicate) {
    setOfflineStatus("That spot is already pinned.");
    return;
  }
  if (pins.length >= MAX_ROUTE_PINS) {
    setOfflineStatus("You can pin up to 12 spots. Remove one to add another.");
    return;
  }
  const id = safeRouteId("pin", `${Date.now().toString(36)}${Math.floor(Math.random() * 1296).toString(36)}`);
  pins.push({
    id,
    kind: "pin",
    label: pins.length ? `Pinned spot ${pins.length + 1}` : "Pinned spot",
    lat,
    lon,
  });
  saveRoutePins(pins);
  drawRoutePins();
  renderOfflineRouteList();
  setOfflineStatus("Pin added. Save for offline to keep a route from your location.");
}

async function removeRoutePin(id) {
  saveRoutePins(loadRoutePins().filter((pin) => pin.id !== id));
  savedRoutes = savedRoutes.filter((route) => route.id !== id);
  const offline = window.WellnavOffline;
  if (offline) {
    try {
      await offline.deleteRoute(id);
    } catch {
      /* ignore */
    }
  }
  const wasActive = navRouteId === id;
  drawRoutePins();
  drawOfflineRoutes(savedRoutes);
  if (!savedRoutes.length) {
    stopNavigation();
    stopUserLocation();
    return;
  }
  if (wasActive) {
    navRouteId = null;
    navUserPicked = false;
    navSnappedToNearest = false;
    syncNavigationMode();
  }
}

function setPinMode(on) {
  pinMode = !!on;
  const btn = document.getElementById("offline-pin");
  if (btn) {
    btn.classList.toggle("is-on", pinMode);
    btn.setAttribute("aria-pressed", pinMode ? "true" : "false");
    btn.textContent = pinMode ? "Done pinning" : "Pin spot";
  }
  const mapEl = document.getElementById("well-map");
  if (mapEl) mapEl.classList.toggle("is-pinning", pinMode);
}

function boundsOfDestinations(origin, destinations) {
  let west = origin.lon;
  let east = origin.lon;
  let south = origin.lat;
  let north = origin.lat;
  destinations.forEach((dest) => {
    west = Math.min(west, dest.lon);
    east = Math.max(east, dest.lon);
    south = Math.min(south, dest.lat);
    north = Math.max(north, dest.lat);
  });
  return { west: west - 0.02, south: south - 0.02, east: east + 0.02, north: north + 0.02 };
}

async function storeAndDrawRoutes(routes, { fit = false } = {}) {
  const offline = window.WellnavOffline;
  if (offline) await offline.saveRoutes(routes);
  drawOfflineRoutes(routes, { fit });
  watchUserLocation();
  drawUserLocation();
}

async function saveCurrentView(saveBtn) {
  const offline = window.WellnavOffline;
  const bounds = map.getBounds();
  const result = await offline.downloadArea(
    {
      west: bounds.getWest(),
      south: bounds.getSouth(),
      east: bounds.getEast(),
      north: bounds.getNorth(),
    },
    {
      zoom: map.getZoom(),
      onProgress({ done, total }) {
        saveBtn.textContent = `Saving ${done}/${total}…`;
      },
    }
  );
  setOfflineStatus(
    `Saved this view (${result.tiles.toLocaleString()} ${basemapLabel(selectedBasemap())} tile${result.tiles === 1 ? "" : "s"}). Pin a spot or a well to also keep a route.`
  );
}

async function saveOfflineRoutes(saveBtn, destinations, omitted = 0) {
  const offline = window.WellnavOffline;
  setOfflineStatus("Finding your location…");
  const origin = await currentPosition();
  lastUserLatLng = origin;
  drawUserLocation();
  if (!offline.isOnline()) {
    const routes = offline.directRoutes(origin, destinations);
    await storeAndDrawRoutes(routes, { fit: true });
    setOfflineStatus(
      "Offline — showing a direct line to each pin. Connect and save again for road routes and map tiles."
    );
    return;
  }
  saveBtn.textContent = "Routing…";
  let pack = null;
  let responseError = null;
  try {
    const resp = await fetch("/offline/routes", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ origin, destinations }),
    });
    const body = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      responseError = new Error(body.error || "Could not build routes.");
      responseError.status = resp.status;
    } else {
      pack = body;
    }
  } catch (err) {
    responseError = err;
  }
  if (!pack) {
    if (responseError && responseError.status === 400) throw responseError;
    const routes = offline.directRoutes(origin, destinations);
    await storeAndDrawRoutes(routes, { fit: true });
    try {
      await offline.downloadArea(boundsOfDestinations(origin, destinations), {
        zoom: map.getZoom(),
        onProgress({ done, total }) {
          saveBtn.textContent = `Saving ${done}/${total}…`;
        },
      });
      setOfflineStatus("Saved direct routes and map tiles. A road route was unavailable.");
    } catch (tileErr) {
      const message = tileErr && tileErr.message ? tileErr.message : "Map tiles were not saved.";
      setOfflineStatus(`Saved direct routes on this device. ${message}`);
    }
    return;
  }
  const routes = Array.isArray(pack.routes) ? pack.routes : [];
  await storeAndDrawRoutes(routes, { fit: true });
  const tiles = Array.isArray(pack.tiles) ? pack.tiles : [];
  if (tiles.length) {
    try {
      await offline.downloadTiles(tiles, {
        onProgress({ done, total }) {
          saveBtn.textContent = `Saving ${done}/${total}…`;
        },
      });
    } catch (tileErr) {
      if (tileErr && tileErr.name === "AbortError") throw tileErr;
      const message = tileErr && tileErr.message ? tileErr.message : "Map tiles were not saved.";
      setOfflineStatus(`Saved ${routes.length} route${routes.length === 1 ? "" : "s"} on this device. ${message}`);
      return;
    }
  }
  const directCount = routes.filter((route) => route.mode === "direct").length;
  const routeLabel = `${routes.length} route${routes.length === 1 ? "" : "s"}`;
  const layerName = basemapLabel(selectedBasemap());
  const tileLabel = tiles.length
    ? ` and ${tiles.length.toLocaleString()} ${layerName} tile${tiles.length === 1 ? "" : "s"}`
    : "";
  let note = `Saved ${routeLabel}${tileLabel}.`;
  if (directCount) {
    note += directCount === routes.length ? " Road routes were unavailable, so these are direct lines." : ` ${directCount} use a direct line.`;
  }
  if (pack.truncated) note += " Some farther zoom levels were left out to keep the pack a practical size.";
  if (omitted) note += ` ${omitted} extra pin${omitted === 1 ? "" : "s"} did not fit in this pack.`;
  setOfflineStatus(note);
}

function ensureOfflineControls() {
  const actions = document.querySelector(".map-actions");
  if (actions && !document.getElementById("offline-save")) {
    const wrap = document.createElement("div");
    wrap.className = "offline-pack";
    wrap.innerHTML =
      '<button type="button" class="ghost" id="offline-pin" aria-pressed="false">Pin spot</button>' +
      '<button type="button" class="ghost" id="offline-save">Save for offline</button>' +
      '<span class="info-tip">' +
      '<button type="button" class="info-tip-btn" aria-expanded="false" aria-label="About offline maps">i</button>' +
      '<span class="info-tip-pop" popover="manual" hidden role="tooltip">Pin one or more spots, wells, or sites. Save for offline stores a route from your location to each pin and the base layer selected at that moment. Choose USGS Topo. Esri and OpenStreetMap stay online-only.</span>' +
      "</span>" +
      '<button type="button" class="ghost" id="offline-clear" hidden>Clear saved maps</button>' +
      '<p id="offline-status" class="muted"></p>';
    actions.appendChild(wrap);
  }
  if (!document.getElementById("offline-routes")) {
    const list = document.createElement("ul");
    list.id = "offline-routes";
    list.className = "offline-routes";
    list.hidden = true;
    list.setAttribute("aria-label", "Routes to pinned locations");
    const anchor = document.getElementById("mapped-list") || document.getElementById("well-map");
    if (anchor) anchor.insertAdjacentElement("afterend", list);
  }
}

function restoreOfflineRoutePack() {
  drawRoutePins();
  setPinMode(pinMode);
  const offline = window.WellnavOffline;
  if (!offline) return;
  if (!routesReady) {
    routesReady = offline
      .loadRoutes()
      .then((routes) => {
        savedRoutes = Array.isArray(routes) ? routes : [];
        drawOfflineRoutes(savedRoutes);
        if (savedRoutes.length) watchUserLocation();
        applyNetworkState();
      })
      .catch(() => {
        routesReady = null;
      });
    return;
  }
  drawOfflineRoutes(savedRoutes);
  drawUserLocation();
}

function bindOfflinePack() {
  ensureOfflineControls();
  const saveBtn = document.getElementById("offline-save");
  const clearBtn = document.getElementById("offline-clear");
  const pinBtn = document.getElementById("offline-pin");
  const offline = window.WellnavOffline;
  if (!saveBtn || !offline || saveBtn.dataset.bound === "1") {
    refreshOfflineStatus();
    renderOfflineRouteList();
    return;
  }
  saveBtn.dataset.bound = "1";
  saveBtn.addEventListener("click", async () => {
    if (!map || saveBtn.dataset.busy === "1") return;
    const basemap = selectedBasemap();
    if (!offline.cacheableBasemap(basemap)) {
      setOfflineStatus(
        `${basemapLabel(basemap)} can't be saved for offline use. Choose USGS Topo, then save again.`
      );
      return;
    }
    const packed = collectRouteDestinations();
    const destinations = packed.destinations;
    if (!destinations.length && !offline.isOnline()) {
      setOfflineStatus("Connect to download tiles. Pin a location to keep a route for offline use.");
      return;
    }
    saveBtn.dataset.busy = "1";
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    let keepStatus = false;
    try {
      if (!destinations.length) {
        await saveCurrentView(saveBtn);
        keepStatus = true;
      } else {
        await saveOfflineRoutes(saveBtn, destinations, packed.omitted);
        keepStatus = true;
      }
      if (clearBtn) clearBtn.hidden = false;
    } catch (err) {
      keepStatus = true;
      if (err && err.name === "AbortError") {
        setOfflineStatus("Save cancelled.");
      } else {
        setOfflineStatus(err && err.message ? err.message : "Could not save for offline use.");
      }
    } finally {
      saveBtn.dataset.busy = "0";
      saveBtn.disabled = false;
      saveBtn.textContent = "Save for offline";
      if (!keepStatus) refreshOfflineStatus();
    }
  });
  if (pinBtn && pinBtn.dataset.bound !== "1") {
    pinBtn.dataset.bound = "1";
    pinBtn.addEventListener("click", () => setPinMode(!pinMode));
  }
  if (clearBtn && clearBtn.dataset.bound !== "1") {
    clearBtn.dataset.bound = "1";
    clearBtn.addEventListener("click", async () => {
      if (!window.confirm("Remove saved map tiles and offline routes from this device? Pins stay until you remove them.")) return;
      await offline.clear();
      savedRoutes = [];
      stopNavigation();
      drawOfflineRoutes([]);
      stopUserLocation();
      refreshOfflineStatus();
    });
  }
  refreshOfflineStatus();
}

function applyNetworkState() {
  const banner = document.getElementById("net-banner");
  const offline = window.WellnavOffline;
  const online = !offline || offline.isOnline();
  if (banner) banner.hidden = online;
  document.body.classList.toggle("is-offline", !online);
  if (!map) {
    refreshOfflineStatus();
    return;
  }
  if (!online) {
    setBasemap("usgs", { persist: false });
    schedulePipelines();
    scheduleDisposal();
  } else {
    setBasemap(preferredBasemap(), { persist: false });
  }
  syncNavigationMode();
  refreshOfflineStatus();
}

function bindBasemapSelect() {
  const select = document.getElementById("basemap-select");
  if (!select) return;
  if (!select.options.length) {
    Object.entries(BASEMAP_LABELS).forEach(([key, label]) => {
      const opt = document.createElement("option");
      opt.value = key;
      opt.textContent = label;
      select.appendChild(opt);
    });
  }
  select.value = preferredBasemap();
  if (select.dataset.bound === "1") return;
  select.addEventListener("change", () => setBasemap(select.value));
  select.dataset.bound = "1";
}

function pipelinesEnabled() {
  return localStorage.getItem(PIPELINE_PREF) !== "0";
}

function disposalEnabled() {
  return localStorage.getItem(DISPOSAL_PREF) === "1";
}

function bindOverlayToggles() {
  const pipe = document.getElementById("pipeline-toggle");
  const disposal = document.getElementById("disposal-toggle");
  if (pipe) {
    pipe.checked = pipelinesEnabled();
    if (pipe.dataset.bound !== "1") {
      pipe.addEventListener("change", () => {
        localStorage.setItem(PIPELINE_PREF, pipe.checked ? "1" : "0");
        pipelineKey = "";
        loadPipelines();
      });
      pipe.dataset.bound = "1";
    }
  }
  if (disposal) {
    disposal.checked = disposalEnabled();
    if (disposal.dataset.bound !== "1") {
      disposal.addEventListener("change", () => {
        localStorage.setItem(DISPOSAL_PREF, disposal.checked ? "1" : "0");
        disposalKey = "";
        if (!disposal.checked) {
          disposalPopupPinned = false;
          disposalFocus = null;
          if (map) map.closePopup();
          if (!pipelinePin) pinChrome = false;
          updateChrome(loadStore());
        }
        loadDisposal();
      });
      disposal.dataset.bound = "1";
    }
  }
}

function coarsePointer() {
  return window.matchMedia("(pointer: coarse)").matches;
}

function pipelineHitPx() {
  return coarsePointer() ? 28 : 12;
}

function disposalHitPx() {
  // Fingers miss a 6px dot. On a phone, a tap near the site should select it
  // instead of the pipeline underneath.
  return coarsePointer() ? 36 : 8;
}

function pipelineStyle(feature) {
  const props = feature.properties || {};
  const group = props.commodity_group || "other";
  const abandoned = props.status && props.status !== "I";
  const diameter = Number(props.diameter) || 0;
  const selected = pipelineFocus.highlightId && feature.id === pipelineFocus.highlightId;
  const inFocus =
    (pipelineFocus.p5 && props.p5 && props.p5 === pipelineFocus.p5) ||
    (pipelineFocus.system && props.system && props.system === pipelineFocus.system);
  const boost = coarsePointer() ? 1.1 : 0;
  return {
    color: selected ? "#fff2a8" : PIPELINE_COLORS[group] || PIPELINE_COLORS.other,
    weight: selected
      ? Math.max(4.8, Math.min(8, (diameter / 6 || 4.8) + boost))
      : Math.max((inFocus ? 2.6 : 1.8) + boost, Math.min(5.5, (diameter / 8 || 1.8) + boost)),
    opacity: selected ? 1 : abandoned ? 0.42 : inFocus ? 0.95 : 0.88,
    dashArray: abandoned ? "5 4" : null,
  };
}

function ensurePipelineLayer() {
  if (!map) return null;
  if (!map.getPane("pipelines")) {
    map.createPane("pipelines");
    map.getPane("pipelines").style.zIndex = 350;
  }
  if (!pipelineLayer) {
    pipelineLayer = L.geoJSON(null, {
      pane: "pipelines",
      interactive: false,
      renderer: L.canvas({ pane: "pipelines" }),
      style: pipelineStyle,
    });
    pipelineLayer.addTo(map);
  }
  return pipelineLayer;
}

function setPipelineStatus(text) {
  const el = document.getElementById("pipeline-status");
  if (el) el.textContent = text || "";
}

function schedulePipelines() {
  window.clearTimeout(pipelineTimer);
  pipelineTimer = window.setTimeout(loadPipelines, 220);
}

function loadPipelines() {
  if (!map) return;
  const legend = document.getElementById("pipeline-legend");
  bindOverlayToggles();
  if (!pipelinesEnabled()) {
    if (pipelineAbort) pipelineAbort.abort();
    if (pipelineLayer) pipelineLayer.clearLayers();
    if (legend) legend.hidden = true;
    setPipelineStatus("");
    pipelineKey = "off";
    if (pipelinePin || pipelinePinMarker) clearPipelinePin();
    return;
  }
  ensurePipelineLayer();
  const padded = map.getBounds().pad(0.18);
  const bbox = [
    padded.getWest(),
    padded.getSouth(),
    padded.getEast(),
    padded.getNorth(),
  ].join(",");
  const zoom = map.getZoom();
  const focusKey = `${pipelineFocus.p5}|${pipelineFocus.system}|${pipelineFocus.operator}|${pipelineFocus.highlightId || ""}`;
  const key = `${bbox}|${zoom}|0|${focusKey}`;
  if (key === pipelineKey) return;
  pipelineKey = key;
  if (pipelineAbort) pipelineAbort.abort();
  pipelineAbort = new AbortController();
  const params = new URLSearchParams({
    bbox,
    z: String(zoom),
    abandoned: "0",
  });
  if (pipelineFocus.p5) params.set("p5", pipelineFocus.p5);
  if (pipelineFocus.system) params.set("system", pipelineFocus.system);
  if (pipelineFocus.operator && !pipelineFocus.p5) params.set("operator", pipelineFocus.operator);
  const url = `/pipelines?${params.toString()}`;
  fetch(url, { signal: pipelineAbort.signal, headers: { Accept: "application/json" } })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    })
    .then((payload) => {
      if (!pipelineLayer) return;
      pipelineLayer.clearLayers();
      if (payload && payload.features && payload.features.length) {
        pipelineLayer.addData(payload);
      }
      if (window.WellnavOffline && payload) {
        window.WellnavOffline.putOverlay("pipelines", payload, {
          west: padded.getWest(),
          south: padded.getSouth(),
          east: padded.getEast(),
          north: padded.getNorth(),
        }).catch(() => {});
      }
      if (legend) legend.hidden = false;
      const meta = payload.meta || {};
      if (!meta.stored) {
        setPipelineStatus("No local pipeline overlay yet. Run python -m wellnav.ingest load-pipelines");
      } else if (!payload.features.length) {
        setPipelineStatus(
          pipelineFocus.p5 || pipelineFocus.system
            ? "No matching pipelines in this view. Pan the map or clear the pipeline filter."
            : "No pipelines in this view. Zoom in or pan to another area."
        );
      } else if (meta.truncated) {
        setPipelineStatus(`Showing ${meta.count} pipeline segments in view (zoom in for more detail).`);
      } else if (pipelineFocus.p5 || pipelineFocus.system || pipelineFocus.operator) {
        setPipelineStatus(`Showing ${meta.count} matching pipeline segments in view.`);
      } else {
        setPipelineStatus("");
      }
    })
    .catch((err) => {
      if (err && err.name === "AbortError") return;
      const offline = window.WellnavOffline;
      if (offline && !offline.isOnline()) {
        offline
          .getOverlay("pipelines", {
            west: padded.getWest(),
            south: padded.getSouth(),
            east: padded.getEast(),
            north: padded.getNorth(),
          })
          .then((cached) => {
            if (!pipelineLayer) return;
            if (cached && cached.features) {
              pipelineLayer.clearLayers();
              pipelineLayer.addData(cached);
              if (legend) legend.hidden = false;
              setPipelineStatus("Offline — showing the last pipelines saved for this area.");
              return;
            }
            setPipelineStatus("Offline — save this view while online to keep pipelines.");
          })
          .catch(() => setPipelineStatus("Offline — pipeline overlay unavailable."));
        return;
      }
      setPipelineStatus("Could not load the pipeline overlay.");
    });
}

function enablePipelineOverlay() {
  localStorage.setItem(PIPELINE_PREF, "1");
  const pipe = document.getElementById("pipeline-toggle");
  if (pipe) pipe.checked = true;
}

function clearPipelineFocus() {
  pipelineFocus = { p5: "", system: "", operator: "", highlightId: null };
  pipelineKey = "";
  renderPipelineOwners(null);
  loadPipelines();
}

function fitPipelineBounds(west, south, east, north) {
  if (!map) return;
  const w = Number(west);
  const s = Number(south);
  const e = Number(east);
  const n = Number(north);
  if (![w, s, e, n].every(Number.isFinite)) return;
  const bounds = L.latLngBounds([s, w], [n, e]);
  if (!bounds.isValid()) return;
  const span = Math.max(e - w, n - s);
  const maxZoom = span > 4 ? 7 : span > 1.5 ? 9 : 12;
  map.fitBounds(bounds.pad(0.12), { maxZoom, padding: [28, 28] });
}

function focusPipeline(opts) {
  const next = opts || {};
  pipelineFocus = {
    p5: String(next.p5 || "").trim(),
    system: String(next.system || "").trim(),
    operator: String(next.operator || "").trim(),
    highlightId: next.highlightId || null,
  };
  enablePipelineOverlay();
  revealMapOnPhone();
  if (!ensureMap()) return;
  fitPipelineBounds(next.west, next.south, next.east, next.north);
  pipelineKey = "";
  loadPipelines();
  if (pipelineFocus.highlightId) {
    fetchSegmentOwnership(pipelineFocus.highlightId);
  } else {
    fetchOwnerSummary(pipelineFocus);
  }
}

function applyPipelineFocusFromEl(el) {
  if (!el) return;
  focusPipeline({
    p5: el.dataset.p5 || "",
    system: el.dataset.system || "",
    operator: el.dataset.operator || "",
    west: el.dataset.west,
    south: el.dataset.south,
    east: el.dataset.east,
    north: el.dataset.north,
  });
}

function refreshMapSize() {
  if (!map) return;
  window.requestAnimationFrame(() => {
    if (map) map.invalidateSize({ animate: false });
  });
}

function pointerOnMapChrome(event) {
  const target = event && event.originalEvent && event.originalEvent.target;
  if (!target || !target.closest) return false;
  return !!target.closest(".leaflet-marker-icon, .leaflet-popup, .leaflet-control, .leaflet-tooltip");
}

function pointHitsLayer(root, latlng) {
  if (!map || !root || !latlng) return false;
  const point = map.latLngToLayerPoint(latlng);
  let hit = false;
  const visit = (layer) => {
    if (hit || !layer) return;
    if (typeof layer.eachLayer === "function" && layer !== pipelineLayer) {
      layer.eachLayer(visit);
      return;
    }
    if (typeof layer._containsPoint === "function") {
      try {
        if (layer._containsPoint(point)) hit = true;
      } catch {
        /* path not drawn yet */
      }
    }
  };
  visit(root);
  return hit;
}

function nearestDisposal(latlng, maxPx) {
  if (!map || !disposalLayer || !disposalEnabled()) return null;
  const target = map.latLngToLayerPoint(latlng);
  let best = null;
  let bestDist = maxPx;
  disposalLayer.eachLayer((layer) => {
    if (typeof layer.getLatLng !== "function") return;
    const dist = map.latLngToLayerPoint(layer.getLatLng()).distanceTo(target);
    if (dist < bestDist) {
      bestDist = dist;
      best = { layer, dist };
    }
  });
  return best;
}

function selectDisposalFromLayer(layer) {
  const feature = layer && layer.feature;
  if (!feature) return;
  const props = feature.properties || {};
  const here = typeof layer.getLatLng === "function" ? layer.getLatLng() : null;
  selectDisposalSite(
    {
      ...props,
      id: feature.id ?? props.id,
      lat: props.lat != null ? props.lat : here && here.lat,
      lon: props.lon != null ? props.lon : here && here.lng,
    },
    { fromMarker: true }
  );
  if (typeof layer.openPopup === "function") layer.openPopup();
}

function nearestPipeline(latlng, maxPx) {
  if (!map || !pipelineLayer || !pipelinesEnabled() || !L.LineUtil) return null;
  const target = map.latLngToLayerPoint(latlng);
  let best = null;
  let bestDist = maxPx;
  const consider = (layer, coords) => {
    if (!coords || !coords.length) return;
    if (typeof coords[0].lat === "number") {
      for (let i = 0; i < coords.length - 1; i += 1) {
        const a = map.latLngToLayerPoint(coords[i]);
        const b = map.latLngToLayerPoint(coords[i + 1]);
        const point = L.LineUtil.closestPointOnSegment(target, a, b);
        const dist = point.distanceTo(target);
        if (dist < bestDist) {
          bestDist = dist;
          best = { layer, feature: layer.feature, point, dist };
        }
      }
      return;
    }
    coords.forEach((part) => consider(layer, part));
  };
  pipelineLayer.eachLayer((layer) => {
    if (!layer.feature || typeof layer.getLatLngs !== "function") return;
    consider(layer, layer.getLatLngs());
  });
  return best;
}

function pickPipelineAt(event) {
  if (!event || !event.latlng || pointerOnMapChrome(event) || pointHitsLayer(wellLayer, event.latlng)) return;
  const disposal = nearestDisposal(event.latlng, disposalHitPx());
  if (disposal) {
    selectDisposalFromLayer(disposal.layer);
    return;
  }
  const hit = nearestPipeline(event.latlng, pipelineHitPx());
  if (!hit || !hit.feature) return;
  const snapped = hit.point ? map.layerPointToLatLng(hit.point) : event.latlng;
  selectPipelineFeature(hit.feature, snapped, hit.layer);
}

function selectPipelineFeature(feature, latlng, layer) {
  const props = (feature && feature.properties) || {};
  pipelineFocus.highlightId = feature.id;
  pipelineFocus.p5 = pipelineFocus.p5 || props.p5 || "";
  pipelineFocus.operator = pipelineFocus.operator || props.operator || "";
  if (pipelineLayer) pipelineLayer.eachLayer((lyr) => pipelineLayer.resetStyle(lyr));
  fetchSegmentOwnership(feature.id, props);
  if (latlng) dropPipelinePin(latlng, props, layer);
  revealMapOnPhone();
}

function ensurePipelinePinPane() {
  if (!map) return;
  if (!map.getPane("pipeline-pin")) {
    map.createPane("pipeline-pin");
  }
  map.getPane("pipeline-pin").style.zIndex = 450;
}

function snapToPipelineLayer(layer, latlng) {
  if (!map || !latlng || !layer || typeof layer.getLatLngs !== "function" || !L.LineUtil) {
    return latlng;
  }
  const target = map.latLngToLayerPoint(latlng);
  let best = null;
  let bestDist = Infinity;
  const walkSegments = (coords) => {
    if (!coords || !coords.length) return;
    if (typeof coords[0].lat === "number") {
      for (let i = 0; i < coords.length - 1; i += 1) {
        const a = map.latLngToLayerPoint(coords[i]);
        const b = map.latLngToLayerPoint(coords[i + 1]);
        const point = L.LineUtil.closestPointOnSegment(target, a, b);
        const dist = point.distanceTo(target);
        if (dist < bestDist) {
          bestDist = dist;
          best = point;
        }
      }
      return;
    }
    coords.forEach(walkSegments);
  };
  walkSegments(layer.getLatLngs());
  return best ? map.layerPointToLatLng(best) : latlng;
}

function clearPipelinePin() {
  pinChrome = false;
  pipelinePin = null;
  if (pipelinePinMarker) {
    if (map) map.removeLayer(pipelinePinMarker);
    pipelinePinMarker = null;
  }
  updateChrome(loadStore());
}

function dropPipelinePin(latlng, props, layer) {
  if (!map || !latlng) return;
  const snapped = snapToPipelineLayer(layer, latlng);
  const lat = snapped.lat;
  const lon = snapped.lng;
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
  const operator = String((props && props.operator) || "").trim();
  const system = String((props && props.system) || "").trim();
  pipelinePin = {
    lat,
    lon,
    operator,
    system,
    commodity: String((props && (props.commodity_desc || props.commodity)) || "").trim(),
  };
  pinChrome = true;
  ensurePipelinePinPane();
  const here = [lat, lon];
  if (pipelinePinMarker) {
    pipelinePinMarker.setLatLng(here);
  } else {
    pipelinePinMarker = L.marker(here, {
      pane: "pipeline-pin",
      keyboard: true,
      title: "Pipeline point",
      riseOnHover: true,
      icon: L.divIcon({
        className: "pipeline-pin-wrap",
        html: '<span class="pipeline-pin"></span>',
        iconSize: [22, 28],
        iconAnchor: [11, 26],
      }),
    });
    pipelinePinMarker.bindPopup(pipelinePinPopup(pipelinePin), locationPopupOptions());
    pipelinePinMarker.addTo(map);
  }
  if (pipelinePinMarker.getPopup()) {
    pipelinePinMarker.setPopupContent(pipelinePinPopup(pipelinePin));
  }
  pipelinePinMarker.openPopup();
  updateChrome(loadStore());
}

function fetchSegmentOwnership(tpmsId, fallback) {
  fetch(`/pipelines/segment/${encodeURIComponent(tpmsId)}`, {
    headers: { Accept: "application/json" },
  })
    .then((resp) => resp.json().then((body) => ({ ok: resp.ok, body })))
    .then(({ ok, body }) => {
      if (!ok) {
        renderPipelineOwners(fallback ? { ...fallback, id: tpmsId } : null);
        return;
      }
      renderPipelineOwners(body);
    })
    .catch(() => {
      renderPipelineOwners(fallback ? { ...fallback, id: tpmsId } : null);
    });
}

function fetchOwnerSummary(focus) {
  const params = new URLSearchParams();
  if (focus.p5) params.set("p5", focus.p5);
  if (focus.system) params.set("system", focus.system);
  if (focus.operator && !focus.p5) params.set("operator", focus.operator);
  if (![...params.keys()].length) {
    renderPipelineOwners(null);
    return;
  }
  fetch(`/pipelines/owner?${params.toString()}`, { headers: { Accept: "application/json" } })
    .then((resp) => (resp.ok ? resp.json() : null))
    .then((body) => renderPipelineOwners(body))
    .catch(() => renderPipelineOwners(null));
}

function ownerField(root, label, value) {
  if (!value && value !== 0) return;
  const dt = document.createElement("dt");
  dt.textContent = label;
  const dd = document.createElement("dd");
  dd.textContent = String(value);
  root.append(dt, dd);
}

function renderPipelineOwners(payload) {
  const root = document.getElementById("pipeline-owners") || ensurePipelineOwnersNode();
  if (!root) return;
  if (!payload) {
    root.hidden = true;
    root.replaceChildren();
    return;
  }
  const identity = payload.identity || {};
  const summary = payload.summary || payload;
  const operator = payload.operator || summary.operator || "";
  const p5 = payload.p5 || summary.p5 || "";
  const system = payload.system || summary.system || "";
  const head = document.createElement("div");
  head.className = "pipeline-owners-head";
  const titles = document.createElement("div");
  const pinActive = !!(pinChrome && pipelinePin);
  const h3 = document.createElement("h3");
  // Pin chrome title already shows the operator — avoid repeating it as the panel heading.
  h3.textContent = pinActive
    ? "Pipeline details"
    : operator || identity.name || "Pipeline ownership";
  const note = document.createElement("p");
  note.className = "muted";
  note.textContent = payload.disclaimer || summary.disclaimer || "";
  titles.append(h3, note);
  const close = document.createElement("button");
  close.type = "button";
  close.className = "ghost";
  close.textContent = "Clear";
  close.addEventListener("click", clearPipelineFocus);
  head.append(titles, close);

  const dl = document.createElement("dl");
  const quality = payload.quality || "";
  const t4ish = !quality || quality.length <= 2;
  // When a pin is active, title already shows operator — skip repeating it here.
  if (!pinActive || !operator) {
    ownerField(dl, t4ish ? "T-4 operator" : "Operator", operator);
  }
  ownerField(dl, "P-5 number", p5);
  ownerField(dl, "RRC organization", identity.name && identity.name !== operator ? identity.name : "");
  ownerField(dl, "Org status", identity.org_status);
  ownerField(dl, "Org type", identity.org_type);
  if (identity.wells) ownerField(dl, "RRC well count", identity.wells);
  if (!pinActive || !(pipelinePin && pipelinePin.system === system)) {
    ownerField(dl, "Pipeline / system", system);
  }
  ownerField(dl, "Subsystem", payload.subsystem);
  ownerField(dl, t4ish ? "T-4 permit" : "Permit / serial", payload.t4);
  ownerField(dl, "Source", payload.quality_label || "");
  ownerField(dl, "Pipeline ID", payload.pipeline_id);
  ownerField(dl, "Diameter", payload.diameter ? `${payload.diameter} in` : "");
  const commodity = payload.commodity_desc || payload.commodity;
  if (!pinActive || !(pipelinePin && pipelinePin.commodity && commodity)) {
    ownerField(dl, "Commodity", commodity);
  }
  ownerField(dl, "Status", payload.status_label);
  ownerField(dl, "System type", payload.systype_label);
  ownerField(dl, "County", payload.county_name ? `${payload.county_name} County` : "");
  if (summary.segments) ownerField(dl, "Operator segments", summary.segments);
  if (summary.systems) ownerField(dl, "Named systems", summary.systems);
  if (summary.t4_permits) ownerField(dl, "T-4 permits", summary.t4_permits);

  const actions = document.createElement("div");
  actions.className = "pipeline-owners-actions";
  if (p5 || operator) {
    const all = document.createElement("button");
    all.type = "button";
    all.className = "ghost";
    all.textContent = "Show this operator";
    all.addEventListener("click", () => {
      focusPipeline({
        p5,
        operator: p5 ? "" : operator,
        system: "",
        west: summary.minx,
        south: summary.miny,
        east: summary.maxx,
        north: summary.maxy,
      });
    });
    actions.appendChild(all);
  }
  if (system) {
    const sys = document.createElement("button");
    sys.type = "button";
    sys.className = "ghost";
    sys.textContent = "Show this system";
    sys.addEventListener("click", () => {
      focusPipeline({
        p5,
        operator: p5 ? "" : operator,
        system,
        west: payload.minx ?? summary.minx,
        south: payload.miny ?? summary.miny,
        east: payload.maxx ?? summary.maxx,
        north: payload.maxy ?? summary.maxy,
      });
    });
    actions.appendChild(sys);
  }

  root.replaceChildren(head, dl);
  if (actions.childNodes.length) root.appendChild(actions);
  root.hidden = false;
  refreshMapSize();
}

function ensurePipelineOwnersNode() {
  const mapEl = document.getElementById("well-map");
  if (!mapEl) return null;
  let panel = document.getElementById("pipeline-owners");
  if (panel) return panel;
  panel = document.createElement("div");
  panel.id = "pipeline-owners";
  panel.className = "pipeline-owners";
  panel.hidden = true;
  const after = document.getElementById("pipeline-status") || mapEl;
  after.insertAdjacentElement("afterend", panel);
  return panel;
}

function bboxFromBounds(bounds) {
  return [bounds.getWest(), bounds.getSouth(), bounds.getEast(), bounds.getNorth()].join(",");
}

function disposalPopup(props) {
  const waste =
    Array.isArray(props.waste_classifications) && props.waste_classifications.length
      ? props.waste_classifications.join(" · ")
      : props.permit_type_label || props.permit_type || "";
  const lines = [
    props.operator || "",
    props.permit_no ? `Permit ${props.permit_no}` : "",
    waste ? `Accepted: ${waste}` : "",
    props.county ? `${props.county} County` : "",
  ].filter(Boolean);
  return locationPopupHtml({
    title: props.facility || props.permit_no || "Waste disposal site",
    lines,
    pointLabel: "Waste site",
    lat: props.lat,
    lon: props.lon,
    disposalId: props.id || "",
  });
}

function disposalMarkerStyle(selected) {
  const grow = coarsePointer() ? 4 : 0;
  return selected
    ? { radius: 8 + grow, color: "#1a1404", fillColor: "#e08a5c", fillOpacity: 1, weight: 2 }
    : { radius: 6 + grow, color: "#3a2218", fillColor: "#c45c3a", fillOpacity: 0.92, weight: 1 };
}

function disposalFeatureId(feature) {
  if (!feature) return "";
  const props = feature.properties || {};
  return String(feature.id ?? props.id ?? "");
}

function restyleDisposalMarkers() {
  if (!disposalLayer) return;
  disposalLayer.eachLayer((layer) => {
    if (!layer.setStyle) return;
    const selected = disposalFocus && disposalFeatureId(layer.feature) === String(disposalFocus.id);
    layer.setStyle(disposalMarkerStyle(selected));
  });
}

function restoreDisposalPopup() {
  if (!disposalLayer || !disposalPopupPinned || !disposalFocus) return;
  disposalLayer.eachLayer((layer) => {
    if (disposalFeatureId(layer.feature) === String(disposalFocus.id)) {
      layer.openPopup();
    }
  });
}

function ensureDisposalLayer() {
  if (!map) return null;
  if (!map.getPane("disposal")) {
    map.createPane("disposal");
    map.getPane("disposal").style.zIndex = 410;
  }
  if (!disposalLayer) {
    disposalLayer = L.geoJSON(null, {
      pane: "disposal",
      pointToLayer(feature, latlng) {
        const selected = disposalFocus && disposalFeatureId(feature) === String(disposalFocus.id);
        return L.circleMarker(latlng, {
          ...disposalMarkerStyle(selected),
          pane: "disposal",
          interactive: true,
        });
      },
      onEachFeature(feature, layer) {
        const props = feature.properties || {};
        layer.bindPopup(disposalPopup(props), locationPopupOptions());
        layer.on("popupopen", () => {
          disposalPopupPinned = true;
        });
        layer.on("popupclose", () => {
          if (!disposalReloading) disposalPopupPinned = false;
        });
        layer.on("click", (event) => {
          L.DomEvent.stop(event);
          if (event.originalEvent) L.DomEvent.stop(event.originalEvent);
          ignoreDisposalDismiss = true;
          selectDisposalFromLayer(layer);
          window.setTimeout(() => {
            ignoreDisposalDismiss = false;
          }, 0);
        });
      },
    });
    disposalLayer.addTo(map);
  }
  return disposalLayer;
}

function setDisposalStatus(text) {
  const el = document.getElementById("disposal-status");
  if (el) el.textContent = text || "";
}

function scheduleDisposal() {
  window.clearTimeout(disposalTimer);
  disposalTimer = window.setTimeout(loadDisposal, 220);
}

function loadDisposal() {
  if (!map) return;
  const legend = document.getElementById("disposal-legend");
  bindOverlayToggles();
  if (!disposalEnabled()) {
    if (disposalAbort) disposalAbort.abort();
    if (disposalLayer) disposalLayer.clearLayers();
    if (legend) legend.hidden = true;
    setDisposalStatus("");
    disposalKey = "off";
    return;
  }
  ensureDisposalLayer();
  const padded = map.getBounds().pad(0.12);
  const bbox = bboxFromBounds(padded);
  const focusId = disposalFocus && disposalFocus.id ? String(disposalFocus.id) : "";
  const key = `${bbox}|${focusId}`;
  if (key === disposalKey) return;
  disposalKey = key;
  if (disposalAbort) disposalAbort.abort();
  disposalAbort = new AbortController();
  fetch(`/disposal?bbox=${encodeURIComponent(bbox)}`, {
    signal: disposalAbort.signal,
    headers: { Accept: "application/json" },
  })
    .then((resp) => {
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      return resp.json();
    })
    .then((payload) => {
      if (!disposalLayer) return;
      disposalReloading = true;
      disposalLayer.clearLayers();
      if (payload && payload.features && payload.features.length) {
        disposalLayer.addData(payload);
      }
      if (window.WellnavOffline && payload) {
        window.WellnavOffline.putOverlay("disposal", payload, {
          west: padded.getWest(),
          south: padded.getSouth(),
          east: padded.getEast(),
          north: padded.getNorth(),
        }).catch(() => {});
      }
      disposalReloading = false;
      restyleDisposalMarkers();
      restoreDisposalPopup();
      if (legend) legend.hidden = false;
      const stored = (payload.meta && payload.meta.stored) || 0;
      if (!stored) {
        setDisposalStatus("No local waste-site overlay yet. Run python -m wellnav.ingest load-disposal");
      } else if (!payload.features.length) {
        setDisposalStatus("No commercial waste disposal sites in this view.");
      } else {
        setDisposalStatus("");
      }
    })
    .catch((err) => {
      disposalReloading = false;
      if (err && err.name === "AbortError") return;
      const offline = window.WellnavOffline;
      if (offline && !offline.isOnline()) {
        offline
          .getOverlay("disposal", {
            west: padded.getWest(),
            south: padded.getSouth(),
            east: padded.getEast(),
            north: padded.getNorth(),
          })
          .then((cached) => {
            if (!disposalLayer) return;
            if (cached && cached.features) {
              disposalReloading = true;
              disposalLayer.clearLayers();
              disposalLayer.addData(cached);
              disposalReloading = false;
              restyleDisposalMarkers();
              if (legend) legend.hidden = false;
              setDisposalStatus("Offline — showing the last waste sites saved for this area.");
              return;
            }
            setDisposalStatus("Offline — save this view while online to keep waste sites.");
          })
          .catch(() => setDisposalStatus("Offline — waste-site overlay unavailable."));
        return;
      }
      setDisposalStatus("Could not load waste disposal sites.");
    });
}

function siteFromEl(el) {
  if (!el) return null;
  const lat = Number(el.dataset.lat);
  const lon = Number(el.dataset.lon);
  const id = (el.dataset.disposalId || "").trim();
  if (!id || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return {
    id,
    lat,
    lon,
    name: el.dataset.name || "Waste disposal site",
    facility: el.dataset.name || "Waste disposal site",
    operator: el.dataset.operator || "",
    permit_no: el.dataset.permit || "",
    county: el.dataset.county || "",
  };
}

function wasteTextFromSite(site) {
  if (!site) return "";
  if (site.wasteText) return site.wasteText;
  if (Array.isArray(site.waste_classifications) && site.waste_classifications.length) {
    return site.waste_classifications.join(" · ");
  }
  if (window.WellnavDisposalUx && window.WellnavDisposalUx.wasteClassText) {
    return window.WellnavDisposalUx.wasteClassText(site) || "";
  }
  const bits = [site.permit_type_label || site.permit_type, site.discharge_type].filter(Boolean);
  return bits.join(" · ");
}

function selectDisposalSite(site, { fromMarker = false, enriched = false } = {}) {
  if (!site || !Number.isFinite(Number(site.lat)) || !Number.isFinite(Number(site.lon))) return;
  const store = loadStore();
  if (store.selected) {
    store.selected = null;
    saveStore(store);
  }
  disposalFocus = {
    id: String(site.id || site.disposalId || ""),
    lat: Number(site.lat),
    lon: Number(site.lon),
    name: site.facility || site.name || "Waste disposal site",
    operator: site.operator || "",
    permit: site.permit_no || site.permit || "",
    county: site.county || "",
    wasteText: wasteTextFromSite(site),
    permit_type_label: site.permit_type_label || "",
    discharge_type: site.discharge_type || "",
    waste_classifications: site.waste_classifications || [],
  };
  disposalPopupPinned = true;
  pinChrome = false;
  pipelinePin = null;
  if (pipelinePinMarker && map) {
    map.removeLayer(pipelinePinMarker);
    pipelinePinMarker = null;
  }
  localStorage.setItem(DISPOSAL_PREF, "1");
  const toggle = document.getElementById("disposal-toggle");
  if (toggle) toggle.checked = true;
  if (!ensureMap()) {
    updateChrome(loadStore());
    if (window.WellnavDisposalUx) window.WellnavDisposalUx.onDisposalSelected(disposalFocus);
    return;
  }
  if (fromMarker && disposalLayer) {
    restyleDisposalMarkers();
    updateChrome(loadStore());
    revealMapOnPhone();
    if (!enriched && disposalFocus.id && window.WellnavDisposalUx) {
      window.WellnavDisposalUx.enrichDisposalFocus(disposalFocus.id);
    } else if (window.WellnavDisposalUx) {
      window.WellnavDisposalUx.onDisposalSelected(disposalFocus);
    }
    return;
  }
  disposalKey = "";
  loadDisposal();
  map.setView([disposalFocus.lat, disposalFocus.lon], Math.max(map.getZoom(), 13));
  updateChrome(loadStore());
  revealMapOnPhone();
  if (window.WellnavDisposalUx) {
    if (!enriched && disposalFocus.id) window.WellnavDisposalUx.enrichDisposalFocus(disposalFocus.id);
    else window.WellnavDisposalUx.onDisposalSelected(disposalFocus);
  }
}

window.selectDisposalSite = selectDisposalSite;

function applyDisposalFocusFromEl(el) {
  const site = siteFromEl(el);
  if (site) selectDisposalSite(site);
}

function ensureChromeNodes() {
  const mapEl = document.getElementById("well-map");
  if (!mapEl) return;
  const chrome = mapEl.closest(".map-chrome") || mapEl.parentElement;
  if (!chrome) return;

  if (!document.getElementById("map-title") || !document.getElementById("map-sub")) {
    let head = chrome.querySelector(".map-head");
    if (!head) {
      head = document.createElement("div");
      head.className = "map-head";
      chrome.insertBefore(head, chrome.firstChild);
    }
    let titles = head.firstElementChild;
    if (!titles || titles.classList.contains("map-actions")) {
      titles = document.createElement("div");
      head.insertBefore(titles, head.firstChild);
    }
    if (!document.getElementById("map-title")) {
      const h2 = document.createElement("h2");
      h2.id = "map-title";
      h2.textContent = "Map";
      titles.insertBefore(h2, titles.firstChild);
    }
    if (!document.getElementById("map-sub")) {
      const p = document.createElement("p");
      p.id = "map-sub";
      p.className = "muted";
      document.getElementById("map-title").insertAdjacentElement("afterend", p);
    }
  }

  if (!document.getElementById("mapped-list")) {
    const list = document.createElement("ul");
    list.id = "mapped-list";
    list.className = "mapped-list";
    mapEl.insertAdjacentElement("afterend", list);
  }
  let footer = chrome.querySelector(".map-footer");
  if (!footer) {
    footer = document.createElement("div");
    footer.className = "map-footer";
    const after = document.getElementById("mapped-list") || mapEl;
    after.insertAdjacentElement("afterend", footer);
  }
  if (!document.getElementById("map-coords")) {
    const coords = document.createElement("div");
    coords.id = "map-coords";
    coords.className = "coord-bar";
    footer.appendChild(coords);
  } else {
    const coords = document.getElementById("map-coords");
    coords.classList.add("coord-bar");
    if (coords.parentElement !== footer) footer.appendChild(coords);
  }
  if (!document.getElementById("nav-links")) {
    const nav = document.createElement("div");
    nav.id = "nav-links";
    nav.className = "route-row";
    footer.appendChild(nav);
  } else {
    const nav = document.getElementById("nav-links");
    if (nav.parentElement !== footer) footer.appendChild(nav);
  }
  if (!document.getElementById("basemap-select")) {
    const head = chrome.querySelector(".map-head") || chrome;
    let actions = chrome.querySelector(".map-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "map-actions";
      head.appendChild(actions);
    }
    const label = document.createElement("label");
    label.className = "basemap-picker";
    label.append("Base layer ");
    const select = document.createElement("select");
    select.id = "basemap-select";
    label.appendChild(select);
    actions.appendChild(label);
  }
  if (!document.getElementById("pipeline-toggle")) {
    const head = chrome.querySelector(".map-head") || chrome;
    let actions = chrome.querySelector(".map-actions");
    if (!actions) {
      actions = document.createElement("div");
      actions.className = "map-actions";
      head.appendChild(actions);
    }
    const pipe = document.createElement("label");
    pipe.className = "overlay-toggle";
    pipe.innerHTML = '<input type="checkbox" id="pipeline-toggle"> Pipelines';
    actions.append(pipe);
  }
  if (!document.getElementById("disposal-toggle")) {
    const actions = chrome.querySelector(".map-actions");
    if (actions) {
      const disposal = document.createElement("label");
      disposal.className = "overlay-toggle";
      disposal.innerHTML = '<input type="checkbox" id="disposal-toggle"> Waste sites';
      actions.appendChild(disposal);
    }
  }
  if (!document.getElementById("pipeline-status")) {
    const status = document.createElement("p");
    status.id = "pipeline-status";
    status.className = "muted pipeline-status";
    const legend = document.getElementById("pipeline-legend");
    (legend || mapEl).insertAdjacentElement("afterend", status);
  }
  if (!document.getElementById("pipeline-owners")) {
    const panel = document.createElement("div");
    panel.id = "pipeline-owners";
    panel.className = "pipeline-owners";
    panel.hidden = true;
    const after = document.getElementById("pipeline-status") || mapEl;
    after.insertAdjacentElement("afterend", panel);
  }
  if (!document.getElementById("disposal-status")) {
    const status = document.createElement("p");
    status.id = "disposal-status";
    status.className = "muted pipeline-status";
    const legend = document.getElementById("disposal-legend");
    (legend || mapEl).insertAdjacentElement("afterend", status);
  }
  ensureOfflineControls();
}

function ensureMapChrome() {
  if (document.getElementById("well-map")) {
    ensureChromeNodes();
    return true;
  }
  const panel = document.getElementById("map-panel");
  if (!panel) return false;
  panel.innerHTML = MAP_CHROME_HTML;
  bindBasemapSelect();
  bindOverlayToggles();
  bindOfflinePack();
  return !!document.getElementById("well-map");
}

function observeMapSize(el) {
  if (!el || typeof ResizeObserver === "undefined") return;
  if (mapSizeObserver) {
    try {
      mapSizeObserver.disconnect();
    } catch {
      /* ignore */
    }
  }
  mapSizeObserver = new ResizeObserver(() => {
    if (map) map.invalidateSize({ animate: false });
  });
  mapSizeObserver.observe(el);
}

function destroyMap() {
  overlays.clear();
  if (mapSizeObserver) {
    try {
      mapSizeObserver.disconnect();
    } catch {
      /* ignore */
    }
    mapSizeObserver = null;
  }
  if (pipelineAbort) {
    pipelineAbort.abort();
    pipelineAbort = null;
  }
  if (disposalAbort) {
    disposalAbort.abort();
    disposalAbort = null;
  }
  pipelineLayer = null;
  pipelineKey = "";
  disposalLayer = null;
  disposalKey = "";
  pipelinePinMarker = null;
  pipelinePin = null;
  pinChrome = false;
  routePinLayer = null;
  routeLineLayer = null;
  userLocationMarker = null;
  navRemainLine = null;
  navJoinLine = null;
  if (map) {
    try {
    map.remove();
    } catch {
      /* container may already be gone after an HTMX swap */
    }
  }
    map = null;
  activeBase = null;
  wellLayer = null;
  }

function ensureMap() {
  if (typeof L === "undefined") return null;
  if (!ensureMapChrome()) return null;
  const el = document.getElementById("well-map");
  if (!el) return null;

  if (map && map.getContainer() === el) {
    el.classList.remove("is-idle");
    bindBasemapSelect();
    bindOverlayToggles();
    bindOfflinePack();
    observeMapSize(el);
    schedulePipelines();
    scheduleDisposal();
    restoreOfflineRoutePack();
    window.setTimeout(() => {
      if (map) map.invalidateSize({ animate: false });
    }, 80);
    return map;
  }

  destroyMap();
  if (el._leaflet_id) {
    el._leaflet_id = null;
    el.innerHTML = "";
  }
  el.classList.remove("is-idle");

  map = L.map(el, {
    zoomControl: true,
    attributionControl: false,
    closePopupOnClick: true,
    tapTolerance: coarsePointer() ? 32 : 15,
  }).setView([31.2, -99.2], 6);
  if (map.attributionControl) map.removeControl(map.attributionControl);
  L.control.attribution({ position: "topright", prefix: false }).addTo(map);
  map.createPane("pipelines");
  map.getPane("pipelines").style.zIndex = 350;
  map.createPane("disposal");
  map.getPane("disposal").style.zIndex = 410;
  map.createPane("pipeline-pin");
  map.getPane("pipeline-pin").style.zIndex = 450;
  setBasemap(preferredBasemap(), { persist: false });
  wellLayer = L.layerGroup().addTo(map);
  bindBasemapSelect();
  bindOverlayToggles();
  bindOfflinePack();
  observeMapSize(el);
  map.on("click", (event) => {
    if (pinMode) {
      addRoutePin(event.latlng);
      return;
    }
    const disposal = event && event.latlng && nearestDisposal(event.latlng, disposalHitPx());
    if (!disposal) disposalPopupPinned = false;
    pickPipelineAt(event);
  });
  map.on("contextmenu", (event) => {
    if (event.originalEvent) event.originalEvent.preventDefault();
    addRoutePin(event.latlng);
  });
  map.on("moveend", schedulePipelines);
  map.on("zoomend", schedulePipelines);
  map.on("moveend", scheduleDisposal);
  map.on("zoomend", scheduleDisposal);
  map.on("moveend", persistMapView);
  map.on("zoomend", persistMapView);
  map.on("dragstart", onNavDrag);
  if (!loadStore().order.length) restoreMapView();
  schedulePipelines();
  scheduleDisposal();
  restoreOfflineRoutePack();
  window.setTimeout(() => {
    if (map) map.invalidateSize({ animate: false });
  }, 80);
  return map;
}

function wellheadPopup(well) {
  return locationPopupHtml({
    title: well.name || formatApi(well.api, well.state),
    detail: wellSubtitle(well),
    pointLabel: "Wellhead",
    showPointLabel: false,
    showCoords: false,
    compact: true,
    lat: well.lat,
    lon: well.lon,
  });
}

function markerStyle(selected) {
  return selected
    ? { radius: 9, color: "#1a1404", fillColor: "#d4a017", fillOpacity: 1, weight: 2 }
    : { radius: 7, color: "#2a331c", fillColor: "#7cb36a", fillOpacity: 0.92, weight: 1 };
}

function removeOverlay(api) {
  const layer = overlays.get(api);
  if (!layer || !wellLayer) {
    overlays.delete(api);
    return;
  }
  ["head", "toe", "line"].forEach((key) => {
    if (layer[key]) wellLayer.removeLayer(layer[key]);
  });
  overlays.delete(api);
}

function upsertOverlay(well, selected) {
  if (!wellLayer || !Number.isFinite(well.lat) || !Number.isFinite(well.lon)) return;
  let layer = overlays.get(well.api);
  if (!layer) {
    const head = L.circleMarker([well.lat, well.lon], markerStyle(selected))
      .bindPopup(wellheadPopup(well), locationPopupOptions({ compact: true }));
    head.on("click", () => selectWell(well.api));
    wellLayer.addLayer(head);
    layer = { head, toe: null, line: null };
    overlays.set(well.api, layer);
  } else {
    layer.head.setLatLng([well.lat, well.lon]);
    layer.head.setStyle(markerStyle(selected));
    layer.head.setPopupContent(wellheadPopup(well));
    if (layer.toe) {
      wellLayer.removeLayer(layer.toe);
      layer.toe = null;
    }
    if (layer.line) {
      wellLayer.removeLayer(layer.line);
      layer.line = null;
    }
  }

  if (selected && hasToe(well)) {
    layer.line = L.polyline(
      [
        [well.lat, well.lon],
        [well.toeLat, well.toeLon],
      ],
      { color: "#d4a017", weight: 2, dashArray: "6 6" }
    );
    layer.toe = L.circleMarker([well.toeLat, well.toeLon], {
      radius: 6,
      color: "#d4a017",
      fillColor: "#7cb36a",
      fillOpacity: 0.9,
      weight: 2,
    }).bindPopup(
      locationPopupHtml({
        title: well.name || "Toe / bottom hole",
        lines: well.name
          ? ["Toe / bottom hole", "RRC default mapped point"]
          : ["RRC default mapped point"],
        pointLabel: "Toe",
        showPointLabel: false,
        lat: well.toeLat,
        lon: well.toeLon,
      }),
      locationPopupOptions()
    );
    wellLayer.addLayer(layer.line);
    wellLayer.addLayer(layer.toe);
  }

  if (selected && !searchInputIsActive()) layer.head.openPopup();
  else if (!selected) layer.head.closePopup();
}

function renderOverlays(store) {
  if (!wellLayer) return;
  [...overlays.keys()].forEach((api) => {
    if (!store.wells[api]) removeOverlay(api);
  });
  store.order.forEach((api) => {
    const well = store.wells[api];
    if (well) upsertOverlay(well, store.selected === api);
  });
}

function fitToWells(store) {
  if (!map) return;
  const points = [];
  store.order.forEach((api) => {
    const well = store.wells[api];
    if (!well || !Number.isFinite(well.lat) || !Number.isFinite(well.lon)) return;
    points.push([well.lat, well.lon]);
    if (store.selected === api && hasToe(well)) {
      points.push([well.toeLat, well.toeLon]);
    }
  });
  if (!points.length) return;
  if (points.length === 1) {
    map.setView(points[0], 15);
    return;
  }
  map.fitBounds(points, { padding: [40, 40], maxZoom: FIT_MAX_ZOOM });
}

function coordChip(title, lat, lon, hint) {
  const chip = document.createElement("div");
  chip.className = "coord-chip";
  const label = document.createElement("span");
  label.className = "coord-chip-label";
  label.textContent = title;
  const mono = document.createElement("span");
  mono.className = "mono";
  mono.textContent = `${Number(lat).toFixed(6)}, ${Number(lon).toFixed(6)}`;
  chip.append(label, mono);
  if (hint) {
    const muted = document.createElement("span");
    muted.className = "muted";
    muted.textContent = hint;
    chip.appendChild(muted);
  }
  return chip;
}

function locationPopupOptions(opts = {}) {
  const phone = window.matchMedia("(max-width: 960px)").matches;
  const mapEl = document.getElementById("well-map");
  const sheetPad = phone && mapEl ? Math.max(120, Math.round(mapEl.clientHeight * 0.38)) : 16;
  const bottomPad = opts.compact ? (phone ? 28 : 12) : sheetPad;
  return {
    className: opts.compact ? "loc-popup loc-popup-compact" : "loc-popup",
    autoClose: true,
    closeOnClick: true,
    closeOnEscapeKey: true,
    maxWidth: opts.compact ? 220 : 300,
    autoPan: true,
    autoPanPaddingTopLeft: opts.compact ? [8, 8] : [16, 16],
    autoPanPaddingBottomRight: [opts.compact ? 8 : 16, bottomPad],
  };
}

function locationPopupHtml(place) {
  const lat = Number(place.lat);
  const lon = Number(place.lon);
  const parts = [];
  if (place.title) parts.push(`<strong>${escapeHtml(place.title)}</strong>`);
  if (!place.compact) {
    (place.lines || []).forEach((line) => {
      if (line) parts.push(escapeHtml(line));
    });
    if (place.pointLabel && place.showPointLabel !== false) parts.push(escapeHtml(place.pointLabel));
    if (Number.isFinite(lat) && Number.isFinite(lon) && place.showCoords !== false) {
      parts.push(escapeHtml(`${lat.toFixed(6)}, ${lon.toFixed(6)}`));
    }
  }
  let html = parts.join("<br>");
  const hasCoords = Number.isFinite(lat) && Number.isFinite(lon);
  if (!hasCoords) return html;
  const detail = [place.detail, ...(place.lines || [])].filter(Boolean).join("\n");
  html += mapsShareMarkup({
    title: place.title || place.pointLabel || "Location",
    detail,
    pointLabel: place.pointLabel || "Location",
    lat,
    lon,
    disposalId: place.disposalId || "",
    compact: !!place.compact,
  });
  return html;
}

function attrText(value) {
  return escapeHtml(String(value ?? "")).replace(/\r\n|\r|\n/g, "&#10;");
}

function mapsShareMarkup(place) {
  const lat = Number(place.lat);
  const lon = Number(place.lon);
  const coords = `${lat.toFixed(6)}, ${lon.toFixed(6)}`;
  const apple = `https://maps.apple.com/?daddr=${lat},${lon}`;
  const google = `https://maps.google.com/maps/dir/?api=1&destination=${lat},${lon}`;
  const disposalId = place.disposalId ? attrText(place.disposalId) : "";
  const compact = !!place.compact;
  const textLabel = compact ? "Text" : "Text message";
  const prompt = compact ? "" : `<p class="loc-share-prompt"></p>`;
  return (
    `<div class="loc-share${compact ? " loc-share-compact" : ""}" data-share-title="${attrText(place.title || "Location")}"` +
    ` data-share-detail="${attrText(place.detail || "")}"` +
    ` data-share-point="${attrText(place.pointLabel || "Location")}"` +
    ` data-share-coords="${attrText(coords)}"` +
    ` data-share-apple="${attrText(apple)}"` +
    ` data-share-google="${attrText(google)}"` +
    (disposalId ? ` data-share-disposal-id="${disposalId}"` : "") +
    `>` +
    `<div class="loc-share-platforms" role="group" aria-label="Choose a maps link">` +
    `<button type="button" class="route apple" data-share-platform="apple" aria-pressed="false" title="Double-click to open Apple Maps">Apple Maps</button>` +
    `<button type="button" class="route google" data-share-platform="google" aria-pressed="false" title="Double-click to open Google Maps">Google Maps</button>` +
    `</div>` +
    `<div class="loc-share-via" hidden>` +
    prompt +
    `<div class="loc-share-actions">` +
    `<a class="ghost" data-share-via="sms">${textLabel}</a>` +
    `<a class="ghost" data-share-via="email">Email</a>` +
    `</div></div></div>`
  );
}

function pipelinePinPopup(pin) {
  const title = pin.operator || pin.system || "Pipeline point";
  const lines = [];
  if (pin.system && pin.system !== title) lines.push(pin.system);
  if (pin.commodity) lines.push(pin.commodity);
  return locationPopupHtml({
    title,
    lines,
    pointLabel: "Pipeline point",
    showPointLabel: title !== "Pipeline point",
    lat: pin.lat,
    lon: pin.lon,
  });
}

function locationShareBody(root, platform) {
  const title = root.dataset.shareTitle || "Location";
  const detail = root.dataset.shareDetail || "";
  const point = root.dataset.sharePoint || "Location";
  const coords = root.dataset.shareCoords || "";
  const link = platform === "google" ? root.dataset.shareGoogle : root.dataset.shareApple;
  const platformName = platform === "google" ? "Google Maps" : "Apple Maps";
  const lines = [title];
  if (detail) lines.push(detail);
  if (coords) lines.push(`${point}: ${coords}`);
  lines.push("", platformName, link || "");
  return lines.join("\n").replace(/\n/g, "\r\n");
}

function prefersIosSms() {
  const ua = navigator.userAgent || "";
  if (/iPhone|iPad|iPod/i.test(ua)) return true;
  return navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
}

function deviceShareHref(via, subject, body) {
  if (via === "email") {
    return `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }
  const encoded = encodeURIComponent(body);
  return prefersIosSms() ? `sms:&body=${encoded}` : `sms:?body=${encoded}`;
}

function refreshLocationPopup() {
  const popup = map && map._popup;
  if (!popup || !popup.isOpen() || !popup._map || !popup._container) return;
  if (typeof popup._updateLayout !== "function" || typeof popup._updatePosition !== "function") return;
  const container = popup._container;
  // Leaflet's update() rewrites popup HTML and would collapse the share step.
  container.style.visibility = "hidden";
  try {
    popup._updateLayout();
    popup._updatePosition();
    if (typeof popup._adjustPan === "function") popup._adjustPan();
  } finally {
    container.style.visibility = "";
  }
}

function selectSharePlatform(root, platform) {
  root.dataset.platform = platform;
  root.querySelectorAll("[data-share-platform]").forEach((btn) => {
    const on = btn.dataset.sharePlatform === platform;
    btn.classList.toggle("is-selected", on);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
  });
  const via = root.querySelector(".loc-share-via");
  const prompt = root.querySelector(".loc-share-prompt");
  const name = platform === "google" ? "Google Maps" : "Apple Maps";
  const title = root.dataset.shareTitle || "Location";
  const body = locationShareBody(root, platform);
  const sms = root.querySelector('[data-share-via="sms"]');
  const email = root.querySelector('[data-share-via="email"]');
  if (sms) sms.href = deviceShareHref("sms", title, body);
  if (email) email.href = deviceShareHref("email", title, body);
  if (prompt) prompt.textContent = `Send the ${name} link`;
  if (via) via.hidden = false;
  window.requestAnimationFrame(refreshLocationPopup);
}

function openMapsLink(href, disposalId) {
  if (!href) return;
  const a = document.createElement("a");
  a.className = "route";
  a.target = "_blank";
  a.rel = "noopener";
  a.href = href;
  if (disposalId) a.dataset.disposalId = String(disposalId);
  document.body.appendChild(a);
  a.click();
  a.remove();
}

let lastMapsOpen = 0;

function openMapsFromShare(root, platform) {
  const now = Date.now();
  if (now - lastMapsOpen < 700) return;
  lastMapsOpen = now;
  const href = platform === "google" ? root.dataset.shareGoogle : root.dataset.shareApple;
  openMapsLink(href, root.dataset.shareDisposalId || "");
}

function sharePlatformButton(target) {
  if (!target || !target.closest) return null;
  return target.closest("[data-share-platform]");
}

function onLocationShareClick(event) {
  const platformBtn = sharePlatformButton(event.target);
  if (!platformBtn) return;
  event.preventDefault();
  const root = platformBtn.closest(".loc-share");
  if (!root) return;
  const platform = platformBtn.dataset.sharePlatform;
  const now = Date.now();
  const last = Number(root.dataset.shareClickAt || 0);
  if (root.dataset.platform === platform && now - last < 450) {
    root.dataset.shareClickAt = "0";
    openMapsFromShare(root, platform);
    return;
  }
  root.dataset.shareClickAt = String(now);
  selectSharePlatform(root, platform);
}

function onLocationShareDblClick(event) {
  const platformBtn = sharePlatformButton(event.target);
  if (!platformBtn) return;
  event.preventDefault();
  event.stopPropagation();
  const root = platformBtn.closest(".loc-share");
  if (!root) return;
  openMapsFromShare(root, platformBtn.dataset.sharePlatform);
}

function bindLocationShare() {
  if (document.documentElement.dataset.locationShare === "1") return;
  document.documentElement.dataset.locationShare = "1";
  document.addEventListener("click", onLocationShareClick, true);
  document.addEventListener("dblclick", onLocationShareDblClick, true);
}

function updateChrome(store) {
  ensureChromeNodes();
  syncMapTabCount(store);
  const selected = store.selected ? store.wells[store.selected] : null;
  const title = document.getElementById("map-title");
  const sub = document.getElementById("map-sub");
  const list = document.getElementById("mapped-list");
  const coords = document.getElementById("map-coords");
  const nav = document.getElementById("nav-links");

  const showPin =
    pinChrome && pipelinePin && Number.isFinite(pipelinePin.lat) && Number.isFinite(pipelinePin.lon);
  const chrome = document.querySelector(".map-chrome");
  if (chrome) {
    const next = showPin ? "pipeline" : disposalFocus ? "disposal" : selected ? "well" : "idle";
    const changed = chrome.dataset.focus !== next;
    chrome.dataset.focus = next;
    if (changed) refreshMapSize();
  }

  if (title) {
    if (showPin) title.textContent = pipelinePin.operator || pipelinePin.system || "Pipeline point";
    else if (disposalFocus) title.textContent = disposalFocus.name;
    else title.textContent = selected ? selected.name || formatApi(selected.api) : "Map";
  }
  if (sub) {
    if (showPin) {
      // Keep subtitle short — ownership panel already lists operator/system/commodity.
      sub.textContent = pipelinePin.system && pipelinePin.operator
        ? "Pinned pipeline point"
        : pipelinePin.system || pipelinePin.commodity || "Pinned pipeline point";
    } else if (disposalFocus) {
      // Waste classifications live in #disposal-waste-classes — keep subtitle lean.
      const bits = [];
      if (disposalFocus.operator) bits.push(disposalFocus.operator);
      if (disposalFocus.permit) bits.push(disposalFocus.permit);
      if (disposalFocus.county) bits.push(`${disposalFocus.county} County`);
      sub.textContent = bits.length ? bits.join(" · ") : "Commercial waste disposal";
    } else if (selected) sub.textContent = "";
    else if (store.order.length) sub.textContent = "Select a well to route.";
    else sub.textContent = "Pin wells from search to show them on the map.";
  }

  if (list) {
    list.replaceChildren();
    if (store.order.length < 2) {
      list.hidden = true;
    } else {
      list.hidden = false;
    store.order.forEach((api) => {
      const well = store.wells[api];
      if (!well) return;
      const li = document.createElement("li");
      li.className = "mapped-item" + (store.selected === api ? " selected" : "");
      li.dataset.api = api;

      const pick = document.createElement("button");
      pick.type = "button";
      pick.className = "mapped-item-select";
      pick.textContent = well.name || formatApi(api);
      pick.title = wellSubtitle(well);
      pick.addEventListener("click", () => selectWell(api));

      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "mapped-item-remove";
      remove.setAttribute("aria-label", `Remove ${pick.textContent} from map`);
      remove.textContent = "×";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        removeWell(api);
      });

      li.append(pick, remove);
      list.appendChild(li);
    });
    }
  }

  if (coords) {
    coords.replaceChildren();
    if (showPin) {
      coords.appendChild(coordChip("Pipeline", pipelinePin.lat, pipelinePin.lon, "Pinned point"));
    } else if (disposalFocus) {
      coords.appendChild(coordChip("Waste site", disposalFocus.lat, disposalFocus.lon, "WGS84"));
    }
  }

  if (nav) {
    nav.replaceChildren();
    const destLat = showPin ? pipelinePin.lat : disposalFocus ? disposalFocus.lat : selected && selected.lat;
    const destLon = showPin ? pipelinePin.lon : disposalFocus ? disposalFocus.lon : selected && selected.lon;
    if (Number.isFinite(destLat) && Number.isFinite(destLon)) {
      if (showPin) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "ghost pipeline-pin-remove";
        remove.textContent = "Remove pin";
        remove.addEventListener("click", () => clearPipelinePin());
        nav.appendChild(remove);
      } else if (disposalFocus) {
        const clear = document.createElement("button");
        clear.type = "button";
        clear.className = "ghost";
        clear.textContent = "Clear site";
        clear.addEventListener("click", () => {
          disposalFocus = null;
          disposalPopupPinned = false;
          if (window.WellnavDisposalUx) window.WellnavDisposalUx.hideWaitPanel();
          restyleDisposalMarkers();
          updateChrome(loadStore());
        });
        nav.appendChild(clear);
      } else if (store.order.length === 1 && store.selected) {
        const remove = document.createElement("button");
        remove.type = "button";
        remove.className = "ghost";
        remove.textContent = "Remove";
        remove.addEventListener("click", () => removeWell(store.selected));
        nav.appendChild(remove);
      }
    }
  }

  // Do not re-fetch wait reports on every chrome refresh — selectDisposalSite owns that.
  if ((showPin || !disposalFocus) && window.WellnavDisposalUx) {
    window.WellnavDisposalUx.hideWaitPanel();
  }
}

function paint({ fit = false } = {}) {
  const store = loadStore();
  if (!ensureMap()) {
    updateChrome(store);
    syncMapButtons();
    return;
  }
  if (!store.order.length) {
    [...overlays.keys()].forEach(removeOverlay);
  } else {
    renderOverlays(store);
    if (fit) fitToWells(store);
  }
  updateChrome(store);
  syncMapButtons();
  window.setTimeout(() => {
    if (map) map.invalidateSize();
  }, 60);
}

function selectWell(api) {
  const store = loadStore();
  if (!store.wells[api]) return;
  store.selected = api;
  saveStore(store);
  disposalFocus = null;
  disposalPopupPinned = false;
  pinChrome = false;
  if (window.WellnavDisposalUx) window.WellnavDisposalUx.hideWaitPanel();
  if (map) map.closePopup();
  revealMapOnPhone();
  paint({ fit: false });
}

function removeWell(api) {
  removeFromStore(api);
  removeOverlay(api);
  paint({ fit: true });
}

function addWell(well, { select = false } = {}) {
  const { added, isNew } = upsertIntoStore(well, { select });
  if (!added && isNew === false && !select) return;
  revealMapOnPhone();
  paint({ fit: isNew });
}

function toggleWellOnMap(btn) {
  const well = wellFromButton(btn);
  if (!well) {
    markNoCoords(btn);
    return;
  }
  const store = loadStore();
  if (store.wells[well.api]) {
    removeWell(well.api);
    return;
  }
  addWell(well, { select: !store.selected });
}

function addAndSelectWell(btn) {
  const well = wellFromButton(btn);
  if (!well) {
    markNoCoords(btn);
    return;
  }
  const existed = !!loadStore().wells[well.api];
  upsertIntoStore(well, { select: true });
  revealMapOnPhone();
  paint({ fit: !existed });
}

function mapAllVisibleWells() {
  let addedAny = false;
  document.querySelectorAll("tr.well-row").forEach((row) => {
    const well = wellFromRow(row);
    if (!well) {
      markNoCoords(row.querySelector(".map-toggle"));
      return;
    }
    const { added, isNew } = upsertIntoStore(well, { select: false });
    if (added && isNew) addedAny = true;
  });
  const store = loadStore();
  if (!store.selected && store.order.length) {
    store.selected = store.order[0];
    saveStore(store);
  }
  revealMapOnPhone();
  paint({ fit: addedAny || !!store.order.length });
}

function clearMappedWells() {
  saveStore(emptyStore());
  [...overlays.keys()].forEach(removeOverlay);
  paint({ fit: false });
}

function markRowsSaved(apis) {
  const wanted = new Set((apis || []).filter(Boolean));
  if (!wanted.size) return;
  document.querySelectorAll("tr.well-row").forEach((row) => {
    const api = (row.dataset.api || "").trim();
    if (!wanted.has(api)) return;
    row.classList.add("is-saved");
    const btn = row.querySelector(".save-form .save-btn");
    if (!btn) return;
    btn.classList.add("on");
    btn.textContent = btn.classList.contains("save-star") ? "★" : "★ Saved";
    btn.setAttribute("aria-pressed", "true");
    btn.title = "Remove from saved wells";
  });
}

function applySaveStatus(root) {
  const status = root && root.querySelector ? root.querySelector("[data-apis]") : null;
  const marked = status || document.querySelector("#save-status [data-apis]");
  if (!marked) return;
  markRowsSaved(String(marked.dataset.apis || "").split(","));
}

function setToggleLabel(btn, on) {
  const label = on ? "Unpin" : "Pin";
  btn.textContent = label;
}

function syncMapButtons() {
  const store = loadStore();
  document.querySelectorAll("tr.well-row").forEach((row) => {
    const well = wellFromRow(row);
    const api = (row.dataset.api || "").trim();
    const mapped = !!(api && store.wells[api]);
    const selected = store.selected === api;
    row.classList.toggle("is-mapped", mapped);
    row.classList.toggle("is-selected", selected);
    const btn = row.querySelector(".map-toggle");
    if (!btn) return;
    btn.classList.toggle("on", mapped);
    btn.setAttribute("aria-pressed", mapped ? "true" : "false");
    setToggleLabel(btn, mapped);
    if (!well) {
      btn.title = "No coordinates";
      btn.disabled = true;
    } else {
      btn.disabled = false;
      btn.title = mapped ? "Remove from map" : "Add to map";
    }
  });
}

function initWellMap() {
  let el = document.getElementById("well-map");
  if (!el) {
    if (!document.getElementById("map-panel")) {
      syncMapButtons();
      return;
    }
    if (!ensureMapChrome()) {
      syncMapButtons();
      return;
    }
    el = document.getElementById("well-map");
    if (!el) {
      syncMapButtons();
      return;
    }
  }

  ensureChromeNodes();
  const incoming = wellFromMapEl(el);
  if (incoming) {
    upsertIntoStore(incoming, { select: true });
  }

  const preferApi = (el.dataset.api || "").trim();
  if (preferApi) {
    const store = loadStore();
    if (store.wells[preferApi]) {
      store.selected = preferApi;
      saveStore(store);
    }
  }

  const store = loadStore();
  if (!ensureMap()) {
    updateChrome(store);
    syncMapButtons();
    return;
  }
  renderOverlays(store);
  if (store.order.length) fitToWells(store);
  updateChrome(store);
  syncMapButtons();
}

window.toggleWellOnMap = toggleWellOnMap;
window.addAndSelectWell = addAndSelectWell;
window.mapAllVisibleWells = mapAllVisibleWells;
window.clearMappedWells = clearMappedWells;
window.syncMapButtons = syncMapButtons;
window.initWellMap = initWellMap;
window.setBasemap = setBasemap;

const SUGGEST_TRIGGER =
  "input[this.value.trim().length>=2] changed delay:300ms, keyup[this.value.trim().length>=2] changed delay:300ms, search[this.value.trim().length>=2]";

function searchScope() {
  const el = document.querySelector("#search-form [name=scope]");
  if (!el) return "wells";
  if (el.tagName === "SELECT") return el.value || "wells";
  return document.querySelector("#search-form [name=scope]:checked")?.value || "wells";
}

function wellSearchMode() {
  return document.querySelector("#search-form [name=mode]:checked")?.value || "name";
}

function liveWellQuery() {
  const mode = wellSearchMode();
  return searchScope() === "wells" && (mode === "name" || mode === "api");
}

function refreshLiveResults() {
  const q = document.getElementById("q");
  if (!q || !window.htmx || !liveWellQuery()) return;
  const text = q.value.trim();
  const hasResults = !!document.querySelector("#results table.wells");
  const hasFilters = !!document.querySelector(
    "#active-filters .filter-chip, #search-form input[name='lease_no']"
  );
  if (text.length >= 1 || hasResults || hasFilters) window.htmx.trigger(q, "dofilter");
}

function applySearchContext({ refetch = false } = {}) {
  const form = document.getElementById("search-form");
  const q = document.getElementById("q");
  const spinner = document.getElementById("spinner");
  if (!form) return;
  const scope = searchScope();
  const pipelines = scope === "pipelines";
  const disposal = scope === "disposal";
  const live = !pipelines && !disposal && liveWellQuery();
  form.setAttribute("action", pipelines ? "/pipelines/search" : disposal ? "/disposal/search" : "/search");
  form.setAttribute("hx-get", pipelines ? "/pipelines/search" : disposal ? "/disposal/search" : "/search");
  if (q) {
    if (live) {
      q.setAttribute("hx-get", "/search");
      q.setAttribute("hx-target", "#results");
      q.setAttribute("hx-swap", "innerHTML");
      q.setAttribute("hx-trigger", "input changed delay:280ms, dofilter");
      q.setAttribute("hx-include", "#search-form, #column-filters");
      q.setAttribute("hx-indicator", "#spinner");
      q.setAttribute("hx-sync", "this:replace");
      q.setAttribute("hx-headers", '{"X-Live-Filter":"1"}');
      q.removeAttribute("hx-params");
    } else {
      q.setAttribute("hx-get", pipelines ? "/pipelines/suggest" : disposal ? "/disposal/suggest" : "/operators");
      q.setAttribute("hx-trigger", SUGGEST_TRIGGER);
      q.setAttribute("hx-target", "#operator-suggest");
      q.setAttribute("hx-include", "[name=mode],[name=state],[name=pipe_mode],[name=disp_mode],[name=scope]");
      q.setAttribute(
        "hx-params",
        pipelines ? "q,pipe_mode,scope,state" : disposal ? "q,disp_mode,scope,state" : "q,mode,state"
      );
      q.setAttribute("hx-sync", "this:abort");
      q.removeAttribute("hx-indicator");
      q.removeAttribute("hx-headers");
      q.removeAttribute("hx-swap");
    }
  }
  if (spinner) {
    spinner.textContent = pipelines
      ? "Searching pipeline records…"
      : disposal
        ? "Searching waste disposal sites…"
        : "Searching local well tables…";
  }
  if (window.htmx) {
    const qEl = q;
    const active = qEl && document.activeElement === qEl;
    const start = qEl ? qEl.selectionStart : null;
    const end = qEl ? qEl.selectionEnd : null;
    const value = qEl ? qEl.value : "";
    window.htmx.process(form);
    if (qEl) window.htmx.process(qEl);
    if (active && qEl) {
      qEl.focus();
      if (qEl.value !== value) qEl.value = value;
      if (typeof start === "number" && typeof end === "number") {
        try {
          qEl.setSelectionRange(start, end);
        } catch {
          /* type=search may reject selection in some browsers */
        }
      }
    }
  }
  if (refetch) refreshLiveResults();
}

function bindSearchContext() {
  const form = document.getElementById("search-form");
  if (!form || form.dataset.scopeBound === "1") return;
  form.dataset.scopeBound = "1";
  form.addEventListener(
    "change",
    (event) => {
      const name = event.target.name;
      const box = document.getElementById("q");
      if (
        box &&
        ((name === "use_name" && !event.target.checked && wellSearchMode() === "name") ||
          (name === "use_api" && !event.target.checked && wellSearchMode() === "api"))
      ) {
        box.value = "";
        box.dataset.suppressLive = "1";
        if (window.htmx) window.htmx.trigger(box, "htmx:abort");
      }
      if (name === "mode") {
        const next = event.target.value;
        const typed = box && box.value.trim();
        if (typed && (next === "name" || next === "api")) {
          const drop = next === "name" ? "api" : "name";
          document
            .querySelectorAll("#active-filters [name=" + drop + "], #active-filters [name=use_" + drop + "]")
            .forEach((el) => el.remove());
        }
      }
      if (name === "scope" || name === "pipe_mode" || name === "disp_mode" || name === "state" || name === "mode") {
        applySearchContext({ refetch: name === "mode" || name === "state" || name === "scope" });
      }
    },
    true
  );
  form.addEventListener(
    "click",
    (event) => {
      const btn = event.target.closest("[name=remove_name], [name=remove_api]");
      if (!btn) return;
      const mode = wellSearchMode();
      if ((btn.name === "remove_name" && mode !== "name") || (btn.name === "remove_api" && mode !== "api")) return;
      const box = document.getElementById("q");
      if (!box) return;
      box.value = "";
      box.dataset.suppressLive = "1";
      if (window.htmx) window.htmx.trigger(box, "htmx:abort");
    },
    true
  );
  const q = document.getElementById("q");
  if (q && q.dataset.liveBound !== "1") {
    q.dataset.liveBound = "1";
    q.addEventListener("input", () => {
      delete q.dataset.suppressLive;
    });
    q.addEventListener("search", () => {
      if (!q.value.trim() && liveWellQuery() && window.htmx) window.htmx.trigger(q, "dofilter");
    });
  }
  applySearchContext();
}

document.addEventListener("pointerdown", (event) => {
  if (ignoreDisposalDismiss || !disposalPopupPinned) return;
  if (event.target.closest(".leaflet-popup, .leaflet-interactive")) return;
  disposalPopupPinned = false;
  if (map) map.closePopup();
});

document.addEventListener("click", (event) => {
  const action = event.target.closest("[data-map-action]");
  if (action) {
    event.preventDefault();
    const kind = action.dataset.mapAction;
    if (kind === "page") mapAllVisibleWells();
    else if (kind === "clear") clearMappedWells();
    return;
  }
  const pipeBtn = event.target.closest(".pipeline-map-btn");
  if (pipeBtn) {
    event.preventDefault();
    const row = pipeBtn.closest(".pipeline-row");
    if (row) applyPipelineFocusFromEl(row);
    return;
  }
  const disposalBtn = event.target.closest(".disposal-map-btn");
  if (disposalBtn) {
    event.preventDefault();
    const row = disposalBtn.closest(".disposal-row") || disposalBtn.closest(".disposal-results");
    if (row) applyDisposalFocusFromEl(row);
    return;
  }
  const toggle = event.target.closest(".map-toggle");
  if (toggle) {
    event.preventDefault();
    toggleWellOnMap(toggle);
    return;
  }
  const name = event.target.closest(".well-name-link");
  if (name) {
    const row = name.closest("tr.well-row");
    const well = wellFromRow(row);
    if (well) {
      event.preventDefault();
      addAndSelectWell(row);
      return;
    }
  }
  if (event.target.closest("a, button, input, label, .save-form")) return;
  const row = event.target.closest("tr.well-row.is-mapped");
  if (row && row.dataset.api) {
    selectWell(row.dataset.api);
  }
});

document.addEventListener("htmx:configRequest", (event) => {
  const elt = event.detail && event.detail.elt;
  const q = searchInputEl();
  if (elt && elt.id === "q" && elt.dataset.suppressLive === "1") {
    event.preventDefault();
    return;
  }
  if (!elt || !q) return;
  const target = elt.getAttribute && elt.getAttribute("hx-target");
  if (elt.id === "search-form" || target === "#results") {
    const form = document.getElementById("search-form");
    const snapshot = q.value;
    elt.dataset.submittedQ = snapshot;
    if (form) form.dataset.submittedQ = snapshot;
  }
});

document.addEventListener("htmx:afterSwap", (event) => {
  const target = event.detail && event.detail.target;
  const targetId = target && target.id;
  if (targetId === "results") {
    const marker = document.getElementById("clear-query");
    if (marker) {
      const q = searchInputEl();
      const form = document.getElementById("search-form");
      const submitted = form ? form.dataset.submittedQ : undefined;
      if (q) {
        if (submitted === undefined) {
          if (!searchInputIsActive()) q.value = "";
        } else if (q.value === submitted) {
          q.value = "";
        }
      }
      marker.remove();
    }
  }
  syncMapButtons();
  if (targetId === "save-status" || (target && target.querySelector && target.querySelector("#save-status"))) {
    applySaveStatus(target);
  }
  bindSearchContext();
  if (targetId === "results" && !searchInputIsActive()) {
    const auto = document.querySelector("#results [data-auto-map]");
    if (auto) {
      if (auto.dataset.disposalId) applyDisposalFocusFromEl(auto);
      else applyPipelineFocusFromEl(auto);
    }
  }
});

document.addEventListener("htmx:sendError", (event) => {
  const elt = event.detail && event.detail.elt;
  if (!elt || elt.id !== "search-form" || elt.dataset.nativeFallback === "1") return;
  elt.dataset.nativeFallback = "1";
  HTMLFormElement.prototype.submit.call(elt);
});

document.body.addEventListener("click", (event) => {
  if (event.target.closest("#account-nav a, #account-nav button, #search-form .primary")) {
    showWorkspacePane("search");
  }
});

document.getElementById("search-form")?.addEventListener("submit", () => {
  showWorkspacePane("search");
  const q = searchInputEl();
  if (!q) return;
  q.dataset.suppressLive = "1";
  if (window.htmx) window.htmx.trigger(q, "htmx:abort");
});

window.addEventListener("resize", () => {
  if (!isPhoneWorkspace() || workspaceRoot()?.dataset.pane === "map") {
    window.setTimeout(() => {
      if (map) map.invalidateSize();
    }, 80);
  }
});

initWorkspaceTabs();
bindSearchContext();
window.addEventListener("online", applyNetworkState);
window.addEventListener("offline", applyNetworkState);
function closeInfoTips() {
  document.querySelectorAll(".info-tip-pop.is-open").forEach((pop) => {
    pop.classList.remove("is-open");
    pop.hidden = true;
    if (typeof pop.hidePopover === "function" && pop.matches(":popover-open")) pop.hidePopover();
    if (pop._home) pop._home.appendChild(pop);
  });
  document.querySelectorAll('.info-tip-btn[aria-expanded="true"]').forEach((btn) => {
    btn.setAttribute("aria-expanded", "false");
  });
}

function openInfoTip(btn, pop) {
  if (!pop._home) pop._home = pop.parentElement;
  closeInfoTips();
  pop.hidden = false;
  if (!pop.hasAttribute("popover")) pop.setAttribute("popover", "manual");
  document.body.appendChild(pop);
  if (typeof pop.showPopover === "function" && !pop.matches(":popover-open")) pop.showPopover();
  pop.classList.add("is-open");
  btn.setAttribute("aria-expanded", "true");
  const rect = btn.getBoundingClientRect();
  const width = Math.min(240, window.innerWidth - 16);
  let left = rect.left;
  if (left + width > window.innerWidth - 8) left = window.innerWidth - width - 8;
  if (left < 8) left = 8;
  pop.style.width = `${width}px`;
  pop.style.left = `${left}px`;
  pop.style.top = `${rect.bottom + 8}px`;
  const box = pop.getBoundingClientRect();
  if (box.bottom > window.innerHeight - 8) {
    pop.style.top = `${Math.max(8, rect.top - box.height - 8)}px`;
  }
}

function bindInfoTips() {
  if (document.documentElement.dataset.infoTips === "1") return;
  document.documentElement.dataset.infoTips = "1";
  const fineHover = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  document.addEventListener("click", (event) => {
    const btn = event.target.closest(".info-tip-btn");
    if (btn) {
      event.preventDefault();
      event.stopPropagation();
      const tip = btn.closest(".info-tip");
      const pop = tip && tip.querySelector(".info-tip-pop");
      if (!pop) return;
      const open = btn.getAttribute("aria-expanded") === "true";
      closeInfoTips();
      if (!open) openInfoTip(btn, pop);
      return;
    }
    if (!event.target.closest(".info-tip-pop")) closeInfoTips();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeInfoTips();
  });
  if (!fineHover) return;
  document.addEventListener("mouseover", (event) => {
    const tip = event.target.closest(".info-tip");
    if (!tip || tip.contains(event.relatedTarget)) return;
    const btn = tip.querySelector(".info-tip-btn");
    const pop = tip.querySelector(".info-tip-pop");
    if (btn && pop) openInfoTip(btn, pop);
  });
  document.addEventListener("mouseout", (event) => {
    const next = event.relatedTarget;
    const open = document.querySelector(".info-tip-pop.is-open");
    if (next && open && (next === open || open.contains(next))) return;
    const tip = event.target.closest(".info-tip");
    if (tip && next && tip.contains(next)) return;
    if (event.target.closest(".info-tip, .info-tip-pop")) closeInfoTips();
  });
}

bindInfoTips();
bindLocationShare();
applyNetworkState();
if (document.getElementById("well-map") || document.getElementById("map-panel")) {
  initWellMap();
}
