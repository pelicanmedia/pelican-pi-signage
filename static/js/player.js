const stage   = document.getElementById("stage");
const idle    = document.getElementById("idle");
const idleMsg = document.getElementById("idleMsg");

let currentId    = null;
let currentLayer = null;
let pollTimer    = null;
let forceNext    = false;
let preloaded    = null;   // { id, src, layer } built ahead of time for the next item

const PRELOAD_SECONDS = 8;  // start loading the next item this long before it's due

const VIDEO_URL_RE  = /\.(mp4|webm|mov|m4v)(\?.*)?$/i;
const STREAM_URL_RE = /\.m3u8(\?.*)?$/i;   // HLS live streams / playlists

function messageBox(title, detail, note) {
  const box = document.createElement("div");
  box.className = "message";
  box.innerHTML = `
    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
        d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
    </svg>
    <div class="message-title"></div>
    <div class="message-detail"></div>
    <div class="message-note"></div>`;
  box.querySelector(".message-title").textContent  = title || "";
  box.querySelector(".message-detail").textContent = detail || "";
  box.querySelector(".message-note").textContent   = note || "";
  return box;
}

// Stop a layer's video/stream/timers so the Pi frees the decoder and network
function disposeLayer(layer) {
  if (!layer) return;
  if (layer._cleanup) { try { layer._cleanup(); } catch (_) {} }
  layer.querySelectorAll("video").forEach(v => {
    v.pause();
    v.removeAttribute("src");
    v.load();
  });
  layer.remove();
}

function makeVideo(src, deferPlay, layer, asset) {
  const vid = document.createElement("video");
  vid.preload     = "auto";
  vid.autoplay    = !deferPlay;
  vid.muted       = true;   // required for autoplay in Chromium
  vid.playsInline = true;
  vid.controls    = false;

  const isStream = STREAM_URL_RE.test(src);
  if (isStream && window.Hls && Hls.isSupported()) {
    // Use the bundled hls.js even where Chromium has native HLS (new and
    // patchy across builds), so streams behave the same on every Pi
    // capLevelToPlayerSize: never fetch more pixels than the screen shows
    const hls = new Hls({ capLevelToPlayerSize: true, backBufferLength: 30 });
    let recovered = false;
    hls.on(Hls.Events.ERROR, (_e, data) => {
      if (!data.fatal) return;
      if (!recovered && data.type === Hls.ErrorTypes.MEDIA_ERROR) {
        recovered = true;
        hls.recoverMediaError();
      } else if (!recovered && data.type === Hls.ErrorTypes.NETWORK_ERROR) {
        recovered = true;
        hls.startLoad();
      } else {
        hls.destroy();
        vid.style.display = "none";
        layer.appendChild(messageBox(asset.name, asset.src, "The live stream could not be played"));
      }
    });
    hls.loadSource(src);
    hls.attachMedia(vid);
    layer._cleanup = () => hls.destroy();
  } else {
    vid.src = src;
  }

  // Videos move on when they finish. Live streams never end, so they run
  // for their set duration (a recorded HLS playlist that ends still advances).
  vid.addEventListener("ended", async () => {
    forceNext = true;
    await fetch("/api/advance", { method: "POST" });
    poll();
  });
  return vid;
}

// Reload a web page every refresh_seconds while it's on screen. The new copy
// loads behind the old one and replaces it once loaded, so there's no flash.
function startAutoRefresh(div, asset, seconds) {
  const timer = setInterval(() => {
    const old = div.querySelector("iframe");
    if (!old) return;
    const fresh = old.cloneNode(false);
    fresh.style.cssText = "position:absolute; inset:0; visibility:hidden;";
    fresh.addEventListener("load", () => {
      fresh.style.cssText = "";
      old.remove();
      div.querySelectorAll(".message").forEach(m => m.remove());  // back online
    }, { once: true });
    fresh.src = asset.src;
    div.appendChild(fresh);
  }, seconds * 1000);
  const prev = div._cleanup;
  div._cleanup = () => { clearInterval(timer); if (prev) prev(); };
}

