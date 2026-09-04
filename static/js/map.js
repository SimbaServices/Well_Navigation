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

let map;
let activeBase;
let wellLayer;

function preferredBasemap() {
  return localStorage.getItem("wellnav.basemap") || "imagery";
}

function setBasemap(key) {
  if (!map || !BASE_LAYERS[key]) return;
  if (activeBase) map.removeLayer(activeBase);
  activeBase = BASE_LAYERS[key]();
  activeBase.addTo(map);
  localStorage.setItem("wellnav.basemap", key);
}

function initWellMap() {
  if (map) {
    map.remove();
    map = null;
  }
  const el = document.getElementById("well-map");
  if (!el || typeof L === "undefined") return;
  const lat = Number(el.dataset.lat);
  const lon = Number(el.dataset.lon);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;

  map = L.map(el, { zoomControl: true }).setView([lat, lon], 15);
  setBasemap(preferredBasemap());

  wellLayer = L.layerGroup().addTo(map);
  const wellhead = L.marker([lat, lon], { title: "Wellhead" }).bindPopup(
    `<strong>Wellhead</strong><br>${el.dataset.label || ""}<br>${lat.toFixed(6)}, ${lon.toFixed(6)}`
  );
  wellLayer.addLayer(wellhead);
  wellhead.openPopup();

  const toeLat = Number(el.dataset.toeLat);
  const toeLon = Number(el.dataset.toeLon);
  if (Number.isFinite(toeLat) && Number.isFinite(toeLon)) {
    const toe = L.circleMarker([toeLat, toeLon], {
      radius: 7,
      color: "#d4a017",
      fillColor: "#7cb36a",
      fillOpacity: 0.9,
      weight: 2,
    }).bindPopup(
      `<strong>Toe / bottom hole</strong><br>${toeLat.toFixed(6)}, ${toeLon.toFixed(6)}<br>RRC default mapped point`
    );
    wellLayer.addLayer(toe);
    L.polyline(
      [
        [lat, lon],
        [toeLat, toeLon],
      ],
      { color: "#d4a017", weight: 2, dashArray: "6 6" }
    ).addTo(wellLayer);
    map.fitBounds(
      [
        [lat, lon],
        [toeLat, toeLon],
      ],
      { padding: [40, 40], maxZoom: 16 }
    );
  }

  const select = document.getElementById("basemap-select");
  if (select) select.value = preferredBasemap();
  setTimeout(() => map.invalidateSize(), 80);
}

if (document.getElementById("well-map")) {
  initWellMap();
}
