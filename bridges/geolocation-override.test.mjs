import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./geolocation-override.js", import.meta.url), "utf8");

function waitFor(predicate) {
  const started = Date.now();
  return new Promise((resolve, reject) => {
    const timer = setInterval(() => {
      if (predicate()) {
        clearInterval(timer);
        resolve();
      } else if (Date.now() - started > 2000) {
        clearInterval(timer);
        reject(new Error("timed out waiting for geolocation bridge"));
      }
    }, 10);
  });
}

function load(options = {}) {
  const requests = [];
  let payload = options.payload ?? { status: "idle", fix: null };
  let fail = Boolean(options.fail);
  const sandbox = {
    console,
    setTimeout,
    clearTimeout,
    setInterval,
    clearInterval,
    AbortController,
    Map,
    Date,
    Number,
    String,
    Promise,
    Object,
    Error,
    ROUTE_SIM_BASE_URL: options.baseUrl,
    fetch(url) {
      requests.push(String(url));
      if (fail) return Promise.reject(new Error("down"));
      const body = typeof payload === "function" ? payload() : payload;
      return Promise.resolve({
        ok: true,
        status: 200,
        json: async () => body,
      });
    },
    navigator: { geolocation: {} },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox, { filename: "geolocation-override.js" });
  return {
    sandbox,
    requests,
    setPayload(next) {
      payload = next;
    },
    setFail(next) {
      fail = next;
    },
    stop() {
      sandbox.__routeSimGeo.stop();
    },
  };
}

const fix = {
  latitude: 30.2672,
  longitude: -97.7431,
  heading: 90,
  speedMps: 12.5,
  speedKmh: 45,
  accuracyMeters: 5,
  timestamp: "2026-09-25T12:00:00.000Z",
  distanceMeters: 10,
  totalMeters: 100,
  fraction: 0.1,
};

test("watchPosition emits the latest fix and clearWatch stops it", async (t) => {
  const env = load({ payload: { status: "playing", fix } });
  t.after(() => env.stop());
  const samples = [];
  const id = env.sandbox.navigator.geolocation.watchPosition((position) => {
    samples.push(position);
  }, () => {
    throw new Error("unexpected error");
  });

  await waitFor(() => samples.length >= 1);
  const position = samples[0];
  assert.equal(position.coords.latitude, 30.2672);
  assert.equal(position.coords.longitude, -97.7431);
  assert.equal(position.coords.accuracy, 5);
  assert.equal(position.coords.speed, 12.5);
  assert.equal(position.coords.heading, 90);
  assert.equal(position.timestamp, Date.parse(fix.timestamp));
  assert.equal(env.requests[0], "/api/location");

  env.sandbox.navigator.geolocation.clearWatch(id);
  env.setPayload({
    status: "playing",
    fix: { ...fix, latitude: 31 },
  });
  await env.sandbox.__routeSimGeo.pollNow();
  await new Promise((resolve) => setTimeout(resolve, 30));
  assert.equal(samples.length, 1);
});

test("paused fixes are emitted and idle or down calls fail soft", async (t) => {
  const paused = load({
    payload: { status: "paused", fix: { ...fix, speedMps: 0, heading: null } },
  });
  t.after(() => paused.stop());
  const position = await new Promise((resolve, reject) => {
    paused.sandbox.navigator.geolocation.getCurrentPosition(resolve, reject);
  });
  assert.equal(position.coords.speed, 0);
  assert.equal(position.coords.heading, null);

  const idle = load({ payload: { status: "idle", fix } });
  t.after(() => idle.stop());
  const idleError = await new Promise((resolve) => {
    idle.sandbox.navigator.geolocation.getCurrentPosition(
      () => resolve(null),
      (error) => resolve(error)
    );
  });
  assert.equal(idleError.code, 2);
  assert.match(idleError.message, /idle/);

  const down = load({ fail: true });
  t.after(() => down.stop());
  const downError = await new Promise((resolve) => {
    down.sandbox.navigator.geolocation.getCurrentPosition(
      () => resolve(null),
      (error) => resolve(error)
    );
  });
  assert.equal(downError.code, 2);
  assert.match(downError.message, /unreachable/);
});

test("a configured base URL is polled about once a second", async (t) => {
  const env = load({
    baseUrl: "http://127.0.0.1:8787/",
    payload: { status: "playing", fix },
  });
  t.after(() => env.stop());
  await waitFor(() => env.requests.length >= 1);
  assert.equal(env.requests[0], "http://127.0.0.1:8787/api/location");

  const before = env.requests.length;
  await new Promise((resolve) => setTimeout(resolve, 1100));
  assert.ok(env.requests.length > before);
});

test("a throwing success callback does not break the next watch", async (t) => {
  const env = load({ payload: { status: "playing", fix } });
  t.after(() => env.stop());
  env.sandbox.navigator.geolocation.watchPosition(() => {
    throw new Error("app bug");
  });
  const samples = [];
  env.sandbox.navigator.geolocation.watchPosition((position) => {
    samples.push(position.coords.latitude);
  });
  await waitFor(() => samples.length >= 1);
  await env.sandbox.__routeSimGeo.pollNow();
  await waitFor(() => samples.length >= 2);
  assert.deepEqual(samples.slice(0, 2), [30.2672, 30.2672]);
});
