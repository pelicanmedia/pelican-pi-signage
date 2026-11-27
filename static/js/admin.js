// -------------------------------------------------------------------------
// Helpers
// -------------------------------------------------------------------------
const $ = id => document.getElementById(id);
let pendingFiles = [];

// If the session expires, any API call returns 401 — send the user to log in.
function redirectToLogin() {
  window.location.href = "/login?next=" + encodeURIComponent(window.location.pathname);
}
const _fetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const res = await _fetch(...args);
  if (res.status === 401) redirectToLogin();
  return res;
};

function toast(msg, isError = false) {
  const el = $("toast");
  el.textContent = msg;
  el.classList.toggle("bg-red-600", isError);
  el.classList.toggle("bg-gray-800", !isError);
  el.classList.add("opacity-100");
  setTimeout(() => el.classList.remove("opacity-100"), 2500);
}

function typeBadge(type) {
  const map = {
    image: "bg-blue-100 text-blue-700",
    video: "bg-purple-100 text-purple-700",
    url:   "bg-green-100 text-green-700",
  };
  return `<span class="px-2 py-0.5 rounded-full text-xs font-medium ${map[type] || "bg-gray-100 text-gray-600"}">${type}</span>`;
}

// -------------------------------------------------------------------------
// Playlist rendering
// -------------------------------------------------------------------------
let playlist = [];

let processingTimer = null;

async function loadPlaylist() {
  const data = await fetch("/api/playlist").then(r => r.json());
  playlist = data;
  renderPlaylist();

  // While videos are converting, refresh so their status updates
  clearTimeout(processingTimer);
  if (playlist.some(a => a.media_status === "processing")) {
    processingTimer = setTimeout(loadPlaylist, 5000);
  }
}

function renderPlaylist() {
  const tbody = $("playlistBody");
  tbody.innerHTML = "";
  $("itemCount").textContent = `${playlist.length} item${playlist.length !== 1 ? "s" : ""}`;

  if (playlist.length === 0) {
    tbody.innerHTML = `<tr id="emptyRow"><td colspan="6" class="text-center text-gray-400 py-12">
      No items yet — upload media or add a URL above.</td></tr>`;
    return;
  }

  playlist.forEach((asset) => {
    const tr = document.createElement("tr");
    tr.className = "border-b border-gray-50 hover:bg-gray-50 transition";
    tr.dataset.id = asset.id;
    tr.draggable = true;

    tr.innerHTML = `
      <td class="px-3 py-3 drag-handle text-gray-300 select-none">
        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 24 24">
          <path d="M9 5a1 1 0 110 2 1 1 0 010-2zm6 0a1 1 0 110 2 1 1 0 010-2zM9 11a1 1 0 110 2 1 1 0 010-2zm6 0a1 1 0 110 2 1 1 0 010-2zM9 17a1 1 0 110 2 1 1 0 010-2zm6 0a1 1 0 110 2 1 1 0 010-2z"/>
        </svg>
      </td>
      <td class="px-4 py-3">
        <div class="font-medium text-gray-800 truncate max-w-xs" title="${escHtml(asset.src)}">
          ${escHtml(asset.name)}
        </div>
        <div class="text-xs text-gray-400 truncate max-w-xs">${escHtml(asset.src)}</div>
        ${scheduleLine(asset)}
        ${mediaLine(asset)}
      </td>
      <td class="px-4 py-3">${typeBadge(asset.type)}</td>
      <td class="px-4 py-3">
        ${asset.type === 'video' || (asset.type === 'url' && /\.(mp4|webm|mov|avi|m3u8)(\?.*)?$/i.test(asset.src))
          ? `<span class="text-xs text-gray-400 italic">${asset.duration}s (auto)</span>`
          : `<input type="number" value="${asset.duration}" min="1" max="3600" data-id="${asset.id}"
               class="duration-input w-20 border border-gray-200 rounded px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-300" />`
        }
      </td>
      <td class="px-4 py-3 text-center">
        <button class="toggle-btn relative inline-flex h-6 w-11 items-center rounded-full transition
                       ${asset.enabled ? "bg-indigo-500" : "bg-gray-200"}"
                data-id="${asset.id}" data-enabled="${asset.enabled}">
          <span class="inline-block h-4 w-4 transform rounded-full bg-white shadow transition
                       ${asset.enabled ? "translate-x-6" : "translate-x-1"}"></span>
        </button>
      </td>
      <td class="px-4 py-3 text-center whitespace-nowrap">
        <button class="schedule-btn text-gray-400 hover:text-indigo-600 transition mr-2" data-id="${asset.id}"
                title="Schedule" aria-label="Schedule ${escHtml(asset.name)}">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"/>
          </svg>
        </button>
        <button class="delete-btn text-gray-400 hover:text-red-500 transition" data-id="${asset.id}"
                title="Delete" aria-label="Delete ${escHtml(asset.name)}">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7h6m-7 0V5a1 1 0 011-1h4a1 1 0 011 1v2m-7 0h10"/>
          </svg>
        </button>
      </td>
    `;

    tbody.appendChild(tr);
  });

  attachRowListeners();
  attachDragDrop();
}

