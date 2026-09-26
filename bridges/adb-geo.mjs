#!/usr/bin/env node
/**
 * Push the route simulator's current fix to an Android emulator.
 *
 * Each call reads GET /api/location and, when a fix exists, runs
 * `adb emu geo fix <longitude> <latitude>`. Importing this module
 * does not spawn adb.
 */

import { spawn } from "node:child_process";
import { realpathSync } from "node:fs";
import { pathToFileURL } from "node:url";

export const DEFAULT_BASE_URL = "http://127.0.0.1:8787";
export const POLL_MS = 1000;

export const USAGE = [
  "Usage: node bridges/adb-geo.mjs [--base URL] [--watch]",
  "Read GET /api/location and, when a fix exists, run adb emu geo fix <longitude> <latitude>.",
  "Default base URL is http://127.0.0.1:8787, or ROUTE_SIM_BASE_URL.",
  "--watch repeats about once a second. Idle responses are skipped.",
].join("\n");

export function finiteNumber(value) {
  if (value == null || value === "") return null;
  const number = typeof value === "number" ? value : Number(value);
  return Number.isFinite(number) ? number : null;
}

export function resolveBaseUrl(explicit, env = process.env) {
  const raw = explicit ?? env.ROUTE_SIM_BASE_URL ?? DEFAULT_BASE_URL;
  const trimmed = String(raw ?? "").trim().replace(/\/+$/, "");
  return trimmed || DEFAULT_BASE_URL;
}

export function locationUrl(baseUrl, env = process.env) {
  return `${resolveBaseUrl(baseUrl, env)}/api/location`;
}

export function selectFix(payload) {
  if (!payload || typeof payload !== "object") return null;
  if (payload.status === "idle" || payload.fix == null) return null;
  const fix = payload.fix;
  if (typeof fix !== "object") return null;
  if (finiteNumber(fix.latitude) == null || finiteNumber(fix.longitude) == null) return null;
  return fix;
}

/** adb's emulator console takes longitude first, then latitude. */
export function adbGeoFixArgs(fix) {
  const longitude = finiteNumber(fix && fix.longitude);
  const latitude = finiteNumber(fix && fix.latitude);
  if (longitude == null || latitude == null) {
    throw new Error("Fix is missing longitude or latitude");
  }
  return ["emu", "geo", "fix", String(longitude), String(latitude)];
}

export function parseCliArgs(argv) {
  const opts = { watch: false, baseUrl: undefined, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--watch" || arg === "-w") {
      opts.watch = true;
      continue;
    }
    if (arg === "--help" || arg === "-h") {
      opts.help = true;
      continue;
    }
    if (arg === "--base" || arg === "--base-url" || arg === "--url") {
      const value = argv[i + 1];
      if (!value || value.startsWith("-")) throw new Error(`${arg} requires a URL`);
      opts.baseUrl = value;
      i += 1;
      continue;
    }
    if (arg.startsWith("--base=")) {
      opts.baseUrl = arg.slice("--base=".length);
      continue;
    }
    if (arg.startsWith("--base-url=")) {
      opts.baseUrl = arg.slice("--base-url=".length);
      continue;
    }
    throw new Error(`Unknown argument: ${arg}`);
  }
  return opts;
}

export function describePush(payload, fix) {
  if (fix) {
    const status = payload && payload.status ? payload.status : "fix";
    return `${status}: adb emu geo fix ${finiteNumber(fix.longitude)} ${finiteNumber(fix.latitude)}`;
  }
  const status = payload && payload.status ? String(payload.status) : "unknown";
  return `${status}: no fix; skipped adb`;
}

function clip(text) {
  const value = String(text || "").trim().replace(/\s+/g, " ");
  if (!value) return "";
  return value.length > 400 ? `${value.slice(0, 400)}…` : value;
}

function unreachableMessage(err) {
  const message = err && err.message ? err.message : String(err);
  if (message.startsWith("Route simulator unreachable")) return message;
  if (err && err.name === "AbortError") return "Route simulator unreachable: timed out";
  return `Route simulator unreachable: ${message}`;
}

export async function fetchLocation(baseUrl, fetchImpl = globalThis.fetch, env = process.env) {
  if (typeof fetchImpl !== "function") {
    throw new Error("Route simulator unreachable: fetch is not available");
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetchImpl(locationUrl(baseUrl, env), {
      method: "GET",
      cache: "no-store",
      signal: controller.signal,
    });
    if (!response || response.ok !== true) {
      const status = response && response.status != null ? response.status : "no response";
      throw new Error(`Route simulator unreachable: HTTP ${status}`);
    }
    try {
      return await response.json();
    } catch {
      throw new Error("Route simulator unreachable: invalid JSON");
    }
  } catch (err) {
    throw new Error(unreachableMessage(err));
  } finally {
    clearTimeout(timer);
  }
}

