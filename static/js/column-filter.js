/* Column-header text filters. The box stays closed until a header is clicked. */
(function () {
  function filtersRoot() {
    return document.getElementById("column-filters");
  }

  function syncHidden(input) {
    const root = filtersRoot();
    if (!root || !input) return;
    const key = input.getAttribute("data-cf");
    if (!key) return;
    const name = "cf_" + key;
    let hidden = root.querySelector('input[name="' + name + '"]');
    const value = input.value.trim();
    if (!value) {
      if (hidden) hidden.remove();
      markToggle(input);
      return;
    }
    if (!hidden) {
      hidden = document.createElement("input");
      hidden.type = "hidden";
      hidden.name = name;
      root.appendChild(hidden);
    }
    hidden.value = value;
    markToggle(input);
  }

  function markToggle(input) {
    const toggle = input.closest("th") && input.closest("th").querySelector("[data-col-filter]");
    if (!toggle) return;
    const dot = toggle.querySelector(".col-filter-dot");
    const active = input.value.trim().length > 0;
    if (active && !dot) {
      const mark = document.createElement("span");
      mark.className = "col-filter-dot";
      mark.setAttribute("aria-hidden", "true");
      toggle.appendChild(mark);
    } else if (!active && dot) {
      dot.remove();
    }
  }

  function closeFilters(exceptForm) {
    document.querySelectorAll("form.col-filter").forEach((form) => {
      if (form === exceptForm) return;
      form.hidden = true;
      const toggle = form.closest("th") && form.closest("th").querySelector("[data-col-filter]");
      if (toggle) toggle.setAttribute("aria-expanded", "false");
    });
  }

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-col-filter]");
    if (toggle) {
      const form = document.getElementById(toggle.getAttribute("aria-controls"));
      if (!form) return;
      const willOpen = form.hidden;
      closeFilters(willOpen ? form : null);
      form.hidden = !willOpen;
      toggle.setAttribute("aria-expanded", willOpen ? "true" : "false");
      if (willOpen) {
        const input = form.querySelector(".col-filter-input");
        if (input) {
          input.focus();
          const end = input.value.length;
          if (typeof input.setSelectionRange === "function") input.setSelectionRange(end, end);
        }
      }
      return;
    }
    if (!event.target.closest("form.col-filter")) closeFilters(null);
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeFilters(null);
  });

  document.addEventListener("submit", (event) => {
    const form = event.target;
    if (!form.classList || !form.classList.contains("col-filter")) return;
    event.preventDefault();
    const input = form.querySelector(".col-filter-input");
    if (input && window.htmx) {
      syncHidden(input);
      window.htmx.trigger(input, "search");
    }
  });

  function onFilterEdit(event) {
    const input = event.target && event.target.closest && event.target.closest(".col-filter-input");
    if (input) syncHidden(input);
  }
  document.addEventListener("input", onFilterEdit);
  document.addEventListener("search", onFilterEdit);

  document.body.addEventListener("htmx:configRequest", (event) => {
    const input = event.detail && event.detail.elt;
    if (!input || !input.classList || !input.classList.contains("col-filter-input")) return;
    syncHidden(input);
    const key = input.getAttribute("data-cf");
    const params = event.detail.parameters;
    const value = input.value.trim();
    if (value) params["cf_" + key] = value;
    else delete params["cf_" + key];
  });

  document.body.addEventListener("htmx:afterSwap", () => {
    const active = document.activeElement;
    if (active && active.classList && active.classList.contains("col-filter-input")) {
      syncHidden(active);
    }
  });
})();
