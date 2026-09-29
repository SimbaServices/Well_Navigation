/* Stamp last-opened when a signed-in user opens the app or brings it back,
   and last-activity on each tap, search, or other interaction. Store shells
   call wellnavRecordOpen() on native resume: a paused iOS or Android WebView
   often does not reload or fire visibilitychange. The same page script runs
   inside those WebViews, so interactions there are included. */
(function () {
  var last = 0;
  var activityAt = 0;
  var wentHidden = false;
  var KEY = "wn-open-marked";

  function marked() {
    try {
      return sessionStorage.getItem(KEY) === "1";
    } catch (err) {
      return false;
    }
  }

  function mark() {
    try {
      sessionStorage.setItem(KEY, "1");
    } catch (err) {}
  }

  function send() {
    var now = Date.now();
    if (now - last < 2000) return;
    last = now;
    fetch("/session/opened", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      keepalive: true,
    }).catch(function () {});
  }

  function sendActivity() {
    var now = Date.now();
    if (now - activityAt < 250) return;
    activityAt = now;
    fetch("/session/activity", {
      method: "POST",
      credentials: "same-origin",
      headers: { Accept: "application/json" },
      keepalive: true,
    }).catch(function () {});
  }

  window.wellnavRecordOpen = function () {
    send();
    mark();
  };

  document.addEventListener("pointerup", sendActivity, true);
  document.addEventListener("click", sendActivity, true);
  document.addEventListener("change", sendActivity, true);
  document.addEventListener("submit", sendActivity, true);
  document.addEventListener("input", sendActivity, true);

  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "hidden") {
      wentHidden = true;
      return;
    }
    if (!wentHidden && marked()) return;
    wentHidden = false;
    send();
    mark();
  });

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) return;
    send();
    mark();
  });

  if (!marked()) {
    if (document.visibilityState === "hidden") wentHidden = true;
    else {
      send();
      mark();
    }
  }
})();
