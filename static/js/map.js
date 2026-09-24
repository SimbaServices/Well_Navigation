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
        <button type="button" class="ghost" id="offline-save">Save this view</button>
        <span class="info-tip">
          <button type="button" class="info-tip-btn" aria-expanded="false" aria-label="About saved maps">i</button>
          <span class="info-tip-pop" popover="manual" hidden role="tooltip">Save USGS topo tiles for this view before you lose signal. Esri layers need a network.</span>
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
    if (clearBtn) clearBtn.hidden = !info.tiles;
    if (!info.tiles) {
      setOfflineStatus(offline.isOnline() ? "" : "No saved map tiles on this device.");
      return;
    }
    setOfflineStatus(
      `${info.tiles.toLocaleString()} USGS tiles on this device (${formatBytes(info.bytes)}).`
    );
  } catch {
    setOfflineStatus("");
  }
}

function bindOfflinePack() {
  const saveBtn = document.getElementById("offline-save");
  const clearBtn = document.getElementById("offline-clear");
  const offline = window.WellnavOffline;
  if (!saveBtn || !offline || saveBtn.dataset.bound === "1") {
    refreshOfflineStatus();
    return;
  }
  saveBtn.dataset.bound = "1";
  saveBtn.addEventListener("click", async () => {
    if (!map || saveBtn.dataset.busy === "1") return;
    if (!offline.isOnline()) {
      setOfflineStatus("Connect to download tiles, then you can use them offline.");
      return;
    }
    const bounds = map.getBounds();
    saveBtn.dataset.busy = "1";
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
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
        `Saved ${result.tiles.toLocaleString()} USGS tiles (z${result.minZ}–${result.maxZ}).`
      );
      if (clearBtn) clearBtn.hidden = false;
    } catch (err) {
      if (err && err.name === "AbortError") {
        setOfflineStatus("Save cancelled.");
      } else {
        setOfflineStatus(err && err.message ? err.message : "Could not save this view.");
      }
    } finally {
      saveBtn.dataset.busy = "0";
      saveBtn.disabled = false;
      saveBtn.textContent = "Save this view";
      refreshOfflineStatus();
    }
  });
  if (clearBtn && clearBtn.dataset.bound !== "1") {
    clearBtn.dataset.bound = "1";
    clearBtn.addEventListener("click", async () => {
      if (!window.confirm("Remove saved map tiles and offline overlays from this device?")) return;
      await offline.clear();
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

function pointHitsMarker(latlng) {
  if (!map || !latlng) return false;
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
  visit(wellLayer);
  visit(disposalLayer);
  return hit;
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
          best = { layer, feature: layer.feature, point };
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
  if (!event || !event.latlng || pointerOnMapChrome(event) || pointHitsMarker(event.latlng)) return;
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
      title: "Pipeline point — click to remove",
      riseOnHover: true,
      icon: L.divIcon({
        className: "pipeline-pin-wrap",
        html: '<span class="pipeline-pin"></span>',
        iconSize: [22, 28],
        iconAnchor: [11, 26],
      }),
    });
    pipelinePinMarker.on("click", (event) => {
      L.DomEvent.stopPropagation(event);
      if (event.originalEvent) L.DomEvent.stop(event.originalEvent);
      clearPipelinePin();
    });
    pipelinePinMarker.addTo(map);
  }
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
  const bits = [
    `<strong>${escapeHtml(props.facility || props.permit_no || "Waste disposal site")}</strong>`,
    props.operator ? escapeHtml(props.operator) : "",
    props.permit_no ? `Permit ${escapeHtml(props.permit_no)}` : "",
    waste ? `Accepted: ${escapeHtml(waste)}` : "",
    props.county ? `${escapeHtml(props.county)} County` : "",
    Number.isFinite(props.lat) && Number.isFinite(props.lon)
      ? `${Number(props.lat).toFixed(6)}, ${Number(props.lon).toFixed(6)}`
      : "",
  ].filter(Boolean);
  return bits.join("<br>");
}

