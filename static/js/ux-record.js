(() => {
  const STORE = "wellnav-ux-rec";
  const MAX_RESUME_MS = 12 * 60 * 60 * 1000;
  const FLUSH_MS = 1500;
  const BEACON_MAX = 56000;

  let recordingId = null;
  let queue = [];
  let uploading = Promise.resolve();
  let stopRecord = null;
  let flushTimer = 0;
  let starting = null;
  let unauthorized = false;

  function rrwebApi() {
    return window.rrweb && typeof window.rrweb.record === "function" ? window.rrweb : null;
  }

  function waitForRrweb() {
    return new Promise((resolve, reject) => {
      const api = rrwebApi();
      if (api) {
        resolve(api);
        return;
      }
      const started = Date.now();
      const timer = window.setInterval(() => {
        const ready = rrwebApi();
        if (ready) {
          window.clearInterval(timer);
          resolve(ready);
          return;
        }
        if (Date.now() - started > 4000) {
          window.clearInterval(timer);
          reject(new Error("rrweb missing"));
        }
      }, 50);
    });
  }

  function readResume() {
    try {
      const data = JSON.parse(sessionStorage.getItem(STORE) || "null");
      if (!data || !data.id || typeof data.id !== "string") return null;
      if (!/^[a-f0-9]{16,32}$/.test(data.id)) return null;
      if (Date.now() - Number(data.at || 0) > MAX_RESUME_MS) return null;
      return data.id;
    } catch (_error) {
      return null;
    }
  }

  function writeResume(id) {
    try {
      sessionStorage.setItem(STORE, JSON.stringify({ id, at: Date.now() }));
    } catch (_error) {}
  }

  function clearResume() {
    try {
      sessionStorage.removeItem(STORE);
    } catch (_error) {}
  }

  async function post(url, body, headers, keepalive) {
    const response = await fetch(url, {
      method: "POST",
      credentials: "same-origin",
      redirect: "error",
      keepalive: Boolean(keepalive),
      headers: headers || { Accept: "application/json" },
      body,
    });
    if (response.status === 401) {
      unauthorized = true;
      throw new Error("sign_in_required");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(data.error || "Could not save that recording.");
      error.status = response.status;
      error.detail = data.error || "";
      throw error;
    }
    return data;
  }

  function beacon(url, body) {
    if (!navigator.sendBeacon || body.length >= BEACON_MAX) return false;
    try {
      return navigator.sendBeacon(url, new Blob([body], { type: "application/json" }));
    } catch (_error) {
      return false;
    }
  }

  function takeQueue() {
    if (!queue.length) return "";
    const batch = queue;
    queue = [];
    return JSON.stringify(batch);
  }

  async function createRecording() {
    const created = await post("/ux/recordings", null);
    if (!created.id) throw new Error("Could not start that recording.");
    recordingId = created.id;
    writeResume(recordingId);
    return recordingId;
  }

  async function ensureId() {
    if (unauthorized) return null;
    if (recordingId) return recordingId;
    if (starting) return starting;
    starting = (async () => {
      const resume = readResume();
      if (resume) {
        recordingId = resume;
        writeResume(resume);
        return recordingId;
      }
      return createRecording();
    })().finally(() => {
      starting = null;
    });
    return starting;
  }

  async function rotate() {
    recordingId = null;
    clearResume();
    return createRecording();
  }

  function staleRecording(error) {
    const text = `${error && error.detail ? error.detail : ""} ${error && error.message ? error.message : ""}`;
    return error && (error.status === 400 || error.status === 404) && /not found|already saved|too large/i.test(text);
  }

  async function flush(keepalive) {
    if (unauthorized) return;
    const body = takeQueue();
    if (!body) return;
    const id = await ensureId();
    if (!id) {
      queue = JSON.parse(body).concat(queue);
      return;
    }
    try {
      await post(
        `/ux/recordings/${id}/chunk`,
        body,
        { Accept: "application/json", "Content-Type": "application/json" },
        keepalive
      );
      writeResume(id);
    } catch (error) {
      if (unauthorized) return;
      if (staleRecording(error)) {
        await rotate();
        if (recordingId) {
          await post(
            `/ux/recordings/${recordingId}/chunk`,
            body,
            { Accept: "application/json", "Content-Type": "application/json" },
            keepalive
          );
        }
        return;
      }
      queue = JSON.parse(body).concat(queue);
      throw error;
    }
  }

  function finishRecording(id) {
    if (!id) return;
    if (!beacon(`/ux/recordings/${id}/finish`, "{}")) {
      fetch(`/ux/recordings/${id}/finish`, {
        method: "POST",
        credentials: "same-origin",
        keepalive: true,
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: "{}",
      }).catch(() => {});
    }
  }

  function unloadFlush() {
    const id = recordingId || readResume();
    if (unauthorized || !id) return;
    if (queue.length) {
      const body = takeQueue();
      if (body && !beacon(`/ux/recordings/${id}/chunk`, body)) {
        fetch(`/ux/recordings/${id}/chunk`, {
          method: "POST",
          credentials: "same-origin",
          keepalive: true,
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body,
        }).catch(() => {});
      }
    }
    finishRecording(id);
  }

  function hookRecorder(api) {
    if (stopRecord) return;
    stopRecord = api.record({
      emit(event) {
        queue.push(event);
        const snapshot = event && (event.type === 2 || event.type === 4);
        if (snapshot || queue.length >= 40) {
          uploading = uploading.then(() => flush(false)).catch(() => {});
        }
      },
      maskAllInputs: true,
      maskInputOptions: { password: true },
      inlineStylesheet: true,
      checkoutEveryNms: 8000,
      sampling: { mousemove: 200, mouseInteraction: true, scroll: 150, input: "last" },
    });
  }

  async function start() {
    if (unauthorized) return;
    const api = await waitForRrweb();
    hookRecorder(api);
    await ensureId();
    await flush(false);
    if (!flushTimer) {
      flushTimer = window.setInterval(() => {
        uploading = uploading.then(() => flush(false)).catch(() => {});
      }, FLUSH_MS);
    }
  }

  function onVisible() {
    unauthorized = false;
    if (!stopRecord || !recordingId) {
      start().catch(() => {});
      return;
    }
    uploading = uploading.then(() => flush(false)).catch(() => {});
  }

  document.addEventListener("htmx:afterSettle", () => {
    if (window.rrweb && rrweb.record && typeof rrweb.record.takeFullSnapshot === "function") {
      try {
        rrweb.record.takeFullSnapshot();
      } catch (_error) {}
    }
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) unloadFlush();
    else onVisible();
  });
  window.addEventListener("pagehide", (event) => {
    unloadFlush();
    if (event.persisted && stopRecord) return;
  });
  window.addEventListener("pageshow", onVisible);
  window.addEventListener("focus", onVisible);
  start().catch(() => {});
})();
