function isStoreClient() {
  var ua = navigator.userAgent || "";
  return ua.indexOf("WellNavigation/") !== -1 && ua.toLowerCase().indexOf("store") !== -1;
}

// Store WebViews ship their own shell. A controlling service worker there
// serves the cached page but drops live /operators and /search calls.
if (!isStoreClient() && "serviceWorker" in navigator && !window.__WN_STORE) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {});
  });
}