function disposalMarkerStyle(selected) {
  return selected
    ? { radius: 8, color: "#1a1404", fillColor: "#e08a5c", fillOpacity: 1, weight: 2 }
    : { radius: 6, color: "#3a2218", fillColor: "#c45c3a", fillOpacity: 0.92, weight: 1 };
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
    map.getPane("disposal").style.zIndex = 360;
  }
  if (!disposalLayer) {
    disposalLayer = L.geoJSON(null, {
      pane: "disposal",
      pointToLayer(feature, latlng) {
        const selected = disposalFocus && disposalFeatureId(feature) === String(disposalFocus.id);
        return L.circleMarker(latlng, disposalMarkerStyle(selected));
      },
      onEachFeature(feature, layer) {
        const props = feature.properties || {};
        layer.bindPopup(disposalPopup(props), {
          autoClose: true,
          closeOnClick: true,
          closeOnEscapeKey: true,
          autoPan: false,
        });
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
          selectDisposalSite(
            { ...props, id: feature.id ?? props.id },
            { fromMarker: true }
          );
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
  L.control.attribution({ position: "topright", prefix: false }).addTo(map);
  map.createPane("pipelines");
  map.getPane("pipelines").style.zIndex = 350;
  map.createPane("disposal");
  map.getPane("disposal").style.zIndex = 360;
  map.createPane("pipeline-pin");
  map.getPane("pipeline-pin").style.zIndex = 450;
  setBasemap(preferredBasemap(), { persist: false });
  wellLayer = L.layerGroup().addTo(map);
  bindBasemapSelect();
  bindOverlayToggles();
  bindOfflinePack();
  observeMapSize(el);
  map.on("click", (event) => {
    disposalPopupPinned = false;
    pickPipelineAt(event);
  });
  map.on("moveend", schedulePipelines);
  map.on("zoomend", schedulePipelines);
  map.on("moveend", scheduleDisposal);
  map.on("zoomend", scheduleDisposal);
  map.on("moveend", persistMapView);
  map.on("zoomend", persistMapView);
  if (!loadStore().order.length) restoreMapView();
  schedulePipelines();
  scheduleDisposal();
  window.setTimeout(() => {
    if (map) map.invalidateSize({ animate: false });
  }, 80);
  return map;
}

function wellheadPopup(well) {
  const name = escapeHtml(well.name || formatApi(well.api));
  const status = escapeHtml(well.status || "");
  const lines = [`<strong>${name}</strong>`];
  if (status) lines.push(status);
  lines.push("Wellhead", `${well.lat.toFixed(6)}, ${well.lon.toFixed(6)}`);
  return lines.join("<br>");
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
      .bindPopup(wellheadPopup(well), { autoClose: true, closeOnClick: true });
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
      `<strong>Toe / bottom hole</strong><br>${well.toeLat.toFixed(6)}, ${well.toeLon.toFixed(6)}<br>RRC default mapped point`
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

function routeLink(kind, href, label) {
  const a = document.createElement("a");
  a.className = `route ${kind}`;
  a.target = "_blank";
  a.rel = "noopener";
  a.href = href;
  a.textContent = label;
  return a;
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
    } else if (selected) sub.textContent = wellSubtitle(selected);
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
    } else if (selected) {
      coords.appendChild(coordChip("Wellhead", selected.lat, selected.lon, "WGS84"));
      if (hasToe(selected)) {
        coords.appendChild(coordChip("Toe", selected.toeLat, selected.toeLon));
      }
    }
  }

  if (nav) {
    nav.replaceChildren();
    const destLat = showPin ? pipelinePin.lat : disposalFocus ? disposalFocus.lat : selected && selected.lat;
    const destLon = showPin ? pipelinePin.lon : disposalFocus ? disposalFocus.lon : selected && selected.lon;
    if (Number.isFinite(destLat) && Number.isFinite(destLon)) {
      const dest = `${destLat},${destLon}`;
      nav.append(
        routeLink("apple", `https://maps.apple.com/?daddr=${dest}`, "Apple Maps"),
        routeLink("google", `https://maps.google.com/maps/dir/?api=1&destination=${dest}`, "Google Maps")
      );
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
    btn.textContent = "★ Saved";
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

function searchScope() {
  const el = document.querySelector("#search-form [name=scope]");
  if (!el) return "wells";
  if (el.tagName === "SELECT") return el.value || "wells";
  return document.querySelector("#search-form [name=scope]:checked")?.value || "wells";
}

function applySearchContext() {
  const form = document.getElementById("search-form");
  const q = document.getElementById("q");
  const spinner = document.getElementById("spinner");
  if (!form) return;
  const scope = searchScope();
  const pipelines = scope === "pipelines";
  const disposal = scope === "disposal";
  form.setAttribute("action", pipelines ? "/pipelines/search" : disposal ? "/disposal/search" : "/search");
  form.setAttribute("hx-get", pipelines ? "/pipelines/search" : disposal ? "/disposal/search" : "/search");
  if (q) {
    q.setAttribute("hx-get", pipelines ? "/pipelines/suggest" : disposal ? "/disposal/suggest" : "/operators");
    q.setAttribute(
      "hx-trigger",
      "input[this.value.trim().length>=2] changed delay:300ms, keyup[this.value.trim().length>=2] changed delay:300ms, search[this.value.trim().length>=2]"
    );
    q.setAttribute("hx-include", "[name=mode],[name=state],[name=pipe_mode],[name=disp_mode],[name=scope]");
    q.setAttribute(
      "hx-params",
      pipelines ? "q,pipe_mode,scope,state" : disposal ? "q,disp_mode,scope,state" : "q,mode,state"
    );
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
}

function bindSearchContext() {
  const form = document.getElementById("search-form");
  if (!form || form.dataset.scopeBound === "1") return;
  form.dataset.scopeBound = "1";
  form.addEventListener("change", (event) => {
    if (event.target.name === "scope" || event.target.name === "pipe_mode" || event.target.name === "disp_mode" || event.target.name === "state") {
      applySearchContext();
    }
  });
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
applyNetworkState();
if (document.getElementById("well-map") || document.getElementById("map-panel")) {
  initWellMap();
}