// Build a DOM layer for the given asset. With deferPlay, videos load but
// don't start until the layer is shown.
function buildLayer(asset, deferPlay = false) {
  const div = document.createElement("div");
  div.className = "layer";

  if (asset.type === "image") {
    const img = document.createElement("img");
    img.src = asset.src;
    div.appendChild(img);

  } else if (asset.type === "video") {
    div.appendChild(makeVideo(asset.src, deferPlay, div, asset));

  } else if (asset.type === "url") {
    // Direct video file and live stream (HLS) URLs play in a video element
    if (VIDEO_URL_RE.test(asset.src) || STREAM_URL_RE.test(asset.src)) {
      div.appendChild(makeVideo(asset.src, deferPlay, div, asset));
      return div;
    }

    const frame = document.createElement("iframe");
    frame.setAttribute("allowfullscreen", "");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups");

    let fallback = null;
    const showFallback = (message) => {
      frame.style.display = "none";
      if (fallback) fallback.remove();
      fallback = messageBox(asset.name, asset.src, message);
      div.appendChild(fallback);
    };

    frame.addEventListener("error", () => showFallback("This site blocks iframe embedding (X-Frame-Options)"));

    // Probe reachability before loading the iframe so a styled fallback is shown
    // instead of the browser's own error page when the network is unavailable.
    // mode:'no-cors' gives an opaque response for any reachable server (including
    // CORS-restricted ones), and throws only on genuine network failure.
    const controller = new AbortController();
    const probeTimer = setTimeout(() => controller.abort(), 3000);
    fetch(asset.src, { mode: "no-cors", signal: controller.signal })
      .then(() => {
        clearTimeout(probeTimer);
        frame.src = asset.src;
      })
      .catch(() => {
        clearTimeout(probeTimer);
        showFallback("Could not reach this URL — check your internet connection");
      });

    div.appendChild(frame);

    const refresh = asset.options && asset.options.refresh_seconds;
    if (refresh) startAutoRefresh(div, asset, refresh);
  }

  return div;
}

// Fade a layer out, then dispose of it (with a fallback in case the
// transition never fires, e.g. while the page is hidden)
function fadeOut(layer) {
  let done = false;
  const finish = () => { if (!done) { done = true; disposeLayer(layer); } };
  layer.classList.remove("visible");
  layer.addEventListener("transitionend", finish, { once: true });
  setTimeout(finish, 1500);
}

// Cross-fade to a new asset
function showAsset(asset) {
  const prev = currentLayer;

  let next;
  if (preloaded && preloaded.id === asset.id && preloaded.src === asset.src) {
    next = preloaded.layer;          // already buffered
    const vid = next.querySelector("video");
    if (vid) { vid.currentTime = 0; vid.play().catch(() => {}); }
  } else {
    if (preloaded) disposeLayer(preloaded.layer);  // wasn't needed after all
    next = buildLayer(asset);
  }
  preloaded = null;
  stage.appendChild(next);

  // Trigger reflow so the transition fires
  void next.offsetWidth;
  next.classList.add("visible");

  if (prev) fadeOut(prev);

  currentLayer = next;
  currentId    = asset.id;
  idle.style.display = "none";
}

// Build the next video/image off-screen so it starts without a gap.
// Web pages aren't preloaded (they'd run scripts and probe the network early).
function maybePreload(asset) {
  const up = asset.upcoming;
  if (!up || up.id === asset.id || asset.remaining > PRELOAD_SECONDS) return;
  if (preloaded && preloaded.id === up.id && preloaded.src === up.src) return;

  // Live streams aren't preloaded: they'd start buffering a moving target
  const isVideoUrl = up.type === "url" && VIDEO_URL_RE.test(up.src);
  if (preloaded) { disposeLayer(preloaded.layer); preloaded = null; }
  if (up.type === "video" || isVideoUrl) {
    preloaded = { id: up.id, src: up.src, layer: buildLayer(up, true) };
  } else if (up.type === "image") {
    preloaded = { id: up.id, src: up.src, layer: buildLayer(up) };
  }
}

// Poll /api/next and schedule the next poll based on remaining time
async function poll() {
  clearTimeout(pollTimer);

  let asset = null;
  try {
    const res = await fetch("/api/next", { cache: "no-store" });
    asset = await res.json();
  } catch (_) {
    // Network error – retry in 5 s
    pollTimer = setTimeout(poll, 5000);
    return;
  }

  // Apply background colour on every poll
  if (asset && asset.bg_color) {
    document.body.style.background = asset.bg_color;
    stage.style.background = asset.bg_color;
  }

  if (asset && asset.screen_off) {
    // Screen is scheduled off: stop everything and show plain black (the
    // server also turns the output / TV off where it can)
    if (preloaded) { disposeLayer(preloaded.layer); preloaded = null; }
    if (currentLayer) { disposeLayer(currentLayer); currentLayer = null; }
    currentId = null;
    idle.style.display = "none";
    document.body.style.background = "#000";
    stage.style.background = "#000";
    pollTimer = setTimeout(poll, 5000);
    return;
  }

  if (!asset || !asset.type) {
    // No enabled assets
    if (currentLayer) {
      const dying = currentLayer;
      currentLayer = null;
      fadeOut(dying);
    }
    currentId = null;
    idleMsg.textContent = (asset && asset.idle_message) || "No content enabled in playlist.";
    idle.style.display = "flex";
    pollTimer = setTimeout(poll, 5000);
    return;
  }

  if (asset.id !== currentId || forceNext) {
    forceNext = false;
    showAsset(asset);
  }

  maybePreload(asset);

  // Schedule next poll slightly before the server will advance (min 1 s, max 2 s so
  // skips from the admin page and disabled items show up quickly)
  const delay = Math.min(2000, Math.max(1000, (asset.remaining - 0.3) * 1000));
  pollTimer = setTimeout(poll, delay);
}

// Kick off
poll();
