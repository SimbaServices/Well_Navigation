(() => {
  function parseEvents(text) {
    const events = [];
    text.split(/\n+/).forEach((line) => {
      if (!line.trim()) return;
      const parsed = JSON.parse(line);
      if (Array.isArray(parsed)) events.push(...parsed);
      else events.push(parsed);
    });
    return events;
  }

  function restyle(iframe) {
    const doc = iframe && iframe.contentDocument;
    if (!doc) return;
    if (doc.getElementById("wellnav-replay-css")) return;
    const link = doc.createElement("link");
    link.id = "wellnav-replay-css";
    link.rel = "stylesheet";
    link.href = "/static/css/app.css?v=crisp1";
    (doc.head || doc.documentElement).appendChild(link);
  }

  function fit(root) {
    const wrap = root.querySelector(".replayer-wrapper");
    const iframe = root.querySelector("iframe");
    if (!wrap || !iframe) return;
    restyle(iframe);
    const width = Number(iframe.getAttribute("width") || iframe.clientWidth || 0);
    const height = Number(iframe.getAttribute("height") || iframe.clientHeight || 0);
    if (!width || !height) return;
    const availW = root.clientWidth;
    const availH = root.clientHeight;
    if (!availW || !availH) return;
    const scale = Math.min(availW / width, availH / height, 1);
    wrap.style.position = "absolute";
    wrap.style.width = `${width}px`;
    wrap.style.height = `${height}px`;
    const left = Math.max(0, (availW - width * scale) / 2);
    const top = Math.max(0, (availH - height * scale) / 2);
    if ("zoom" in wrap.style) {
      wrap.style.zoom = String(scale);
      wrap.style.transform = "";
      wrap.style.left = `${left / scale}px`;
      wrap.style.top = `${top / scale}px`;
    } else {
      wrap.style.zoom = "";
      wrap.style.transformOrigin = "top left";
      wrap.style.transform = `scale(${scale})`;
      wrap.style.left = `${left}px`;
      wrap.style.top = `${top}px`;
    }
  }

  function showError(root, message) {
    root.textContent = message;
  }

  function bindPlay(button, toggle) {
    if (!button) return;
    button.addEventListener("click", toggle);
  }

  function playVideo(root, blob, playBtn) {
    const video = document.createElement("video");
    video.controls = true;
    video.src = URL.createObjectURL(blob);
    root.innerHTML = "";
    root.appendChild(video);
    bindPlay(playBtn, () => {
      if (video.paused) video.play();
      else video.pause();
    });
    video.play();
  }

  function playSession(root, events, playBtn, seek) {
    if (!window.rrweb || !rrweb.Replayer) {
      showError(root, "The session player failed to load.");
      return;
    }
    const hasSnapshot = events.some((event) => event && event.type === 2);
    if (!hasSnapshot || events.length < 2) {
      showError(root, "This session has no full page snapshot, so it cannot be replayed.");
      return;
    }
    const player = new rrweb.Replayer(events, {
      root,
      skipInactive: true,
      mouseTail: true,
    });
    let playing = true;
    const setLabel = () => {
      if (playBtn) playBtn.textContent = playing ? "Pause" : "Play";
    };
    const sync = () => fit(root);
    bindPlay(playBtn, () => {
      if (playing) {
        player.pause();
        playing = false;
      } else {
        player.play(player.getCurrentTime());
        playing = true;
      }
      setLabel();
    });
    player.on("finish", () => {
      playing = false;
      setLabel();
    });
    player.on("resize", sync);
    player.on("fullsnapshot-rebuilded", sync);
    const clock = document.getElementById("ux-time");
    const meta = player.getMetaData();
    const total = Math.max(1, meta.totalTime || 1);
    const format = (ms) => {
      const sec = Math.max(0, Math.round(Number(ms) / 1000));
      return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
    };
    const stamp = () => {
      const now = player.getCurrentTime();
      if (seek && document.activeElement !== seek) seek.value = String(now);
      if (clock) clock.textContent = `${format(now)} / ${format(total)}`;
    };
    if (seek) {
      seek.min = 0;
      seek.max = total;
      seek.value = 0;
      seek.addEventListener("input", () => {
        player.pause();
        playing = false;
        setLabel();
        player.play(Number(seek.value) || 0);
        playing = true;
        setLabel();
        stamp();
      });
    }
    setLabel();
    player.play();
    stamp();
    window.setInterval(stamp, 250);
    window.setTimeout(sync, 40);
    window.setTimeout(sync, 250);
    window.addEventListener("resize", sync);
    if (window.ResizeObserver) {
      new ResizeObserver(sync).observe(root);
    }
  }

  window.wellnavStartReplay = function wellnavStartReplay(id) {
    const root = document.getElementById("ux-player");
    const playBtn = document.getElementById("ux-play");
    const seek = document.getElementById("ux-seek");
    if (!root || !id) return;
    fetch(`/ux/recordings/${id}/file`, { credentials: "same-origin" })
      .then((response) => {
        if (!response.ok) throw new Error("missing");
        const type = response.headers.get("content-type") || "";
        if (type.indexOf("webm") !== -1) return response.blob().then((blob) => playVideo(root, blob, playBtn));
        return response.text().then((text) => playSession(root, parseEvents(text), playBtn, seek));
      })
      .catch(() => {
        showError(root, "Could not load this session. Pull it again, then watch from the local player.");
      });
  };
})();
