const stage   = document.getElementById("stage");
const idle    = document.getElementById("idle");
const idleMsg = document.getElementById("idleMsg");

let currentId    = null;
let currentLayer = null;
let pollTimer    = null;
let forceNext    = false;

// Build a DOM layer for the given asset
function buildLayer(asset) {
  const div = document.createElement("div");
  div.className = "layer";

  if (asset.type === "image") {
    const img = document.createElement("img");
    img.src = asset.src;
    div.appendChild(img);

  } else if (asset.type === "video") {
    const vid = document.createElement("video");
    vid.src         = asset.src;
    vid.autoplay    = true;
    vid.muted       = true;   // required for autoplay in Chromium
    vid.playsInline = true;
    vid.controls    = false;
    vid.addEventListener("ended", async () => {
      forceNext = true;
      await fetch("/api/advance", { method: "POST" });
      poll();
    });
    div.appendChild(vid);

  } else if (asset.type === "url") {
    // Treat direct video file URLs as a video element
    if (/\.(mp4|webm|mov|avi|m3u8)(\?.*)?$/i.test(asset.src)) {
      const vid = document.createElement("video");
      vid.src         = asset.src;
      vid.autoplay    = true;
      vid.muted       = true;
      vid.playsInline = true;
      vid.controls    = false;
      vid.addEventListener("ended", async () => {
        forceNext = true;
        await fetch("/api/advance", { method: "POST" });
        poll();
      });
      div.appendChild(vid);
      return div;
    }

    const frame = document.createElement("iframe");
    frame.src = asset.src;
    frame.setAttribute("allowfullscreen", "");
    frame.setAttribute("sandbox", "allow-scripts allow-same-origin allow-forms allow-popups");

    // Fallback overlay shown if the site blocks iframe embedding
    const fallback = document.createElement("div");
    fallback.style.cssText = `
      display:none; position:absolute; inset:0; align-items:center;
      justify-content:center; flex-direction:column; gap:1rem;
      background:#111; color:#888; font-family:system-ui,sans-serif; text-align:center; padding:2rem;
    `;
    fallback.innerHTML = `
      <svg style="width:48px;height:48px;opacity:.4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
          d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636"/>
      </svg>
      <div style="font-size:1rem;font-weight:600;color:#aaa">${asset.name}</div>
      <div style="font-size:.8rem">${asset.src}</div>
      <div style="font-size:.75rem;color:#666">This site blocks iframe embedding (X-Frame-Options)</div>
    `;

    frame.addEventListener("load", () => {
      try {
        void frame.contentDocument.title;
      } catch (_) {
        // Cross-origin — browser will show its own error if X-Frame-Options blocked it.
      }
    });
    frame.addEventListener("error", () => {
      frame.style.display = "none";
      fallback.style.display = "flex";
    });

    div.appendChild(frame);
    div.appendChild(fallback);
  }

  return div;
}

// Cross-fade to a new asset
function showAsset(asset) {
  const prev = currentLayer;

  const next = buildLayer(asset);
  stage.appendChild(next);

  // Trigger reflow so the transition fires
  void next.offsetWidth;
  next.classList.add("visible");

  if (prev) {
    prev.classList.remove("visible");
    prev.addEventListener("transitionend", () => prev.remove(), { once: true });
  }

  currentLayer = next;
  currentId    = asset.id;
  idle.style.display = "none";
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

  if (!asset || !asset.type) {
    // No enabled assets
    if (currentLayer) {
      const dying = currentLayer;
      currentLayer = null;
      dying.classList.remove("visible");
      dying.addEventListener("transitionend", () => dying.remove(), { once: true });
    }
    currentId = null;
    idleMsg.textContent = "No content enabled in playlist.";
    idle.style.display = "flex";
    pollTimer = setTimeout(poll, 5000);
    return;
  }

  if (asset.id !== currentId || forceNext) {
    forceNext = false;
    showAsset(asset);
  }

  // Schedule next poll slightly before the server will advance (min 1 s, max 5 s so
  // disabled assets are detected quickly even during long videos)
  const delay = Math.min(5000, Math.max(1000, (asset.remaining - 0.3) * 1000));
  pollTimer = setTimeout(poll, delay);
}

// Kick off
poll();
