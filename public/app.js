"use strict";

(function () {
  const statusEl = document.getElementById("status");
  const speedReadout = document.getElementById("speed-readout");
  const progressReadout = document.getElementById("progress-readout");
  const progressBar = document.getElementById("progress-bar");
  const progressTrack = document.getElementById("progress-track");
  const routeText = document.getElementById("route-text");
  const pointCount = document.getElementById("point-count");
  const speedInput = document.getElementById("speed");
  const loopInput = document.getElementById("loop");
  const undoButton = document.getElementById("undo");
  const clearButton = document.getElementById("clear");
  const messageEl = document.getElementById("message");
  const linkEl = document.getElementById("link");

  if (typeof L === "undefined") {
    setMessage("Map library failed to load.");
    return;
  }

  const map = L.map("map", { zoomControl: true }).setView([39.5, -98.35], 5);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  map.zoomControl.setPosition("bottomright");

  const casing = L.polyline([], {
    color: "#ffffff",
    weight: 7,
    opacity: 0.95,
    interactive: false,
  }).addTo(map);
  const line = L.polyline([], {
    color: "#2563eb",
    weight: 4,
    interactive: false,
  }).addTo(map);
  const vertices = L.layerGroup().addTo(map);

  let coordinates = [];
  let marker = null;
  let hasLiveFix = false;
  let simStatus = "idle";
  let routeGeneration = 0;
  let routeChain = Promise.resolve();
  let simGeneration = 0;
  let simChain = Promise.resolve();
  let speedTimer = 0;
  let socket = null;
  let linkUp = false;
  let pollTimer = 0;
  let pollBusy = false;
  let reconnectTimer = 0;
  let stopped = false;

  function setMessage(text) {
    messageEl.textContent = text || "";
  }

  function setLinkNotice(text) {
    if (!text) {
      linkEl.hidden = true;
      linkEl.textContent = "";
      return;
    }
    linkEl.hidden = false;
    linkEl.textContent = text;
  }

  function setStatus(status) {
    const known = status === "idle" || status === "playing" || status === "paused";
    if (!known) return;
    simStatus = status;
    statusEl.dataset.state = status;
    statusEl.textContent = status.charAt(0).toUpperCase() + status.slice(1);
  }

  function formatCoord(value) {
    return Number(value.toFixed(6)).toString();
  }

  function formatRoute(points) {
    return points.map(([lng, lat]) => `${formatCoord(lat)},${formatCoord(lng)}`).join("\n");
  }

  function formatDistance(meters, useKm) {
    if (!Number.isFinite(meters)) return "—";
    if (useKm) return `${(meters / 1000).toFixed(2)} km`;
    return `${Math.round(meters)} m`;
  }

  function readSpeed() {
    const raw = speedInput.value.trim();
    if (!raw) return null;
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) return null;
    return value;
  }

  function vehicleIcon() {
    return L.divIcon({
      className: "vehicle-icon",
      html: '<div class="vehicle" style="transform:rotate(0deg)"><svg viewBox="0 0 32 32" width="32" height="32" aria-hidden="true"><path d="M16 2.5 L26.5 28 L16 22.5 L5.5 28 Z" fill="#1d4ed8" stroke="#ffffff" stroke-width="2" stroke-linejoin="round"/></svg></div>',
      iconSize: [32, 32],
      iconAnchor: [16, 16],
    });
  }

  function moveMarker(lat, lng, heading) {
    const latLng = [lat, lng];
    if (!marker) {
      marker = L.marker(latLng, {
        icon: vehicleIcon(),
        interactive: false,
        zIndexOffset: 1000,
      }).addTo(map);
    } else {
      marker.setLatLng(latLng);
    }
    const applyHeading = () => {
      const root = marker && marker.getElement();
      const arrow = root && root.querySelector(".vehicle");
      if (arrow) arrow.style.transform = `rotate(${heading}deg)`;
    };
    applyHeading();
    requestAnimationFrame(applyHeading);
  }

  function renderRoute() {
    const latLngs = coordinates.map(([lng, lat]) => [lat, lng]);
    casing.setLatLngs(latLngs);
    line.setLatLngs(latLngs);
    vertices.clearLayers();
    coordinates.forEach(([lng, lat], index) => {
      L.circleMarker([lat, lng], {
        radius: index === 0 ? 6 : 4.5,
        color: "#1d4ed8",
        weight: 2,
        fillColor: index === 0 ? "#16a34a" : "#ffffff",
        fillOpacity: 1,
        interactive: false,
      }).addTo(vertices);
    });
    pointCount.textContent = coordinates.length === 1 ? "1 point" : `${coordinates.length} points`;
    undoButton.disabled = coordinates.length === 0;
    clearButton.disabled = coordinates.length === 0;
    if (!hasLiveFix) {
      if (coordinates.length) {
        moveMarker(coordinates[0][1], coordinates[0][0], 0);
      } else if (marker) {
        map.removeLayer(marker);
        marker = null;
      }
    }
  }

  function syncText() {
    routeText.value = formatRoute(coordinates);
  }

  function frameRoute() {
    if (!coordinates.length) return;
    if (coordinates.length === 1) {
      map.setView([coordinates[0][1], coordinates[0][0]], Math.max(map.getZoom(), 15));
      return;
    }
    map.fitBounds(coordinates.map(([lng, lat]) => [lat, lng]), {
      padding: [48, 48],
      maxZoom: 16,
    });
  }

  function lineStringCoordinates(data) {
    if (!data || typeof data !== "object") {
      throw new Error("Paste a GeoJSON LineString.");
    }
    if (data.type === "Feature") return lineStringCoordinates(data.geometry);
    if (data.type === "FeatureCollection") {
      const features = Array.isArray(data.features) ? data.features : [];
      const match = features.find((feature) => feature && feature.geometry && feature.geometry.type === "LineString");
      if (!match) throw new Error("Paste a GeoJSON LineString.");
      return lineStringCoordinates(match.geometry);
    }
    if (data.type !== "LineString" || !Array.isArray(data.coordinates)) {
      throw new Error("Paste a GeoJSON LineString.");
    }
    return data.coordinates.map((position, index) => {
      if (!Array.isArray(position) || position.length < 2) {
        throw new Error(`Coordinate ${index + 1} is invalid.`);
      }
      const lng = Number(position[0]);
      const lat = Number(position[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng) || Math.abs(lat) > 90 || Math.abs(lng) > 180) {
        throw new Error(`Coordinate ${index + 1} is out of range.`);
      }
      return [lng, lat];
    });
  }

  function parseRoute(raw) {
    const text = String(raw).replace(/^\uFEFF/, "").trim();
    if (!text) return [];
    if (text[0] === "{" || text[0] === "[") {
      let data;
      try {
        data = JSON.parse(text);
      } catch (error) {
        throw new Error("That GeoJSON could not be parsed.");
      }
      return lineStringCoordinates(data);
    }
    const points = [];
    text.split(/\r?\n/).forEach((line, index) => {
      const row = line.trim();
      if (!row) return;
      const parts = row.split(/[,\s]+/).filter(Boolean);
      if (parts.length < 2) throw new Error(`Line ${index + 1}: expected lat,lng.`);
      const lat = Number(parts[0]);
      const lng = Number(parts[1]);
      if (!Number.isFinite(lat) || !Number.isFinite(lng)) {
        throw new Error(`Line ${index + 1}: expected lat,lng.`);
      }
      if (Math.abs(lat) > 90 || Math.abs(lng) > 180) {
        throw new Error(`Line ${index + 1}: coordinates are out of range.`);
      }
      points.push([lng, lat]);
    });
    return points;
  }

  async function errorMessage(response) {
    try {
      const text = await response.text();
      if (!text) return `${response.status} ${response.statusText}`;
      try {
        const data = JSON.parse(text);
        if (data && typeof data.error === "string") return data.error;
        if (data && typeof data.message === "string") return data.message;
      } catch (error) {
        /* response was not JSON */
      }
      return text.slice(0, 200);
    } catch (error) {
      return `${response.status} ${response.statusText}`;
    }
  }

  async function postJson(url, body) {
    const response = await fetch(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await errorMessage(response));
  }

  function saveRoute() {
    const generation = ++routeGeneration;
    const snapshot = coordinates.map(([lng, lat]) => [lng, lat]);
    const run = routeChain
      .catch(() => {})
      .then(() => postJson("/api/route", { coordinates: snapshot }))
      .then(() => {
        if (generation === routeGeneration) setMessage("");
      })
      .catch((error) => {
        if (generation === routeGeneration) setMessage(error.message || "Could not save the route.");
      });
    routeChain = run;
    return run;
  }

  function sendSimulation(action) {
    const body = { action };
    if (action === "play") {
      const speedKmh = readSpeed();
      if (speedKmh == null) {
        setMessage("Enter a speed in km/h.");
        return;
      }
      body.speedKmh = speedKmh;
      body.loop = loopInput.checked;
      setStatus("playing");
    } else if (action === "pause") {
      setStatus("paused");
    } else if (action === "reset") {
      setStatus("idle");
    }
    const generation = ++simGeneration;
    const run = simChain
      .catch(() => {})
      .then(() => postJson("/api/simulation", body))
      .then(() => {
        if (generation === simGeneration) setMessage("");
      })
      .catch((error) => {
        if (generation === simGeneration) setMessage(error.message || "Simulation request failed.");
      });
    simChain = run;
  }

  function applyFix(fix) {
    if (!fix || typeof fix !== "object") return;
    const lat = Number(fix.latitude);
    const lng = Number(fix.longitude);
    const heading = Number(fix.heading);
    if (Number.isFinite(lat) && Number.isFinite(lng)) {
      hasLiveFix = true;
      moveMarker(lat, lng, Number.isFinite(heading) ? heading : 0);
    }
    const speed = Number(fix.speedKmh);
    speedReadout.textContent = Number.isFinite(speed) ? `${speed.toFixed(1)} km/h` : "—";
    const distance = Number(fix.distanceMeters);
    const total = Number(fix.totalMeters);
    let fraction = Number(fix.fraction);
    if (!Number.isFinite(fraction) && Number.isFinite(distance) && Number.isFinite(total) && total > 0) {
      fraction = distance / total;
    }
    const useKm = Math.max(
      Number.isFinite(total) ? Math.abs(total) : 0,
      Number.isFinite(distance) ? Math.abs(distance) : 0
    ) >= 1000;
    progressReadout.textContent = `${formatDistance(distance, useKm)} / ${formatDistance(total, useKm)}`;
    const clamped = Number.isFinite(fraction) ? Math.max(0, Math.min(1, fraction)) : 0;
    const percent = Math.round(clamped * 100);
    progressBar.style.width = `${percent}%`;
    progressTrack.setAttribute("aria-valuenow", String(percent));
  }

  function applyUpdate(data) {
    if (!data || typeof data !== "object") return;
    if (typeof data.status === "string") setStatus(data.status);
    if (Object.prototype.hasOwnProperty.call(data, "fix")) applyFix(data.fix);
    else if (Object.prototype.hasOwnProperty.call(data, "latitude")) applyFix(data);
  }

  function undoPoint() {
    if (!coordinates.length) return;
    coordinates.pop();
    renderRoute();
    syncText();
    saveRoute();
  }

  let clickTimer = 0;
  let recentAdds = 0;

  function cancelPendingClicks() {
    window.clearTimeout(clickTimer);
    clickTimer = 0;
    recentAdds = 0;
  }

  map.on("click", (event) => {
    coordinates.push([event.latlng.lng, event.latlng.lat]);
    recentAdds += 1;
    renderRoute();
    syncText();
    window.clearTimeout(clickTimer);
    clickTimer = window.setTimeout(() => {
      clickTimer = 0;
      recentAdds = 0;
      saveRoute();
    }, 550);
  });
  map.on("dblclick", () => {
    window.clearTimeout(clickTimer);
    clickTimer = 0;
    const remove = Math.min(recentAdds, coordinates.length);
    recentAdds = 0;
    if (!remove) return;
    coordinates.splice(coordinates.length - remove, remove);
    renderRoute();
    syncText();
    saveRoute();
  });

  document.getElementById("apply").addEventListener("click", () => {
    let next;
    try {
      next = parseRoute(routeText.value);
    } catch (error) {
      setMessage(error.message || "Could not read that route.");
      return;
    }
    cancelPendingClicks();
    coordinates = next;
    renderRoute();
    syncText();
    if (coordinates.length) frameRoute();
    setMessage("");
    saveRoute();
  });

  undoButton.addEventListener("click", () => {
    cancelPendingClicks();
    undoPoint();
  });
  clearButton.addEventListener("click", () => {
    if (!coordinates.length) return;
    cancelPendingClicks();
    coordinates = [];
    renderRoute();
    syncText();
    saveRoute();
  });

  document.getElementById("play").addEventListener("click", () => {
    if (clickTimer) {
      window.clearTimeout(clickTimer);
      clickTimer = 0;
      recentAdds = 0;
      saveRoute();
    }
    routeChain.then(() => sendSimulation("play"));
  });
  document.getElementById("pause").addEventListener("click", () => sendSimulation("pause"));
  document.getElementById("reset").addEventListener("click", () => sendSimulation("reset"));

  speedInput.addEventListener("input", () => {
    window.clearTimeout(speedTimer);
    speedTimer = window.setTimeout(() => {
      if (simStatus !== "playing" || readSpeed() == null) return;
      sendSimulation("play");
    }, 300);
  });
  loopInput.addEventListener("change", () => {
    if (simStatus === "playing") sendSimulation("play");
  });

  function stopPolling() {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = 0;
    }
  }

  async function pollLocation() {
    if (pollBusy || linkUp || stopped) return;
    pollBusy = true;
    try {
      const response = await fetch("/api/location", {
        cache: "no-store",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error("bad status");
      const data = await response.json();
      if (!linkUp) {
        applyUpdate(data);
        setLinkNotice("Live updates unavailable. Checking location every second.");
      }
    } catch (error) {
      if (!linkUp) setLinkNotice("Could not reach the location service.");
    } finally {
      pollBusy = false;
    }
  }

  function startPolling() {
    if (pollTimer || stopped) return;
    pollLocation();
    pollTimer = window.setInterval(pollLocation, 1000);
  }

  function scheduleReconnect() {
    if (reconnectTimer || stopped) return;
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = 0;
      connectSocket();
    }, 2000);
  }

  function connectSocket() {
    if (stopped) return;
    if (!location.host) {
      startPolling();
      return;
    }
    if (socket && (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN)) {
      return;
    }
    let next;
    try {
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      next = new WebSocket(`${protocol}//${location.host}/ws`);
    } catch (error) {
      linkUp = false;
      setLinkNotice("Live updates unavailable. Checking location every second.");
      startPolling();
      scheduleReconnect();
      return;
    }
    socket = next;
    next.addEventListener("open", () => {
      linkUp = true;
      stopPolling();
      setLinkNotice("");
    });
    next.addEventListener("message", (event) => {
      try {
        applyUpdate(JSON.parse(event.data));
      } catch (error) {
        /* ignore malformed frames */
      }
    });
    next.addEventListener("close", () => {
      linkUp = false;
      if (socket === next) socket = null;
      if (stopped) return;
      setLinkNotice("Live updates unavailable. Checking location every second.");
      startPolling();
      scheduleReconnect();
    });
  }

  window.addEventListener("pagehide", () => {
    stopped = true;
    stopPolling();
    window.clearTimeout(reconnectTimer);
    if (socket) socket.close();
  });

  renderRoute();
  connectSocket();
})();