function mediaLine(asset) {
  if (asset.media_status === "processing") {
    return `<div class="text-xs mt-1"><span class="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-sky-100 text-sky-700 font-medium">
      <svg class="w-3 h-3 animate-spin" fill="none" viewBox="0 0 24 24"><circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="3" opacity=".25"/><path d="M22 12a10 10 0 00-10-10" stroke="currentColor" stroke-width="3"/></svg>
      Converting for smooth playback… (not shown until done)</span></div>`;
  }
  if (asset.media_status === "failed") {
    return `<div class="text-xs mt-1"><span class="px-1.5 py-0.5 rounded bg-red-100 text-red-700 font-medium"
      title="${escHtml(asset.media_error || "")}">Conversion failed — this video won't play. Try re-exporting it as H.264 MP4.</span></div>`;
  }
  return "";
}

function scheduleLine(asset) {
  const summary = scheduleSummary(asset);
  if (!summary) return "";
  const notNow = asset.enabled && !asset.active_now
    ? `<span class="ml-1.5 px-1.5 py-0.5 rounded bg-amber-100 text-amber-700 font-medium">Not showing now</span>`
    : "";
  return `<div class="text-xs text-indigo-600 mt-0.5 flex items-center flex-wrap gap-y-1">
    <svg class="w-3.5 h-3.5 mr-1 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
    </svg>${escHtml(summary)}${notNow}</div>`;
}

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// -------------------------------------------------------------------------
// Row event listeners
// -------------------------------------------------------------------------
function attachRowListeners() {
  document.querySelectorAll(".duration-input").forEach(input => {
    input.addEventListener("change", async () => {
      const id = input.dataset.id;
      const duration = parseInt(input.value, 10);
      if (!duration || duration < 1) return;
      await patchAsset(id, { duration });
      toast("Duration updated");
    });
  });

  document.querySelectorAll(".toggle-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      const id = btn.dataset.id;
      const newEnabled = btn.dataset.enabled === "1" ? 0 : 1;
      await patchAsset(id, { enabled: newEnabled });
      await loadPlaylist();
      toast(newEnabled ? "Enabled" : "Disabled");
    });
  });

  document.querySelectorAll(".schedule-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const asset = playlist.find(a => a.id === +btn.dataset.id);
      if (!asset) return;
      openScheduleEditor(asset, async fields => {
        await patchAsset(asset.id, fields);
        await loadPlaylist();
        toast("Schedule saved");
      });
    });
  });

  document.querySelectorAll(".delete-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Delete this item?")) return;
      await fetch(`/api/asset/${btn.dataset.id}`, { method: "DELETE" });
      await loadPlaylist();
      toast("Deleted");
    });
  });

}

