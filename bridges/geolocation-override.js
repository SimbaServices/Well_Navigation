/**
 * Route simulator geolocation test double.
 *
 * Overrides navigator.geolocation so a navigation app under development
 * reads fixes from GET /api/location. This does not hide the override
 * or bypass location-integrity checks.
 *
 * Same origin is the default. For another origin, set this first:
 *   window.ROUTE_SIM_BASE_URL = "http://127.0.0.1:8787";
 */
(function () {
  "use strict";

  var root = typeof window !== "undefined" ? window : globalThis;
  if (root.__routeSimGeo && root.__routeSimGeo.installed) return;

  var POLL_MS = 1000;
  var watchers = new Map();
  var waiters = [];
  var nextWatchId = 1;
  var cached = null;
  var lastError = null;
  var timer = null;
  var polling = false;
  var active = true;
  var originals = null;
  var originalClear = null;

  function endpoint() {
    var configured = root.ROUTE_SIM_BASE_URL;
    var base = configured == null || configured === ""
      ? ""
      : String(configured).trim().replace(/\/+$/, "");
    return base + "/api/location";
  }

  function positionError(code, message) {
    return {
      code: code,
      message: message,
      PERMISSION_DENIED: 1,
      POSITION_UNAVAILABLE: 2,
      TIMEOUT: 3,
    };
  }

  function finiteOrNull(value) {
    if (value == null || value === "") return null;
    var number = Number(value);
    return Number.isFinite(number) ? number : null;
  }

  function positionFromFix(fix) {
    if (!fix || typeof fix !== "object") return null;
    var latitude = finiteOrNull(fix.latitude);
    var longitude = finiteOrNull(fix.longitude);
    if (latitude == null || longitude == null) return null;
    var timestamp = Date.parse(fix.timestamp);
    return {
      coords: {
        latitude: latitude,
        longitude: longitude,
        accuracy: finiteOrNull(fix.accuracyMeters),
        altitude: null,
        altitudeAccuracy: null,
        heading: finiteOrNull(fix.heading),
        speed: finiteOrNull(fix.speedMps),
      },
      timestamp: Number.isFinite(timestamp) ? timestamp : Date.now(),
    };
  }

  function clonePosition(position) {
    return {
      coords: {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy,
        altitude: position.coords.altitude,
        altitudeAccuracy: position.coords.altitudeAccuracy,
        heading: position.coords.heading,
        speed: position.coords.speed,
      },
      timestamp: position.timestamp,
    };
  }

  function readFix(body) {
    if (!body || body.status === "idle" || body.fix == null) return null;
    return positionFromFix(body.fix);
  }

  function unavailableMessage(body, networkFailed) {
    if (networkFailed) return "Route simulator is unreachable";
    if (body && body.status === "idle") return "Route simulator is idle";
    return "Route simulator has no fix";
  }

  function safeCall(fn, arg) {
    if (typeof fn !== "function") return;
    try {
      fn(arg);
    } catch (err) {
      /* A buggy app callback must not stop the bridge. */
    }
  }

  function deliver(fn, arg) {
    setTimeout(function () {
      safeCall(fn, arg);
    }, 0);
  }

  function settleWaiter(waiter, position, error) {
    if (!waiter || waiter.settled) return;
    waiter.settled = true;
    if (waiter.timer != null) {
      clearTimeout(waiter.timer);
      waiter.timer = null;
    }
    if (position) deliver(waiter.success, clonePosition(position));
    else deliver(waiter.error, error);
  }

  function publish(position, error) {
    if (!active) return;
    cached = position;
    lastError = error;
    watchers.forEach(function (watcher) {
      if (position) {
        watcher.reportedError = false;
        deliver(watcher.success, clonePosition(position));
      } else if (!watcher.reportedError) {
        watcher.reportedError = true;
        deliver(watcher.error, error);
      }
    });
    var pending = waiters.splice(0, waiters.length);
    pending.forEach(function (waiter) {
      settleWaiter(waiter, position, error);
    });
  }

  async function poll() {
    if (!active || polling) return;
    polling = true;
    var ctrl = typeof AbortController === "function" ? new AbortController() : null;
    var abortTimer = ctrl
      ? setTimeout(function () {
          ctrl.abort();
        }, 2000)
      : null;
    try {
      var doFetch = typeof root.fetch === "function" ? root.fetch : fetch;
      var response = await doFetch(endpoint(), {
        method: "GET",
        cache: "no-store",
        credentials: "omit",
        signal: ctrl ? ctrl.signal : undefined,
      });
      if (!response || !response.ok) throw new Error("bad status");
      var body = await response.json();
      if (!active) return;
      var position = readFix(body);
      if (position) publish(position, null);
      else publish(null, positionError(2, unavailableMessage(body, false)));
    } catch (err) {
      if (active) publish(null, positionError(2, unavailableMessage(null, true)));
    } finally {
      if (abortTimer != null) clearTimeout(abortTimer);
      polling = false;
    }
  }

  function ensurePolling() {
    if (!active || timer != null) return;
    poll();
    timer = setInterval(poll, POLL_MS);
  }

  function getCurrentPosition(success, error) {
    ensurePolling();
    if (cached) {
      deliver(success, clonePosition(cached));
      return;
    }
    var waiter = { success: success, error: error, settled: false, timer: null };
    waiters.push(waiter);
    waiter.timer = setTimeout(function () {
      var index = waiters.indexOf(waiter);
      if (index !== -1) waiters.splice(index, 1);
      settleWaiter(
        waiter,
        cached,
        lastError || positionError(2, "Route simulator has no fix")
      );
    }, 1500);
  }

  function watchPosition(success, error) {
    ensurePolling();
    var id = nextWatchId++;
    var watcher = { success: success, error: error, reportedError: false };
    watchers.set(id, watcher);
    if (cached) deliver(success, clonePosition(cached));
    else if (lastError) {
      watcher.reportedError = true;
      deliver(error, lastError);
    }
    return id;
  }

  function clearWatch(id) {
    if (watchers.has(id)) {
      watchers.delete(id);
      return;
    }
    if (typeof originalClear === "function") {
      try {
        originalClear(id);
      } catch (err) {
        /* Unknown watch ids are ignored. */
      }
    }
  }

  function assignMethod(target, name, fn) {
    try {
      target[name] = fn;
      if (target[name] === fn) return true;
    } catch (err) {
      /* Some browsers expose read-only geolocation methods. */
    }
    try {
      Object.defineProperty(target, name, {
        configurable: true,
        writable: true,
        value: fn,
      });
      return target[name] === fn;
    } catch (err) {
      return false;
    }
  }

  function install() {
    var nav = root.navigator;
    if (!nav) {
      nav = {};
      try {
        root.navigator = nav;
      } catch (err) {
        /* Continue and expose the hook below. */
      }
    }
    var current = nav && nav.geolocation;
    if (current && typeof current.clearWatch === "function") {
      originalClear = current.clearWatch.bind(current);
    }
    if (current) {
      originals = {
        getCurrentPosition: current.getCurrentPosition,
        watchPosition: current.watchPosition,
        clearWatch: current.clearWatch,
      };
      assignMethod(current, "getCurrentPosition", getCurrentPosition);
      assignMethod(current, "watchPosition", watchPosition);
      assignMethod(current, "clearWatch", clearWatch);
    }
    var stuck = !current
      || current.getCurrentPosition !== getCurrentPosition
      || current.watchPosition !== watchPosition
      || current.clearWatch !== clearWatch;
    if (stuck && nav) {
      var api = {
        getCurrentPosition: getCurrentPosition,
        watchPosition: watchPosition,
        clearWatch: clearWatch,
      };
      try {
        Object.defineProperty(nav, "geolocation", {
          configurable: true,
          enumerable: true,
          get: function () {
            return api;
          },
        });
      } catch (err) {
        /* The page can still call root.__routeSimGeo.geolocation. */
      }
    }
  }

  function stop() {
    active = false;
    if (timer != null) {
      clearInterval(timer);
      timer = null;
    }
    waiters.forEach(function (waiter) {
      if (waiter.timer != null) clearTimeout(waiter.timer);
      waiter.settled = true;
    });
    waiters.length = 0;
    watchers.clear();
    var nav = root.navigator;
    var current = nav && nav.geolocation;
    if (current && originals) {
      assignMethod(current, "getCurrentPosition", originals.getCurrentPosition);
      assignMethod(current, "watchPosition", originals.watchPosition);
      assignMethod(current, "clearWatch", originals.clearWatch);
    }
    if (root.__routeSimGeo) root.__routeSimGeo.installed = false;
  }

  try {
    install();
    ensurePolling();
    var geolocation = root.navigator && root.navigator.geolocation;
    root.__routeSimGeo = {
      installed: true,
      stop: stop,
      endpoint: endpoint,
      pollNow: poll,
      geolocation: geolocation,
    };
  } catch (err) {
    if (typeof console !== "undefined" && typeof console.warn === "function") {
      console.warn(
        "Route simulator geolocation bridge did not install.",
        err && err.message ? err.message : err
      );
    }
  }
})();
