/* Map is the base layer. Search, saved places, and account slide over it.
   Team covers the workspace. Results and location details pull on and off. */
(function () {
  const PREF_KEYS = ["state", "scope", "mode", "pipe_mode", "disp_mode"];

  function sheet(id) {
    return document.getElementById(id);
  }

  function markTiles(key, value) {
    document.querySelectorAll("[data-pref='" + key + "']").forEach((el) => {
      const on = el.dataset.value === value;
      el.classList.toggle("is-on", on);
      el.setAttribute("aria-pressed", on ? "true" : "false");
    });
  }

  function savePref(key, value) {
    if (!PREF_KEYS.includes(key) || !value) return;
    const body = new URLSearchParams();
    body.set(key, value);
    fetch("/account/prefs", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/x-www-form-urlencoded", Accept: "application/json" },
      body,
    }).catch(function () {});
  }

  const VIEWS = ["search", "pins", "saved", "account", "settings", "results", "details"];

  function pullTeamOutOfResults() {
    const stray = document.querySelector("#results .team-page");
    const body = sheet("team-body");
    if (stray && body && !body.contains(stray)) body.appendChild(stray);
  }

  function showView(name, open) {
    if (name === "search") pullTeamOutOfResults();
    const dock = sheet("bottom-sheet");
    const tab = sheet("sheet-tab");
    if (dock) {
      dock.dataset.view = name || "";
      dock.classList.toggle("is-open", !!open);
      if (!open || name === "details" || name === "pins" || name === "saved") dock.style.height = "";
    }
    if (tab) {
      tab.setAttribute("aria-expanded", open ? "true" : "false");
      tab.setAttribute("aria-label", name ? name : "Panel");
    }
    document.querySelectorAll("#sheet-views > .sheet-view").forEach((el) => {
      el.hidden = el.dataset.view !== name;
    });
    document.querySelectorAll(".nav-tile[data-open]").forEach((button) => {
      button.setAttribute("aria-pressed", open && button.dataset.open === name ? "true" : "false");
    });
  }

  function closeFloats() {
    const dock = sheet("bottom-sheet");
    showView(dock ? dock.dataset.view : "", false);
  }

  function openLayer(name) {
    if (!VIEWS.includes(name)) return;
    showView(name, true);
  }

  function setDock(open, height) {
    const dock = sheet("bottom-sheet");
    if (!dock) return;
    if (open) showView("search", true);
    else showView(dock.dataset.view, false);
    if (typeof height === "number" && open) dock.style.height = Math.round(height) + "px";
    else if (!open) dock.style.height = "";
  }

  function openDock() {
    const dock = sheet("bottom-sheet");
    if (!dock) return;
    if (!(dock.classList.contains("is-open") && dock.dataset.view === "search")) setDock(true);
  }

  function shutDock() {
    setDock(false);
  }

  function resultsHaveContent() {
    const results = sheet("results");
    if (!results) return false;
    return !!results.querySelector("table, .account-card, .banner, .lease-item, .empty, ul, ol");
  }

  function onResults() {
    if (resultsHaveContent()) openDock();
  }

  function setDetail(open) {
    if (open) showView("details", true);
    else {
      const dock = sheet("bottom-sheet");
      if (dock && dock.dataset.view === "details") showView("details", false);
    }
  }

  function noteMapFocus(focus) {
    const tab = sheet("sheet-tab");
    const dock = sheet("bottom-sheet");
    if (tab) tab.classList.toggle("has-focus", !!focus && focus !== "idle");
    if (focus && focus !== "idle" && dock && !dock.classList.contains("is-open")) showView("details", false);
  }

  function bindDockDrag() {
    const dock = sheet("bottom-sheet");
    const tab = sheet("sheet-tab");
    if (!dock || !tab || tab.dataset.bound === "1") return;
    tab.dataset.bound = "1";
    let startY = 0;
    let startH = 0;
    let moved = false;
    let dragging = false;

    tab.addEventListener("pointerdown", (event) => {
      if (event.button !== undefined && event.button !== 0) return;
      dragging = true;
      moved = false;
      startY = event.clientY;
      startH = dock.getBoundingClientRect().height;
      dock.classList.add("is-dragging");
      tab.setPointerCapture(event.pointerId);
    });
    tab.addEventListener("pointermove", (event) => {
      if (!dragging) return;
      const delta = startY - event.clientY;
      if (Math.abs(delta) > 4) moved = true;
      const max = Math.min(window.innerHeight * 0.86, dock.parentElement ? dock.parentElement.clientHeight - 8 : window.innerHeight * 0.86);
      const next = Math.max(28, Math.min(max, startH + delta));
      dock.style.height = next + "px";
      dock.classList.toggle("is-open", next > 56);
    });
    function endDrag() {
      if (!dragging) return;
      dragging = false;
      dock.classList.remove("is-dragging");
      const height = dock.getBoundingClientRect().height;
      if (height < 64) shutDock();
      else dock.classList.add("is-open");
    }
    tab.addEventListener("pointerup", endDrag);
    tab.addEventListener("pointercancel", endDrag);
    tab.addEventListener("click", (event) => {
      if (moved) {
        event.preventDefault();
        moved = false;
        return;
      }
      if (dock.classList.contains("is-open")) shutDock();
      else showView(dock.dataset.view || "search", true);
    });
  }

  function syncFromForm() {
    const state = document.querySelector("#search-form [name=state]");
    if (state) markTiles("state", state.value);
    const scope = document.querySelector("#search-form [name=scope]");
    if (scope) markTiles("scope", scope.value);
  }

  document.body.addEventListener("click", (event) => {
    const teamTab = event.target.closest(".team-page [data-team-tab]");
    if (teamTab) {
      const page = teamTab.closest(".team-page");
      const name = teamTab.dataset.teamTab;
      page.dataset.teamTab = name;
      page.querySelectorAll("[data-team-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.teamPanel !== name;
      });
      return;
    }
    const settingsTab = event.target.closest(".settings-page [data-settings-tab]");
    if (settingsTab && settingsTab.closest(".sheet-tabs")) {
      const page = settingsTab.closest(".settings-page");
      const name = settingsTab.dataset.settingsTab;
      page.dataset.settingsTab = name;
      page.querySelectorAll("[data-settings-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.settingsPanel !== name;
      });
      return;
    }
    const pref = event.target.closest("[data-pref]");
    if (pref) {
      const key = pref.dataset.pref;
      const value = pref.dataset.value;
      markTiles(key, value);
      if (key === "state") {
        const input = document.querySelector("#search-form [name=state]");
        if (input) input.value = value;
        savePref(key, value);
      }
      if (key === "scope") {
        const select = document.querySelector("#search-form [name=scope]");
        if (select && select.value !== value) {
          select.value = value;
          select.dispatchEvent(new Event("change", { bubbles: true }));
        }
        savePref(key, value);
      }
      return;
    }
    const accountTab = event.target.closest("#account-sheet [data-account-tab]");
    if (accountTab) {
      const page = sheet("account-sheet");
      const name = accountTab.dataset.accountTab;
      page.dataset.accountTab = name;
      page.querySelectorAll("[data-account-panel]").forEach((panel) => {
        panel.hidden = panel.dataset.accountPanel !== name;
      });
      if (name !== "team") return;
    }
    const tile = event.target.closest(".nav-tile[data-open]");
    if (!tile) return;
    const name = tile.dataset.open;
    if (tile.getAttribute("aria-pressed") === "true") {
      event.preventDefault();
      event.stopPropagation();
      closeFloats();
      return;
    }
    openLayer(name);
  });

  document.body.addEventListener("change", (event) => {
    const name = event.target && event.target.name;
    if (name !== "mode" && name !== "pipe_mode" && name !== "disp_mode" && name !== "scope") return;
    savePref(name, event.target.value);
    if (name === "scope") markTiles("scope", event.target.value);
  });

  document.getElementById("search-form")?.addEventListener("submit", () => {
    openDock();
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeFloats();
  });

  bindDockDrag();
  syncFromForm();

  window.WellnavLayers = {
    onResults,
    noteMapFocus,
    openLayer,
    closeFloats,
    resetHome() {
      closeFloats();
      shutDock();
      setDetail(false);
    },
  };
})();