async function patchAsset(id, data) {
  const res = await fetch(`/api/asset/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.error || "Save failed");
  }
  return res.json();
}

// -------------------------------------------------------------------------
// Drag-to-reorder
// -------------------------------------------------------------------------
let dragSrc = null;

function attachDragDrop() {
  const rows = document.querySelectorAll("#playlistBody tr[draggable]");

  rows.forEach(row => {
    row.addEventListener("dragstart", e => {
      dragSrc = row;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
    });

    row.addEventListener("dragend", () => {
      row.classList.remove("dragging");
      document.querySelectorAll("#playlistBody tr").forEach(r => r.classList.remove("drag-over"));
    });

    row.addEventListener("dragover", e => {
      e.preventDefault();
      e.dataTransfer.dropEffect = "move";
      document.querySelectorAll("#playlistBody tr").forEach(r => r.classList.remove("drag-over"));
      if (row !== dragSrc) row.classList.add("drag-over");
    });

    row.addEventListener("drop", async e => {
      e.preventDefault();
      if (!dragSrc || dragSrc === row) return;

      const tbody = $("playlistBody");
      const rows  = [...tbody.querySelectorAll("tr[draggable]")];
      const srcIdx = rows.indexOf(dragSrc);
      const tgtIdx = rows.indexOf(row);

      if (srcIdx < tgtIdx) {
        tbody.insertBefore(dragSrc, row.nextSibling);
      } else {
        tbody.insertBefore(dragSrc, row);
      }

      await saveOrder();
    });
  });
}

async function saveOrder() {
  const rows = [...document.querySelectorAll("#playlistBody tr[draggable]")];
  const payload = rows.map((r, i) => ({ id: parseInt(r.dataset.id), position: i }));
  await fetch("/api/reorder", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  toast("Order saved");
  await loadPlaylist();
}

// -------------------------------------------------------------------------
// Upload
// -------------------------------------------------------------------------
const dropZone  = $("dropZone");
const fileInput = $("fileInput");

dropZone.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", () => {
  pendingFiles = [...fileInput.files];
  if (pendingFiles.length) updateDropZoneLabel();
});

dropZone.addEventListener("dragover", e => {
  e.preventDefault();
  dropZone.classList.add("drop-zone-active");
});
dropZone.addEventListener("dragleave", () => dropZone.classList.remove("drop-zone-active"));
dropZone.addEventListener("drop", e => {
  e.preventDefault();
  dropZone.classList.remove("drop-zone-active");
  pendingFiles = [...e.dataTransfer.files];
  updateDropZoneLabel();
});

function updateDropZoneLabel() {
  const p = dropZone.querySelector("p");
  p.textContent = pendingFiles.map(f => f.name).join(", ");
}

$("uploadForm").addEventListener("submit", async e => {
  e.preventDefault();
  if (!pendingFiles.length) { toast("Select a file first", true); return; }

  const name     = $("uploadName").value.trim();
  const duration = $("uploadDuration").value;

  const progress = $("uploadProgress");
  const bar      = $("uploadProgressBar");
  const label    = $("uploadProgressLabel");
  progress.classList.remove("hidden");

  for (let i = 0; i < pendingFiles.length; i++) {
    const file = pendingFiles[i];
    label.textContent = `Uploading ${file.name} (${i + 1}/${pendingFiles.length})…`;
    bar.style.width = "0%";

    const fd = new FormData();
    fd.append("file", file);
    fd.append("name", name || "");
    fd.append("duration", duration);

    await new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.upload.onprogress = ev => {
        if (ev.lengthComputable) bar.style.width = `${(ev.loaded / ev.total * 100).toFixed(0)}%`;
      };
      xhr.onload = () => {
        if (xhr.status === 401) redirectToLogin();
        xhr.status < 300 ? resolve() : reject();
      };
      xhr.onerror = reject;
      xhr.open("POST", "/api/upload");
      xhr.send(fd);
    });
  }

  progress.classList.add("hidden");
  $("uploadForm").reset();
  pendingFiles = [];
  dropZone.querySelector("p").textContent =
    "Drag & drop files here, or click to browse";
  toast("Uploaded file(s) successfully");
  await loadPlaylist();
});

// -------------------------------------------------------------------------
// Add URL
// -------------------------------------------------------------------------
$("urlForm").addEventListener("submit", async e => {
  e.preventDefault();
  const src      = $("urlSrc").value.trim();
  const name     = $("urlName").value.trim();
  const duration = parseInt($("urlDuration").value, 10);

  const res = await fetch("/api/url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ src, name, duration }),
  });
  if (!res.ok) { toast("Failed to add URL", true); return; }
  $("urlForm").reset();
  toast("URL added");
  await loadPlaylist();
});

// -------------------------------------------------------------------------
// Background colour setting
// -------------------------------------------------------------------------
async function loadSettings() {
  const s = await fetch("/api/settings").then(r => r.json());
  if (s.bg_color) $("bgColor").value = s.bg_color;
}

$("saveBgColor").addEventListener("click", async () => {
  const color = $("bgColor").value;
  await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ bg_color: color }),
  });
  toast("Background colour saved");
});

// -------------------------------------------------------------------------
// Init
// -------------------------------------------------------------------------

async function checkVersion() {
  try {
    const v = await fetch("/api/version").then(r => r.json());
    const info  = document.getElementById("versionInfo");
    const badge = document.getElementById("updateBadge");
    if (info && v.installed !== "unknown") info.textContent = `v${v.installed}`;
    if (badge && v.update_available) badge.classList.remove("hidden");
  } catch (_) {}
}

loadPlaylist();
loadSettings();
checkVersion();
