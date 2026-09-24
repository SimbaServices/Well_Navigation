/* Store WebView boot only: credentials + kill service workers.
   Do NOT gate product UX (Near me, wait reports, map, disposal) on __WN_STORE.
   Look / features must match web production and local/dev. */
(function () {
  var ua = navigator.userAgent || "";
  var store = ua.indexOf("WellNavigation/") !== -1 && ua.toLowerCase().indexOf("store") !== -1;
  if (!store) return;
  window.__WN_STORE = true;
  function armCredentials() {
    if (window.htmx && window.htmx.config) window.htmx.config.withCredentials = true;
  }
  armCredentials();
  document.addEventListener("DOMContentLoaded", armCredentials);
  if (!("serviceWorker" in navigator)) return;
  try {
    navigator.serviceWorker.register = function () {
      return Promise.resolve(null);
    };
  } catch (err) {}
  var controlled = !!navigator.serviceWorker.controller;
  var tries = 0;
  try {
    tries = parseInt(sessionStorage.getItem("wn-store-sw-reset") || "0", 10) || 0;
  } catch (err) {
    tries = 2;
  }
  function dropWorkers() {
    return navigator.serviceWorker.getRegistrations().then(function (regs) {
      return Promise.all((regs || []).map(function (reg) { return reg.unregister(); })).then(function () {
        if (!window.caches || !caches.keys) return;
        return caches.keys().then(function (keys) {
          return Promise.all(keys.map(function (key) { return caches.delete(key); }));
        });
      });
    });
  }
  if (!controlled) {
    dropWorkers().catch(function () {});
    return;
  }
  if (tries >= 2) return;
  try {
    sessionStorage.setItem("wn-store-sw-reset", String(tries + 1));
  } catch (err) {}
  dropWorkers().then(function () { window.location.reload(); }).catch(function () {});
})();
