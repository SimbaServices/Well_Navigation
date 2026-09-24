/* Disposal near-me search, map-pin enrichment, and wait-time reporting UI.
   Environment-agnostic: same behavior on web, iOS/Android store WebViews, and local/dev.
   Never branch on __WN_STORE / user-agent for product UX. */
(function () {
  const RADIUM_MODE = "radium_near";
  const NEAR_MODE = "near";

  function $(id) {
    return document.getElementById(id);
  }

  function apiFetch(url, options) {
    const opts = options ? Object.assign({}, options) : {};
    opts.credentials = opts.credentials || "same-origin";
    return fetch(url, opts);
  }

  function pad(n) {
    return String(n).padStart(2, "0");
  }

  function toLocalInputValue(date) {
    const d = date instanceof Date ? date : new Date(date);
    return (
      d.getFullYear() +
      "-" +
      pad(d.getMonth() + 1) +
      "-" +
      pad(d.getDate()) +
      "T" +
      pad(d.getHours()) +
      ":" +
      pad(d.getMinutes())
    );
  }

  function localInputToIso(value) {
    if (!value) return null;
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return null;
    return d.toISOString();
  }

  function formatMinutes(mins) {
    if (mins == null || !Number.isFinite(Number(mins))) return "—";
    const m = Math.round(Number(mins));
    if (m < 60) return m + " min";
    const h = Math.floor(m / 60);
    const rem = m % 60;
    return rem ? h + "h " + rem + "m" : h + "h";
  }

  function formatWhen(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit",
    });
  }

  function getPosition() {
    return new Promise((resolve, reject) => {
      if (!navigator.geolocation) {
        reject(new Error("Location is not available in this browser."));
        return;
      }
      navigator.geolocation.getCurrentPosition(
        (pos) => resolve(pos.coords),
        (err) => {
          const msg =
            err && err.code === 1
              ? "Location permission was denied. Allow location to find disposal sites near you."
              : "Could not read your current location.";
          reject(new Error(msg));
        },
        { enableHighAccuracy: true, timeout: 20000, maximumAge: 30000 }
      );
    });
  }

  function dispMode() {
    const checked = document.querySelector('input[name="disp_mode"]:checked');
    return checked ? checked.value : "name";
  }

  function needsLocationMode(mode) {
    return mode === NEAR_MODE || mode === RADIUM_MODE;
  }

    async function runNearSearch(form) {
    const mode = dispMode();
    if (!needsLocationMode(mode)) return false;
    const status = $("disposal-near-status");
    if (status) {
      status.hidden = false;
      status.textContent = "Getting your location…";
    }
    try {
      const coords = await getPosition();
      let latInput = form.querySelector('input[name="lat"]');
      let lonInput = form.querySelector('input[name="lon"]');
      if (!latInput) {
        latInput = document.createElement("input");
        latInput.type = "hidden";
        latInput.name = "lat";
        form.appendChild(latInput);
      }
      if (!lonInput) {
        lonInput = document.createElement("input");
        lonInput.type = "hidden";
        lonInput.name = "lon";
        form.appendChild(lonInput);
      }
      latInput.value = String(coords.latitude);
      lonInput.value = String(coords.longitude);
      if (status) {
        status.textContent =
          mode === RADIUM_MODE
            ? "Searching radium / NORM disposal facilities near you…"
            : "Searching disposal facilities near you…";
      }
      return true;
    } catch (err) {
      if (status) status.textContent = err.message || String(err);
      const results = $("results");
      if (results) {
        results.innerHTML =
          '<div class="banner error">' +
          (err.message || "Location required for near-me search.") +
          "</div>";
      }
      return false;
    }
  }

  function bindNearSearch() {
    const form = $("search-form");
    if (!form || form.dataset.nearBound === "1") return;
    form.dataset.nearBound = "1";
    form.addEventListener(
      "submit",
      (event) => {
        const scope = form.querySelector('select[name="scope"]');
        if (!scope || scope.value !== "disposal") return;
        const mode = dispMode();
        if (!needsLocationMode(mode)) return;
        event.preventDefault();
        event.stopPropagation();
        runNearSearch(form).then((ok) => {
          if (!ok) return;
          if (window.htmx) {
            window.htmx.ajax("GET", form.getAttribute("action") || "/search", {
              source: form,
              target: "#results",
              values: Object.fromEntries(new FormData(form).entries()),
              swap: "innerHTML",
            });
          } else {
            form.submit();
          }
        });
      },
      true
    );
  }

  function wasteClassText(site) {
    if (!site) return "";
    if (Array.isArray(site.waste_classifications) && site.waste_classifications.length) {
      return site.waste_classifications.join(" · ");
    }
    const bits = [site.permit_type_label || site.permit_type, site.discharge_type].filter(Boolean);
    return bits.join(" · ");
  }

  async function enrichDisposalFocus(siteId) {
    if (!siteId || !window.selectDisposalSite) return;
    try {
      const resp = await apiFetch("/disposal/site/" + encodeURIComponent(siteId), {
        headers: { Accept: "application/json" },
      });
      if (!resp.ok) return;
      const site = await resp.json();
      if (site.error) return;
      window.selectDisposalSite(
        {
          id: site.id,
          lat: site.lat,
          lon: site.lon,
          facility: site.facility,
          name: site.facility,
          operator: site.operator,
          permit_no: site.permit_no,
          county: site.county,
          waste_classifications: site.waste_classifications,
          permit_type_label: site.permit_type_label,
          discharge_type: site.discharge_type,
        },
        { fromMarker: true, enriched: true }
      );
      renderWasteClasses(site);
      loadWaitSummary(site.id);
    } catch {
      /* ignore */
    }
  }

  function renderWasteClasses(site) {
    let el = $("disposal-waste-classes");
    const chrome = document.querySelector(".map-details");
    if (!chrome) return;
    if (!el) {
      el = document.createElement("p");
      el.id = "disposal-waste-classes";
      el.className = "disposal-waste-classes";
      const sub = $("map-sub");
      if (sub) sub.insertAdjacentElement("afterend", el);
      else chrome.appendChild(el);
    }
    const text = wasteClassText(site);
    if (!text) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.textContent = "Accepted waste: " + text;
  }

  function ensureWaitPanel() {
    let panel = $("disposal-wait");
    if (panel) return panel;
    const details = document.querySelector(".map-details");
    if (!details) return null;
    panel = document.createElement("div");
    panel.id = "disposal-wait";
    panel.className = "disposal-wait";
    panel.hidden = true;
    panel.innerHTML =
      '<div class="disposal-wait-head"><h3>Facility status</h3>' +
      '<p class="muted">Averages use your report window (<span id="disposal-wait-window-label">24h</span>). ' +
      "Reports are shared with all users.</p></div>" +
      '<div class="disposal-wait-summary" id="disposal-wait-summary"></div>' +
      '<details class="disposal-wait-prefs"><summary>My average window</summary>' +
      '<form id="disposal-wait-prefs-form" class="disposal-wait-prefs-form">' +
      '<label>Hours of reports to average <input type="number" name="avg_window_hours" id="wait-avg-window" min="1" max="168" value="24" required></label>' +
      '<button type="submit" class="ghost">Save window</button></form></details>' +
      '<details class="disposal-wait-form-wrap"><summary>Report wait time / open lanes</summary>' +
      '<form id="disposal-wait-form" class="disposal-wait-form" novalidate>' +
      '<fieldset class="wait-kind"><legend>Report type</legend>' +
      '<label class="mode"><input type="radio" name="report_kind" value="actual" checked><span>Actual visit</span></label>' +
      '<label class="mode"><input type="radio" name="report_kind" value="partial"><span>Arrival only</span></label>' +
      '<label class="mode"><input type="radio" name="report_kind" value="estimated"><span>Estimated (team)</span></label>' +
      "</fieldset>" +
      '<div class="wait-datetime-row"><label>Arrival <input type="datetime-local" name="arrival_at" id="wait-arrival" required></label>' +
      '<button type="button" class="ghost wait-now-btn" data-target="wait-arrival">Now</button></div>' +
      '<div class="wait-datetime-row" id="wait-departure-row"><label>Departure <input type="datetime-local" name="departure_at" id="wait-departure" required></label>' +
      '<button type="button" class="ghost wait-now-btn" data-target="wait-departure">Now</button></div>' +
      '<label>Open lanes <select name="open_lanes" id="wait-open-lanes"><option value="">Unknown</option>' +
      Array.from({ length: 21 }, (_, i) => '<option value="' + i + '">' + i + "</option>").join("") +
      "</select></label>" +
      '<p class="hint" id="wait-kind-hint"></p>' +
      '<p class="banner error" id="wait-form-error" hidden></p>' +
      '<button type="submit" class="primary">Submit report</button></form></details>';
    const footer = details.querySelector(".map-footer");
    if (footer) details.insertBefore(panel, footer);
    else details.appendChild(panel);
    bindWaitForms(panel);
    return panel;
  }

  function syncKindHints() {
    const kind =
      (document.querySelector('#disposal-wait-form input[name="report_kind"]:checked') || {}).value ||
      "actual";
    const hint = $("wait-kind-hint");
    const depRow = $("wait-departure-row");
    const dep = $("wait-departure");
    if (depRow) depRow.hidden = kind === "partial";
    if (dep) {
      dep.required = kind !== "partial";
      if (kind === "partial") dep.value = "";
    }
    if (!hint) return;
    if (kind === "partial") {
      hint.textContent = "Arrival only. Time cannot be in the future.";
    } else if (kind === "estimated") {
      hint.textContent =
        "Estimated arrival and departure must both be in the future and within 24 hours of each other. Shared with your team.";
    } else {
      hint.textContent =
        "Actual visits need arrival and departure (no future times). Interval must be within 24 hours.";
    }
  }

  function validateWaitPayload(kind, arrivalIso, departureIso) {
    const skewMs = 120 * 1000;
    const maxIntervalMs = 24 * 60 * 60 * 1000;
    const now = Date.now();
    if (!arrivalIso) return "Arrival time is required.";
    const arrivalMs = Date.parse(arrivalIso);
    if (Number.isNaN(arrivalMs)) return "Arrival time must be a valid date.";
    if (kind === "partial") {
      if (departureIso) return "Arrival-only reports cannot include a departure time.";
      if (arrivalMs > now + skewMs) return "Arrival cannot be in the future for arrival-only reports.";
      return null;
    }
    if (!departureIso) {
      return kind === "estimated"
        ? "Estimated reports require a future departure time."
        : "Actual visits require a departure time. Use arrival-only for partial reports.";
    }
    const departureMs = Date.parse(departureIso);
    if (Number.isNaN(departureMs)) return "Departure time must be a valid date.";
    if (departureMs < arrivalMs) return "Departure must be on or after arrival.";
    if (departureMs - arrivalMs > maxIntervalMs) return "Wait interval cannot exceed 24 hours.";
    if (kind === "estimated") {
      if (arrivalMs <= now - skewMs) return "Estimated arrival must be in the future.";
      if (departureMs <= now - skewMs) return "Estimated departure must be in the future.";
    } else {
      if (arrivalMs > now + skewMs) return "Arrival cannot be in the future for actual visits.";
      if (departureMs > now + skewMs) return "Departure cannot be in the future for actual visits.";
    }
    return null;
  }

  function renderSummary(payload) {
    const root = $("disposal-wait-summary");
    if (!root) return;
    const windowHours = payload.window_hours || 24;
    const label = $("disposal-wait-window-label");
    if (label) label.textContent = windowHours + "h";
    const avg = payload.avg_wait_minutes;
    const count = payload.report_count || 0;
    const lanes = payload.open_lanes_latest;
    const parts = [];
    parts.push(
      "<p><strong>Avg wait (" +
        windowHours +
        "h):</strong> " +
        formatMinutes(avg) +
        (count ? " · " + count + " report" + (count === 1 ? "" : "s") : " · no reports in window") +
        "</p>"
    );
    if (lanes != null && lanes !== "") {
      parts.push("<p><strong>Open lanes (latest):</strong> " + lanes + "</p>");
    }
    const recent = payload.recent_reports || [];
    if (recent.length) {
      parts.push('<ul class="wait-report-list">');
      recent.slice(0, 8).forEach((r) => {
        const kind = r.report_kind || "actual";
        const wait = formatMinutes(r.wait_minutes);
        const when = formatWhen(r.created_at || r.arrival_at);
        const laneBit = r.open_lanes != null && r.open_lanes !== "" ? " · " + r.open_lanes + " lanes" : "";
        const flagBtn =
          '<button type="button" class="ghost wait-flag-btn" data-report-id="' +
          r.id +
          '">Flag</button>';
        parts.push(
          "<li><span>" +
            when +
            " · " +
            kind +
            " · wait " +
            wait +
            laneBit +
            "</span> " +
            flagBtn +
            "</li>"
        );
      });
      parts.push("</ul>");
    }
    const estimated = payload.estimated_for_org || [];
    if (estimated.length) {
      parts.push('<p class="muted"><strong>Team estimates</strong></p><ul class="wait-report-list">');
      estimated.slice(0, 5).forEach((r) => {
        parts.push(
          "<li>" +
            formatWhen(r.arrival_at) +
            (r.departure_at ? " → " + formatWhen(r.departure_at) : "") +
            (r.open_lanes != null ? " · " + r.open_lanes + " lanes" : "") +
            "</li>"
        );
      });
      parts.push("</ul>");
    }
    root.innerHTML = parts.join("");
  }

  async function loadWaitSummary(siteId) {
    const panel = ensureWaitPanel();
    if (!panel || !siteId) return;
    panel.hidden = false;
    panel.dataset.siteId = String(siteId);
    const root = $("disposal-wait-summary");
    if (root) root.innerHTML = '<p class="muted">Loading recent wait reports…</p>';
    try {
      const resp = await apiFetch("/disposal/" + encodeURIComponent(siteId) + "/wait", {
        headers: { Accept: "application/json" },
      });
      const body = await resp.json();
      if (!resp.ok) {
        const msg =
          body.error === "sign_in_required"
            ? "Sign in to view and submit facility wait reports."
            : body.error || "Could not load wait reports";
        throw new Error(msg);
      }
      renderSummary(body);
      if (body.window_hours && $("wait-avg-window")) {
        $("wait-avg-window").value = String(body.window_hours);
      }
    } catch (err) {
      if (root) {
        root.innerHTML =
          '<p class="banner error">' + (err.message || "Could not load wait reports") + "</p>";
      }
    }
  }

  function hideWaitPanel() {
    const panel = $("disposal-wait");
    if (panel) panel.hidden = true;
    const waste = $("disposal-waste-classes");
    if (waste) {
      waste.hidden = true;
      waste.textContent = "";
    }
  }

  function bindWaitForms(panel) {
    if (!panel || panel.dataset.bound === "1") return;
    panel.dataset.bound = "1";
    panel.addEventListener("click", (event) => {
      const nowBtn = event.target.closest(".wait-now-btn");
      if (nowBtn) {
        event.preventDefault();
        const target = $(nowBtn.dataset.target);
        if (target) target.value = toLocalInputValue(new Date());
        return;
      }
      const flag = event.target.closest(".wait-flag-btn");
      if (flag) {
        event.preventDefault();
        const id = flag.dataset.reportId;
        const reason = window.prompt("Why is this report wrong?", "Incorrect wait time");
        if (!reason || !id) return;
        apiFetch("/disposal/wait/" + encodeURIComponent(id) + "/flag", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ reason }),
        }).then(() => {
          const siteId = panel.dataset.siteId;
          if (siteId) loadWaitSummary(siteId);
        });
      }
    });
    panel.addEventListener("change", (event) => {
      if (event.target && event.target.name === "report_kind") syncKindHints();
    });
    const form = $("disposal-wait-form");
    if (form) {
      const arrival = $("wait-arrival");
      if (arrival && !arrival.value) arrival.value = toLocalInputValue(new Date());
      syncKindHints();
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const errEl = $("wait-form-error");
        if (errEl) {
          errEl.hidden = true;
          errEl.textContent = "";
        }
        const siteId = panel.dataset.siteId;
        if (!siteId) return;
        const kind =
          (form.querySelector('input[name="report_kind"]:checked') || {}).value || "actual";
        const arrivalAt = localInputToIso($("wait-arrival") && $("wait-arrival").value);
        const departureAt =
          kind === "partial"
            ? null
            : localInputToIso($("wait-departure") && $("wait-departure").value);
        const clientError = validateWaitPayload(kind, arrivalAt, departureAt);
        if (clientError) {
          if (errEl) {
            errEl.hidden = false;
            errEl.textContent = clientError;
          }
          return;
        }
        const payload = {
          report_kind: kind,
          arrival_at: arrivalAt,
          departure_at: departureAt,
          open_lanes: ($("wait-open-lanes") && $("wait-open-lanes").value) || null,
        };
        try {
          const resp = await apiFetch("/disposal/" + encodeURIComponent(siteId) + "/wait", {
            method: "POST",
            headers: { "Content-Type": "application/json", Accept: "application/json" },
            body: JSON.stringify(payload),
          });
          const body = await resp.json();
          if (!resp.ok) {
            const msg =
              body.error === "sign_in_required"
                ? "Sign in to submit facility wait reports."
                : body.error || "Report failed";
            throw new Error(msg);
          }
          loadWaitSummary(siteId);
          const wrap = document.querySelector(".disposal-wait-form-wrap");
          if (wrap) wrap.open = false;
        } catch (err) {
          if (errEl) {
            errEl.hidden = false;
            errEl.textContent = err.message || String(err);
          }
        }
      });
    }
    const prefs = $("disposal-wait-prefs-form");
    if (prefs) {
      prefs.addEventListener("submit", async (event) => {
        event.preventDefault();
        const hours = Number(($("wait-avg-window") && $("wait-avg-window").value) || 24);
        await apiFetch("/account/wait-prefs", {
          method: "POST",
          headers: { "Content-Type": "application/json", Accept: "application/json" },
          body: JSON.stringify({ avg_window_hours: hours }),
        });
        const siteId = panel.dataset.siteId;
        if (siteId) loadWaitSummary(siteId);
      });
    }
  }

  function onDisposalSelected(site) {
    if (!site || !site.id) {
      hideWaitPanel();
      return;
    }
    renderWasteClasses(site);
    const panel = ensureWaitPanel();
    // Skip duplicate fetches when chrome re-selects the same pinned site.
    if (panel && panel.dataset.siteId === String(site.id) && !panel.hidden) {
      panel.hidden = false;
      return;
    }
    loadWaitSummary(site.id);
  }

  window.WellnavDisposalUx = {
    enrichDisposalFocus,
    onDisposalSelected,
    hideWaitPanel,
    loadWaitSummary,
    bindNearSearch,
    wasteClassText,
  };

  function boot() {
    bindNearSearch();
    ensureWaitPanel();
  }
  document.addEventListener("DOMContentLoaded", boot);
  if (document.readyState !== "loading") boot();
})();
