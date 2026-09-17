/* App-shell cache so the iOS / Play Store WebView can reopen the last workspace offline. */
const SHELL = "wellnav-shell-v1";
const PRECACHE = [
  "/",
  "/static/css/app.css?v=offline1",
  "/static/js/map.js?v=offline1",
  "/static/js/offline-map.js?v=1",
  "/static/js/sw-register.js?v=1",
  "/static/vendor/leaflet/leaflet.css",
  "/static/vendor/leaflet/leaflet.js",
  "/static/vendor/leaflet/images/layers.png",
  "/static/vendor/leaflet/images/layers-2x.png",
  "/static/vendor/leaflet/images/marker-icon.png",
  "/static/vendor/leaflet/images/marker-icon-2x.png",
  "/static/vendor/leaflet/images/marker-shadow.png",
  "/static/vendor/htmx.min.js",
  "/static/app-icon.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((cache) => cache.addAll(PRECACHE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== SHELL).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

function isShellAsset(url) {
  if (url.origin !== self.location.origin) return false;
  if (url.pathname.startsWith("/static/")) return true;
  return url.pathname === "/" || url.pathname === "/sw.js";
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  if (url.pathname.startsWith("/offline/tiles/")) return;
  if (url.pathname.startsWith("/login") || url.pathname.startsWith("/register")) return;

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(SHELL).then((cache) => cache.put("/", copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => caches.match("/") || caches.match(req))
    );
    return;
  }

  if (!isShellAsset(url)) return;
  event.respondWith(
    caches.match(req).then((cached) => {
      const fetched = fetch(req)
        .then((resp) => {
          if (resp && resp.ok) {
            const copy = resp.clone();
            caches.open(SHELL).then((cache) => cache.put(req, copy)).catch(() => {});
          }
          return resp;
        })
        .catch(() => cached);
      return cached || fetched;
    })
  );
});
