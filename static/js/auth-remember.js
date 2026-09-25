/* Sign-in persistence.
   The session cookie signs the user back in after the app closes.
   Remember me is separate: after an explicit sign-out, refill email and
   password from this device. A successful sign-in with the box unchecked,
   a new password, or account deletion removes that saved copy. */
(function () {
  var KEY = "wellnav.remember";
  var PENDING = "wellnav.remember.pending";

  function localStore() {
    try {
      return window.localStorage;
    } catch (err) {
      return null;
    }
  }

  function sessionStore() {
    try {
      return window.sessionStorage;
    } catch (err) {
      return null;
    }
  }

  function readSaved() {
    var store = localStore();
    if (!store) return null;
    try {
      var data = JSON.parse(store.getItem(KEY) || "");
      if (!data || typeof data.email !== "string" || typeof data.password !== "string") return null;
      if (!data.email || !data.password) return null;
      return data;
    } catch (err) {
      return null;
    }
  }

  function writeSaved(email, password) {
    var store = localStore();
    if (!store) return;
    store.setItem(KEY, JSON.stringify({ email: email, password: password }));
  }

  function clearSaved() {
    var store = localStore();
    if (!store) return;
    store.removeItem(KEY);
  }

  function stage(payload) {
    var store = sessionStore();
    if (!store) return;
    store.setItem(PENDING, JSON.stringify(payload));
  }

  function peekPending() {
    var store = sessionStore();
    if (!store) return null;
    try {
      var raw = store.getItem(PENDING);
      if (!raw) return null;
      return JSON.parse(raw);
    } catch (err) {
      return null;
    }
  }

  function dropPending() {
    var store = sessionStore();
    if (!store) return;
    store.removeItem(PENDING);
  }

  function commitPending() {
    var pending = peekPending();
    dropPending();
    if (!pending) return;
    if (pending.clear || !pending.remember || !pending.email || !pending.password) {
      clearSaved();
      return;
    }
    writeSaved(pending.email, pending.password);
  }

  function forgetFromQuery() {
    var params;
    try {
      params = new URLSearchParams(window.location.search);
    } catch (err) {
      return;
    }
    if (params.get("forget") !== "1") return;
    clearSaved();
    dropPending();
    params.delete("forget");
    var query = params.toString();
    var next = window.location.pathname + (query ? "?" + query : "") + window.location.hash;
    if (window.history && history.replaceState) history.replaceState(null, "", next);
  }

  function fill(form) {
    var saved = readSaved();
    if (!saved) return;
    var emailInput = form.querySelector("[name=email]");
    var passwordInput = form.querySelector("[name=password]");
    var box = form.querySelector("[name=remember]");
    if (!emailInput || !passwordInput || !box) return;
    if (emailInput.value.trim()) return;
    emailInput.value = saved.email;
    passwordInput.value = saved.password;
    box.checked = true;
  }

  function settleLoginForm(form) {
    if (!form || !form.querySelector("[name=remember]")) return;
    var pending = peekPending();
    dropPending();
    if (pending && !pending.clear) {
      var box = form.querySelector("[name=remember]");
      if (box) box.checked = !!pending.remember;
      return;
    }
    fill(form);
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form || !form.getAttribute) return;
    if (form.getAttribute("data-remember") === "clear") {
      stage({ remember: false, clear: true });
      return;
    }
    var box = form.querySelector("[name=remember]");
    if (!box) return;
    var emailEl = form.querySelector("[name=email]");
    var passwordEl = form.querySelector("[name=password]");
    stage({
      remember: !!box.checked,
      email: emailEl ? emailEl.value.trim() : "",
      password: passwordEl ? passwordEl.value : "",
    });
  }, true);

  document.addEventListener("htmx:afterSwap", function (event) {
    var target = event.detail && event.detail.target;
    if (!target || target.id !== "auth-main") return;
    var form = target.querySelector && target.querySelector("form.auth-form");
    if (form && form.querySelector("[name=remember]")) {
      settleLoginForm(form);
      return;
    }
    dropPending();
  });

  function boot() {
    if (!document.body) return;
    if (document.body.classList.contains("gate")) {
      forgetFromQuery();
      var box = document.querySelector("form.auth-form [name=remember]");
      if (box && box.form) settleLoginForm(box.form);
      else dropPending();
      return;
    }
    commitPending();
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
