/* Visible search-filter chips, reset on context switch, and "Clear filters" hint.
   Filters live as hidden inputs (#active-filters, #column-filters, lease_no...) that ride
   along with every search. This file shows them as chips and drops them when the search
   context (scope or state) changes, so old filters can't silently combine with a new search. */
(function (root) {
  "use strict";

  var KEEP = { sort: 1, dir: 1 };
  var STALE_FORM_INPUTS = "input[name=lease_no], input[name=district], input[name=lease_type]";

  function byId(doc, id) {
    return doc.getElementById(id);
  }

  function describe(doc) {
    var chips = [];
    var af = byId(doc, "active-filters");
    var form = byId(doc, "search-form");
    if (af) {
      af.querySelectorAll("input[name=opn]").forEach(function (el) {
        var parts = String(el.value).split("|");
        var label = (parts.slice(1).join("|") || parts[0] || "").trim();
        if (label) chips.push({ kind: "op", key: parts[0], label: "Operator: " + label });
      });
      var name = af.querySelector("input[name=name]");
      var useName = af.querySelector("input[name=use_name]");
      if (name && name.value && useName) chips.push({ kind: "name", label: "Name: " + name.value });
      var api = af.querySelector("input[name=api]");
      var useApi = af.querySelector("input[name=use_api]");
      if (api && api.value && useApi) chips.push({ kind: "api", label: "API: " + api.value });
    }
    if (form) {
      var lease = form.querySelector("input[name=lease_no]");
      if (lease && lease.value) chips.push({ kind: "lease", label: "Lease " + lease.value });
    }
    var cols = byId(doc, "column-filters");
    if (cols) {
      cols.querySelectorAll("input[name^=cf_]").forEach(function (el) {
        if (el.value) chips.push({ kind: "col", key: el.name, label: el.name.slice(3) + ": " + el.value });
      });
    }
    return chips;
  }

  function removeOne(doc, chip) {
    var af = byId(doc, "active-filters");
    var form = byId(doc, "search-form");
    var cols = byId(doc, "column-filters");
    if (chip.kind === "op" && af) {
      af.querySelectorAll("input[name=opn]").forEach(function (el) {
        if (String(el.value).split("|")[0] === chip.key) el.remove();
      });
      af.querySelectorAll("input[name=op]").forEach(function (el) {
        if (el.value === chip.key) el.remove();
      });
    } else if ((chip.kind === "name" || chip.kind === "api") && af) {
      af.querySelectorAll("input[name=" + chip.kind + "], input[name=use_" + chip.kind + "]").forEach(function (el) {
        el.remove();
      });
    } else if (chip.kind === "lease" && form) {
      form.querySelectorAll(STALE_FORM_INPUTS).forEach(function (el) {
        el.remove();
      });
    } else if (chip.kind === "col" && cols) {
      cols.querySelectorAll("input[name=" + chip.key + "]").forEach(function (el) {
        el.remove();
      });
    }
  }

  /* Drop every search-specific filter. Sort/direction, map, basemap and layers are untouched. */
  function resetFilters(doc) {
    var af = byId(doc, "active-filters");
    var form = byId(doc, "search-form");
    var cols = byId(doc, "column-filters");
    if (af) {
      af.querySelectorAll("input").forEach(function (el) {
        if (!KEEP[el.name]) el.remove();
      });
    }
    if (form) form.querySelectorAll(STALE_FORM_INPUTS).forEach(function (el) {
      el.remove();
    });
    if (cols) cols.replaceChildren();
    renderChips(doc);
  }

  function renderChips(doc) {
    var box = byId(doc, "filter-chips");
    if (!box) return;
    var chips = describe(doc);
    box.replaceChildren();
    if (!chips.length) {
      box.hidden = true;
      return;
    }
    var wrap = doc.createElement("div");
    wrap.className = "filter-chips";
    chips.forEach(function (chip) {
      var el = doc.createElement("span");
      el.className = "filter-chip";
      var text = doc.createElement("span");
      text.textContent = chip.label;
      var x = doc.createElement("button");
      x.type = "button";
      x.className = "chip-x";
      x.setAttribute("data-chip-remove", chip.kind);
      if (chip.key) x.setAttribute("data-chip-key", chip.key);
      x.setAttribute("aria-label", "Remove filter " + chip.label);
      x.textContent = "\u00d7";
      el.appendChild(text);
      el.appendChild(x);
      wrap.appendChild(el);
    });
    var clear = doc.createElement("button");
    clear.type = "button";
    clear.className = "ghost clear-all";
    clear.setAttribute("data-clear-filters", "1");
    clear.textContent = "Clear all";
    wrap.appendChild(clear);
    box.appendChild(wrap);
    box.hidden = false;
  }

  function formValues(doc) {
    var form = byId(doc, "search-form");
    var out = {};
    if (!form) return out;
    new root.FormData(form).forEach(function (value, key) {
      if (key === "q" || key === "commit") return;
      out[key] = value;
    });
    var cols = byId(doc, "column-filters");
    if (cols) cols.querySelectorAll("input[name^=cf_]").forEach(function (el) {
      if (el.value) out[el.name] = el.value;
    });
    return out;
  }

  function emptyResults(doc, message) {
    var results = byId(doc, "results");
    if (!results) return;
    results.replaceChildren();
    var p = doc.createElement("p");
    p.className = "muted save-hint";
    p.textContent = message;
    results.appendChild(p);
  }

  function rerun(doc) {
    if (describe(doc).length === 0) {
      emptyResults(doc, "Filters cleared. Search to see wells.");
      return;
    }
    if (root.htmx) {
      root.htmx.ajax("GET", "/search", { target: "#results", values: formValues(doc), swap: "innerHTML" });
    }
  }

  function clearAll(doc) {
    resetFilters(doc);
    emptyResults(doc, "Filters cleared. Search to see wells.");
    try {
      root.history.replaceState(null, "", root.location.pathname);
    } catch (e) {
      /* ignore */
    }
  }

  function onClick(doc, event) {
    var target = event.target;
    var clear = target.closest && target.closest("[data-clear-filters]");
    if (clear) {
      event.preventDefault();
      clearAll(doc);
      return;
    }
    var x = target.closest && target.closest("[data-chip-remove]");
    if (x) {
      event.preventDefault();
      removeOne(doc, { kind: x.getAttribute("data-chip-remove"), key: x.getAttribute("data-chip-key") || "" });
      renderChips(doc);
      rerun(doc);
    }
  }

  function onChange(doc, event) {
    var t = event.target;
    if (!t || !t.closest || !t.closest("#search-form")) return;
    if (t.name === "scope" || t.name === "state") resetFilters(doc);
  }

  var api = { describe: describe, removeOne: removeOne, resetFilters: resetFilters, renderChips: renderChips, clearAll: clearAll, formValues: formValues };
  root.WellnavSearchFilters = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;

  if (root.document && root.document.addEventListener) {
    var doc = root.document;
    // Capture phase: runs before map.js refetches, so the request carries no stale filters.
    doc.addEventListener("change", function (e) { onChange(doc, e); }, true);
    doc.addEventListener("click", function (e) { onClick(doc, e); });
    var refresh = function () { renderChips(doc); };
    doc.addEventListener("htmx:afterSwap", refresh);
    doc.addEventListener("htmx:afterSettle", refresh);
    if (doc.readyState === "loading") doc.addEventListener("DOMContentLoaded", refresh);
    else refresh();
  }
})(typeof window !== "undefined" ? window : globalThis);
