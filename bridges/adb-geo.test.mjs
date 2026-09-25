import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import test from "node:test";

import {
  adbGeoFixArgs,
  describePush,
  locationUrl,
  main,
  parseCliArgs,
  resolveBaseUrl,
  runProcess,
  selectFix,
} from "./adb-geo.mjs";

const playing = {
  status: "playing",
  fix: {
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
  },
};

function jsonResponse(body, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
  };
}

test("selectFix keeps a playing or paused fix and skips idle", () => {
  assert.equal(selectFix(playing), playing.fix);
  assert.equal(selectFix({ status: "paused", fix: playing.fix }), playing.fix);
  assert.equal(selectFix({ status: "idle", fix: playing.fix }), null);
  assert.equal(selectFix({ status: "playing", fix: null }), null);
  assert.equal(selectFix({ status: "playing", fix: { latitude: null, longitude: 1 } }), null);
  assert.ok(selectFix({ status: "playing", fix: { latitude: 0, longitude: 0 } }));
});

test("adb args are longitude then latitude", () => {
  assert.deepEqual(adbGeoFixArgs(playing.fix), ["emu", "geo", "fix", "-97.7431", "30.2672"]);
  assert.equal(
    describePush(playing, playing.fix),
    "playing: adb emu geo fix -97.7431 30.2672"
  );
});

test("base URL defaults and trims a trailing slash", () => {
  assert.equal(resolveBaseUrl(undefined, {}), "http://127.0.0.1:8787");
  assert.equal(
    locationUrl("http://localhost:9000/", {}),
    "http://localhost:9000/api/location"
  );
  assert.equal(
    resolveBaseUrl(undefined, { ROUTE_SIM_BASE_URL: "http://10.0.0.2:8787/" }),
    "http://10.0.0.2:8787"
  );
});

test("parseCliArgs accepts watch and base", () => {
  assert.deepEqual(parseCliArgs(["--watch", "--base", "http://127.0.0.1:9"]), {
    watch: true,
    baseUrl: "http://127.0.0.1:9",
    help: false,
  });
  assert.equal(parseCliArgs(["--base=http://127.0.0.1:9"]).baseUrl, "http://127.0.0.1:9");
  assert.throws(() => parseCliArgs(["--nope"]), /Unknown argument/);
  assert.throws(() => parseCliArgs(["--base"]), /requires a URL/);
});

test("one shot skips adb when the simulator is idle", async () => {
  let runs = 0;
  const lines = [];
  const outcome = await main(["--base", "http://example.test"], {
    env: {},
    log: (line) => lines.push(line),
    fetchImpl: async (url) => {
      assert.equal(url, "http://example.test/api/location");
      return jsonResponse({ status: "idle", fix: null });
    },
    runImpl: async () => {
      runs += 1;
      return { ok: true, code: 0 };
    },
  });
  assert.equal(outcome.exitCode, 0);
  assert.equal(runs, 0);
  assert.match(lines[0], /idle: no fix; skipped adb/);
});

test("one shot runs adb with longitude then latitude", async () => {
  const seen = [];
  const lines = [];
  const outcome = await main(["--base", "http://example.test"], {
    env: {},
    log: (line) => lines.push(line),
    fetchImpl: async () => jsonResponse(playing),
    runImpl: async (args) => {
      seen.push(args);
      return { ok: true, code: 0 };
    },
  });
  assert.equal(outcome.exitCode, 0);
  assert.deepEqual(seen, [["emu", "geo", "fix", "-97.7431", "30.2672"]]);
  assert.match(lines[0], /playing: adb emu geo fix -97\.7431 30\.2672/);
});