export function runProcess(command, args, spawnImpl = spawn, timeoutMs = 8000) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawnImpl(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    } catch (err) {
      const missing = Boolean(err && err.code === "ENOENT");
      resolve({
        ok: false,
        missing,
        code: missing ? 127 : 1,
        stdout: "",
        stderr: err && err.message ? err.message : String(err),
      });
      return;
    }

    if (!child || typeof child.on !== "function") {
      resolve({
        ok: false,
        missing: false,
        code: 1,
        stdout: "",
        stderr: "Failed to start adb",
      });
      return;
    }

    let stdout = "";
    let stderr = "";
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve(value);
    };
    const timer = setTimeout(() => {
      try {
        child.kill("SIGTERM");
      } catch {
        /* The process may already be gone. */
      }
      finish({
        ok: false,
        missing: false,
        code: 1,
        stdout,
        stderr: "adb timed out",
      });
    }, timeoutMs);

    if (child.stdout && typeof child.stdout.on === "function") {
      child.stdout.on("data", (chunk) => {
        stdout += chunk;
      });
    }
    if (child.stderr && typeof child.stderr.on === "function") {
      child.stderr.on("data", (chunk) => {
        stderr += chunk;
      });
    }
    child.on("error", (err) => {
      const missing = Boolean(err && err.code === "ENOENT");
      finish({
        ok: false,
        missing,
        code: missing ? 127 : 1,
        stdout,
        stderr: missing
          ? "adb was not found on PATH"
          : (err && err.message) || String(err),
      });
    });
    child.on("close", (code) => {
      const exitCode = code == null ? 1 : code;
      finish({
        ok: exitCode === 0,
        missing: false,
        code: exitCode,
        stdout,
        stderr,
      });
    });
  });
}

function result(ok, kind, exitCode, message) {
  return { ok, kind, exitCode, message };
}

export async function pushFix(baseUrl, deps = {}) {
  const fetchImpl = deps.fetchImpl ?? globalThis.fetch;
  const runImpl = deps.runImpl ?? ((args) => runProcess("adb", args));
  let payload;
  try {
    payload = await fetchLocation(baseUrl, fetchImpl, deps.env);
  } catch (err) {
    return result(false, "unreachable", 1, err.message || String(err));
  }

  const fix = selectFix(payload);
  if (!fix) return result(true, "skip", 0, describePush(payload, null));

  let args;
  try {
    args = adbGeoFixArgs(fix);
  } catch (err) {
    return result(true, "skip", 0, describePush(payload, null));
  }

  let run;
  try {
    run = await runImpl(args);
  } catch (err) {
    const missing = Boolean(err && err.code === "ENOENT");
    run = {
      ok: false,
      missing,
      code: missing ? 127 : 1,
      stderr: err && err.message ? err.message : String(err),
    };
  }

  if (!run || run.ok !== true) {
    if (run && run.missing) {
      return result(
        false,
        "adb",
        127,
        "adb was not found on PATH. Install platform-tools and retry."
      );
    }
    const code = run && run.code ? run.code : 1;
    const detail = clip(run && (run.stderr || run.stdout));
    const message = detail
      ? `adb emu geo fix failed (exit ${code}): ${detail}`
      : `adb emu geo fix failed (exit ${code})`;
    return result(false, "adb", code, message);
  }

  return result(true, "applied", 0, describePush(payload, fix));
}

function defaultLog(line, ok) {
  if (ok === false) console.error(line);
  else console.log(line);
}

function delay(ms) {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function sleepMs(ms, shouldStop, sleep) {
  const slice = 200;
  let remaining = ms;
  while (remaining > 0 && !shouldStop()) {
    await sleep(Math.min(slice, remaining));
    remaining -= slice;
  }
}

export async function main(argv = process.argv.slice(2), deps = {}) {
  const log = deps.log ?? defaultLog;
  try {
    const opts = parseCliArgs(argv);
    if (opts.help) {
      log(USAGE, true);
      return { exitCode: 0 };
    }

    const baseUrl = resolveBaseUrl(opts.baseUrl, deps.env);
    const shouldStop = deps.shouldStop ?? (() => false);
    const sleep = deps.sleep ?? delay;
    const maxCycles = deps.maxCycles ?? (opts.watch ? Number.POSITIVE_INFINITY : 1);

    if (!opts.watch) {
      const once = await pushFix(baseUrl, deps);
      log(once.message, once.ok);
      return { exitCode: once.exitCode };
    }

    log(`watching ${locationUrl(baseUrl, deps.env)}`, true);
    for (let cycle = 0; cycle < maxCycles && !shouldStop(); cycle += 1) {
      const tick = await pushFix(baseUrl, deps);
      log(tick.message, tick.ok);
      if (tick.kind === "adb") return { exitCode: tick.exitCode };
      const more = cycle < maxCycles - 1 && !shouldStop();
      if (more) await sleepMs(POLL_MS, shouldStop, sleep);
    }
    return { exitCode: 0 };
  } catch (err) {
    const message = err && err.message ? err.message : String(err);
    log(message, false);
    return { exitCode: 1 };
  }
}

function runningAsCli() {
  const entry = process.argv[1];
  if (!entry) return false;
  try {
    return import.meta.url === pathToFileURL(realpathSync(entry)).href;
  } catch {
    return false;
  }
}

if (runningAsCli()) {
  let stopped = false;
  const onStop = () => {
    stopped = true;
  };
  process.once("SIGINT", onStop);
  process.once("SIGTERM", onStop);
  main(process.argv.slice(2), { shouldStop: () => stopped })
    .then((outcome) => {
      process.off("SIGINT", onStop);
      process.off("SIGTERM", onStop);
      process.exitCode = outcome.exitCode;
    })
    .catch((err) => {
      process.off("SIGINT", onStop);
      process.off("SIGTERM", onStop);
      console.error(err && err.message ? err.message : String(err));
      process.exitCode = 1;
    });
}