test("one shot exits non-zero when the server is down or adb fails", async () => {
  const down = await main(["--base", "http://example.test"], {
    env: {},
    log: () => {},
    fetchImpl: async () => {
      throw new Error("ECONNREFUSED");
    },
    runImpl: async () => {
      throw new Error("should not run");
    },
  });
  assert.equal(down.exitCode, 1);

  const http = await main(["--base", "http://example.test"], {
    env: {},
    log: () => {},
    fetchImpl: async () => jsonResponse({}, false, 503),
  });
  assert.equal(http.exitCode, 1);

  const missing = await main(["--base", "http://example.test"], {
    env: {},
    log: (line, ok) => {
      if (ok === false) assert.match(line, /adb was not found on PATH/);
    },
    fetchImpl: async () => jsonResponse(playing),
    runImpl: async () => ({ ok: false, missing: true, code: 127, stderr: "not found" }),
  });
  assert.equal(missing.exitCode, 127);

  const failed = await main(["--base", "http://example.test"], {
    env: {},
    log: (line) => {
      assert.match(line, /no emulator/);
    },
    fetchImpl: async () => jsonResponse(playing),
    runImpl: async () => ({ ok: false, missing: false, code: 1, stderr: "no emulator" }),
  });
  assert.equal(failed.exitCode, 1);
});

test("watch retries a down server and stops when adb is missing", async () => {
  let fetches = 0;
  const lines = [];
  const watched = await main(["--watch", "--base", "http://example.test"], {
    env: {},
    maxCycles: 2,
    sleep: async () => {},
    log: (line) => lines.push(line),
    fetchImpl: async () => {
      fetches += 1;
      if (fetches === 1) throw new Error("ECONNREFUSED");
      return jsonResponse(playing);
    },
    runImpl: async () => ({ ok: true, code: 0 }),
  });
  assert.equal(watched.exitCode, 0);
  assert.equal(fetches, 2);
  assert.match(lines[0], /^watching /);
  assert.match(lines[1], /unreachable/);
  assert.match(lines[2], /adb emu geo fix/);

  let attempts = 0;
  const missing = await main(["--watch", "--base", "http://example.test"], {
    env: {},
    maxCycles: 4,
    sleep: async () => {},
    log: () => {},
    fetchImpl: async () => {
      attempts += 1;
      return jsonResponse(playing);
    },
    runImpl: async () => ({ ok: false, missing: true, code: 127 }),
  });
  assert.equal(missing.exitCode, 127);
  assert.equal(attempts, 1);
});

test("help and unknown arguments do not call adb", async () => {
  const lines = [];
  const help = await main(["--help"], {
    log: (line) => lines.push(line),
    fetchImpl: async () => {
      throw new Error("should not fetch");
    },
  });
  assert.equal(help.exitCode, 0);
  assert.match(lines[0], /Usage:/);

  const unknown = await main(["--nope"], { log: () => {} });
  assert.equal(unknown.exitCode, 1);
});

function fakeChild() {
  const child = new EventEmitter();
  child.stdout = new EventEmitter();
  child.stderr = new EventEmitter();
  child.kill = () => {};
  return child;
}

test("runProcess reports a missing adb and a failed exit without throwing", async () => {
  const missing = await runProcess("adb", ["emu", "geo", "fix", "1", "2"], () => {
    const child = fakeChild();
    queueMicrotask(() => {
      const error = new Error("spawn adb ENOENT");
      error.code = "ENOENT";
      child.emit("error", error);
    });
    return child;
  });
  assert.equal(missing.ok, false);
  assert.equal(missing.missing, true);

  const failed = await runProcess("adb", ["emu", "geo", "fix", "1", "2"], () => {
    const child = fakeChild();
    queueMicrotask(() => {
      child.stderr.emit("data", "no emulator");
      child.emit("close", 1);
    });
    return child;
  });
  assert.equal(failed.ok, false);
  assert.match(failed.stderr, /no emulator/);

  let killed = false;
  const timedOut = await runProcess(
    "adb",
    ["emu", "geo", "fix", "1", "2"],
    () => {
      const child = fakeChild();
      child.kill = () => {
        killed = true;
      };
      return child;
    },
    20
  );
  assert.equal(timedOut.ok, false);
  assert.equal(killed, true);
  assert.match(timedOut.stderr, /timed out/);
});
