#!/usr/bin/env bash
# Pi Signage — self-contained installer
# Writes all application files and installs/configures the system.
# Run with: sudo bash install.sh
# Target: Raspberry Pi OS Bookworm (Debian 12)

set -euo pipefail

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
APP_DIR="/opt/pi-signage"
SERVICE_FLASK="pi-signage.service"
SERVICE_KIOSK="pi-signage-kiosk.service"
SERVICE_UPDATE="pi-signage-update.service"
TIMER_UPDATE="pi-signage-update.timer"
GITHUB_RAW="https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main"

INSTALL_USER="${SUDO_USER:-}"
if [ -z "$INSTALL_USER" ] || [ "$INSTALL_USER" = "root" ]; then
    INSTALL_USER=$(logname 2>/dev/null || true)
fi
if [ -z "$INSTALL_USER" ] || [ "$INSTALL_USER" = "root" ]; then
    INSTALL_USER=$(awk -F: '$3 >= 1000 && $3 < 65534 { print $1; exit }' /etc/passwd)
fi
if ! id -u "$INSTALL_USER" &>/dev/null; then
    echo "ERROR: Could not detect a non-root user. Run via sudo or set SUDO_USER." >&2
    exit 1
fi

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run this script with sudo: sudo bash install.sh" >&2
    exit 1
fi

echo "==> Installing Pi Signage (user: $INSTALL_USER)"

# ---------------------------------------------------------------------------
# Install system packages
# ---------------------------------------------------------------------------
echo "==> Updating package lists…"
apt-get update -qq

echo "==> Installing dependencies…"
apt-get install -y \
    python3 \
    python3-pip \
    python3-flask \
    chromium \
    xdotool \
    unclutter \
    ffmpeg

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
echo "==> Creating application directory at $APP_DIR…"
mkdir -p "$APP_DIR/static/uploads"
mkdir -p "$APP_DIR/static/css"
mkdir -p "$APP_DIR/static/js"
mkdir -p "$APP_DIR/templates"

# ---------------------------------------------------------------------------
# Write app.py
# ---------------------------------------------------------------------------
cat > "$APP_DIR/app.py" <<'PYEOF'
import os
import time
import json
import sqlite3
import subprocess
from flask import Flask, request, jsonify, render_template, send_from_directory
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
DB_PATH = os.path.join(BASE_DIR, "signage.db")

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
    "mp4", "webm", "mov", "avi"
}

VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "avi"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB

# Server-side playlist state (single display device assumption)
_state = {"index": 0, "started_at": time.time()}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS assets (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                name     TEXT    NOT NULL,
                type     TEXT    NOT NULL CHECK(type IN ('image','video','url')),
                src      TEXT    NOT NULL,
                duration INTEGER NOT NULL DEFAULT 10,
                enabled  INTEGER NOT NULL DEFAULT 1,
                position INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('bg_color', '#000000')"
        )
        conn.commit()


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def next_position(conn):
    row = conn.execute("SELECT COALESCE(MAX(position), -1) FROM assets").fetchone()
    return row[0] + 1


def probe_video_duration(filepath):
    """Return video duration in whole seconds via ffprobe, or None if unavailable."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, timeout=15,
        )
        data = json.loads(result.stdout)
        return max(1, round(float(data["format"]["duration"])))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Routes - pages
# ---------------------------------------------------------------------------

@app.route("/")
def player():
    return render_template("index.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


# ---------------------------------------------------------------------------
# API - playlist CRUD
# ---------------------------------------------------------------------------

@app.route("/api/playlist")
def api_playlist():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM assets ORDER BY position ASC, id ASC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    f = request.files["file"]
    if not f.filename:
        return jsonify({"error": "Empty filename"}), 400
    if not allowed_file(f.filename):
        return jsonify({"error": "File type not allowed"}), 400

    filename = secure_filename(f.filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    dest = os.path.join(UPLOAD_FOLDER, filename)
    while os.path.exists(dest):
        filename = f"{base}_{counter}{ext}"
        dest = os.path.join(UPLOAD_FOLDER, filename)
        counter += 1

    f.save(dest)

    asset_type = "video" if ext.lstrip(".").lower() in VIDEO_EXTENSIONS else "image"
    src = f"/static/uploads/{filename}"
    name = request.form.get("name", "").strip() or base

    if asset_type == "video":
        duration = probe_video_duration(dest) or int(request.form.get("duration", 10))
    else:
        duration = int(request.form.get("duration", 10))

    with get_db() as conn:
        pos = next_position(conn)
        cur = conn.execute(
            "INSERT INTO assets (name, type, src, duration, enabled, position) VALUES (?,?,?,?,1,?)",
            (name, asset_type, src, duration, pos),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (cur.lastrowid,)).fetchone()

    return jsonify(dict(asset)), 201


@app.route("/api/url", methods=["POST"])
def api_add_url():
    data = request.get_json(force=True) or {}
    src = data.get("src", "").strip()
    if not src:
        return jsonify({"error": "URL required"}), 400

    name = data.get("name", "").strip() or src
    duration = int(data.get("duration", 30))

    with get_db() as conn:
        pos = next_position(conn)
        cur = conn.execute(
            "INSERT INTO assets (name, type, src, duration, enabled, position) VALUES (?,?,?,?,1,?)",
            (name, "url", src, duration, pos),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (cur.lastrowid,)).fetchone()

    return jsonify(dict(asset)), 201


@app.route("/api/asset/<int:asset_id>", methods=["PATCH"])
def api_update_asset(asset_id):
    data = request.get_json(force=True) or {}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        name     = data.get("name",     row["name"])
        duration = int(data.get("duration", row["duration"]))
        enabled  = int(data.get("enabled",  row["enabled"]))

        conn.execute(
            "UPDATE assets SET name=?, duration=?, enabled=? WHERE id=?",
            (name, duration, enabled, asset_id),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    return jsonify(dict(asset))


@app.route("/api/asset/<int:asset_id>", methods=["DELETE"])
def api_delete_asset(asset_id):
    with get_db() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        if row["type"] in ("image", "video") and row["src"].startswith("/static/uploads/"):
            filepath = os.path.join(BASE_DIR, row["src"].lstrip("/"))
            if os.path.exists(filepath):
                os.remove(filepath)

        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        conn.commit()

    return jsonify({"success": True})


@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    items = request.get_json(force=True) or []
    with get_db() as conn:
        for item in items:
            conn.execute(
                "UPDATE assets SET position=? WHERE id=?",
                (item["position"], item["id"]),
            )
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API - settings
# ---------------------------------------------------------------------------

@app.route("/api/settings")
def api_get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/settings", methods=["POST"])
def api_save_settings():
    data = request.get_json(force=True) or {}
    allowed_keys = {"bg_color"}
    with get_db() as conn:
        for key, value in data.items():
            if key in allowed_keys:
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, str(value)),
                )
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API - version
# ---------------------------------------------------------------------------

GITHUB_RAW = "https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main"


@app.route("/api/version")
def api_version():
    import urllib.request
    version_file = os.path.join(BASE_DIR, "VERSION")
    try:
        with open(version_file) as f:
            installed = f.read().strip()
    except FileNotFoundError:
        installed = "unknown"

    latest = None
    try:
        with urllib.request.urlopen(f"{GITHUB_RAW}/VERSION", timeout=5) as resp:
            latest = resp.read().decode().strip()
    except Exception:
        pass

    return jsonify({
        "installed": installed,
        "latest": latest,
        "update_available": bool(latest and latest != installed),
    })


# ---------------------------------------------------------------------------
# API – player
# ---------------------------------------------------------------------------

@app.route("/api/advance", methods=["POST"])
def api_advance():
    """Force the playlist to move to the next asset immediately."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id FROM assets WHERE enabled=1 ORDER BY position ASC, id ASC"
        ).fetchall()
    if rows:
        _state["index"] = (_state["index"] + 1) % len(rows)
        _state["started_at"] = time.time()
    return jsonify({"success": True})


@app.route("/api/next")
def api_next():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM assets WHERE enabled=1 ORDER BY position ASC, id ASC"
        ).fetchall()
        bg_row = conn.execute(
            "SELECT value FROM settings WHERE key='bg_color'"
        ).fetchone()

    assets = [dict(r) for r in rows]
    bg_color = bg_row["value"] if bg_row else "#000000"

    if not assets:
        return jsonify({"bg_color": bg_color})

    now = time.time()

    if _state["index"] >= len(assets):
        _state["index"] = 0
        _state["started_at"] = now

    elapsed = now - _state["started_at"]
    current = assets[_state["index"]]

    if elapsed >= current["duration"]:
        _state["index"] = (_state["index"] + 1) % len(assets)
        _state["started_at"] = now
        elapsed = 0.0
        current = assets[_state["index"]]

    remaining = current["duration"] - elapsed
    return jsonify({**current, "bg_color": bg_color, "remaining": round(remaining, 2)})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(host="0.0.0.0", port=8080, debug=False)
PYEOF

# ---------------------------------------------------------------------------
# Write templates/index.html  (player)
# ---------------------------------------------------------------------------
cat > "$APP_DIR/templates/index.html" <<'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <title>Pi Signage Player</title>
  <link rel="stylesheet" href="/static/css/player.css" />
</head>
<body>

  <div id="stage"></div>

  <div id="idle">
    <svg fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5"
        d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
    </svg>
    <span id="idleMsg">Waiting for content…</span>
  </div>

  <script src="/static/js/player.js"></script>
</body>
</html>
HTMLEOF

# ---------------------------------------------------------------------------
# Write templates/admin.html
# ---------------------------------------------------------------------------
cat > "$APP_DIR/templates/admin.html" <<'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Pi Signage</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="stylesheet" href="/static/css/admin.css" />
</head>
<body class="bg-gray-100 min-h-screen">

  <!-- Header -->
  <header class="bg-indigo-700 text-white shadow-lg">
    <div class="max-w-6xl mx-auto px-4 py-4 flex items-center justify-between">
      <div class="flex items-center gap-3">
        <svg class="w-7 h-7" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
            d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
        </svg>
        <h1 class="text-xl font-bold tracking-tight">Pi Signage</h1>
      </div>
      <div class="flex items-center gap-3">
        <div class="flex items-center gap-2 bg-white/10 px-3 py-1.5 rounded-lg">
          <label for="bgColor" class="text-sm font-medium whitespace-nowrap">Background</label>
          <input type="color" id="bgColor" value="#000000"
                 class="w-7 h-7 rounded cursor-pointer border-0 bg-transparent p-0"
                 title="Player background colour" />
          <button id="saveBgColor"
                  class="text-xs bg-white/20 hover:bg-white/40 transition px-2 py-1 rounded font-medium">
            Save
          </button>
        </div>
        <div class="flex items-center gap-2">
          <span id="versionInfo" class="text-xs text-white/50"></span>
          <span id="updateBadge"
                class="hidden bg-yellow-400 text-yellow-900 text-xs font-semibold px-2 py-1 rounded"
                title="Update available — SSH in and run: curl -fsSL https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main/install.sh | sudo bash">
            Update Available
          </span>
        </div>
        <a href="/" target="_blank"
           class="text-sm bg-white/20 hover:bg-white/30 transition px-3 py-1.5 rounded-lg font-medium">
          Open Player
        </a>
      </div>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 py-8 space-y-8">

    <!-- Add Content -->
    <section class="grid md:grid-cols-2 gap-6">

      <!-- Upload Media -->
      <div class="bg-white rounded-xl shadow p-6">
        <h2 class="text-lg font-semibold text-gray-800 mb-4">Upload Media</h2>

        <div id="dropZone"
             class="border-2 border-dashed border-gray-300 rounded-lg p-6 text-center text-gray-500 cursor-pointer hover:border-indigo-400 transition mb-4">
          <svg class="mx-auto w-10 h-10 mb-2 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
              d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"/>
          </svg>
          <p class="text-sm">Drag &amp; drop files here, or <span class="text-indigo-600 font-medium">click to browse</span></p>
          <p class="text-xs mt-1 text-gray-400">Images (JPG, PNG, GIF, WebP) · Videos (MP4, WebM, MOV)</p>
          <input type="file" id="fileInput" class="hidden" accept="image/*,video/*" multiple />
        </div>

        <form id="uploadForm" class="space-y-3">
          <div>
            <label class="text-sm font-medium text-gray-700">Name <span class="text-gray-400">(optional)</span></label>
            <input id="uploadName" type="text" placeholder="Leave blank to use filename"
                   class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="text-sm font-medium text-gray-700">Duration (seconds)</label>
            <input id="uploadDuration" type="number" value="10" min="1" max="3600"
                   class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <button type="submit"
                  class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 rounded-lg transition text-sm">
            Upload
          </button>
        </form>

        <div id="uploadProgress" class="hidden mt-3">
          <div class="text-xs text-gray-500 mb-1" id="uploadProgressLabel">Uploading…</div>
          <div class="h-2 bg-gray-200 rounded-full overflow-hidden">
            <div id="uploadProgressBar" class="h-2 bg-indigo-500 rounded-full transition-all" style="width:0%"></div>
          </div>
        </div>
      </div>

      <!-- Add URL -->
      <div class="bg-white rounded-xl shadow p-6">
        <h2 class="text-lg font-semibold text-gray-800 mb-4">Add Web URL</h2>
        <form id="urlForm" class="space-y-3">
          <div>
            <label class="text-sm font-medium text-gray-700">URL</label>
            <input id="urlSrc" type="url" placeholder="https://example.com"
                   class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" required />
          </div>
          <div>
            <label class="text-sm font-medium text-gray-700">Name</label>
            <input id="urlName" type="text" placeholder="My Website"
                   class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <div>
            <label class="text-sm font-medium text-gray-700">Duration (seconds)</label>
            <input id="urlDuration" type="number" value="30" min="1" max="3600"
                   class="mt-1 w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400" />
          </div>
          <button type="submit"
                  class="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-medium py-2 rounded-lg transition text-sm">
            Add URL
          </button>
        </form>
        <p class="mt-3 text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Note: sites like Google, YouTube, and most major platforms block iframe embedding.
          Use URLs from local apps, custom dashboards, or sites you control.
        </p>
      </div>
    </section>

    <!-- Playlist -->
    <section class="bg-white rounded-xl shadow">
      <div class="px-6 py-4 border-b border-gray-100 flex items-center justify-between">
        <h2 class="text-lg font-semibold text-gray-800">Playlist</h2>
        <span id="itemCount" class="text-sm text-gray-500">0 items</span>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full text-sm">
          <thead>
            <tr class="text-xs text-gray-500 uppercase tracking-wider border-b border-gray-100">
              <th class="px-4 py-3 w-8"></th>
              <th class="px-4 py-3 text-left">Name</th>
              <th class="px-4 py-3 text-left">Type</th>
              <th class="px-4 py-3 text-left w-36">Duration (s)</th>
              <th class="px-4 py-3 text-center">Enabled</th>
              <th class="px-4 py-3 text-center">Actions</th>
            </tr>
          </thead>
          <tbody id="playlistBody">
            <tr id="emptyRow">
              <td colspan="6" class="text-center text-gray-400 py-12">
                No items yet — upload media or add a URL above.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </main>

  <!-- Toast -->
  <div id="toast" class="fixed bottom-5 right-5 bg-gray-800 text-white text-sm px-4 py-2.5 rounded-lg shadow-lg
                          opacity-0 transition-opacity duration-300 pointer-events-none z-50"></div>

  <script src="/static/js/admin.js"></script>
</body>
</html>
HTMLEOF

# ---------------------------------------------------------------------------
# Write static/css/admin.css
# ---------------------------------------------------------------------------
cat > "$APP_DIR/static/css/admin.css" <<'CSSEOF'
.drag-over { outline: 2px dashed #6366f1; background: #eef2ff; }
.dragging  { opacity: 0.4; }
.drag-handle { cursor: grab; }
.drag-handle:active { cursor: grabbing; }
.drop-zone-active { border-color: #6366f1 !important; background: #eef2ff; }
CSSEOF

# ---------------------------------------------------------------------------
# Write static/css/player.css
# ---------------------------------------------------------------------------
cat > "$APP_DIR/static/css/player.css" <<'CSSEOF'
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html, body {
  width: 100%; height: 100%;
  background: #000;
  overflow: hidden;
}

#stage {
  position: fixed;
  inset: 0;
}

.layer {
  position: absolute;
  inset: 0;
  opacity: 0;
  transition: opacity 0.6s ease;
}
.layer.visible { opacity: 1; }

.layer img,
.layer video,
.layer iframe {
  width: 100%;
  height: 100%;
  border: none;
  display: block;
}

.layer img   { object-fit: contain; }
.layer video { object-fit: contain; background: #000; }

#idle {
  position: fixed;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  color: #555;
  font-family: system-ui, sans-serif;
  font-size: 1.1rem;
  gap: 0.75rem;
  background: #111;
}
#idle svg { width: 48px; height: 48px; opacity: 0.4; }
CSSEOF

# ---------------------------------------------------------------------------
# Write static/js/admin.js
# ---------------------------------------------------------------------------
cat > "$APP_DIR/static/js/admin.js" <<'JSEOF'
const $ = id => document.getElementById(id);
let pendingFiles = [];

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

let playlist = [];

async function loadPlaylist() {
  const data = await fetch("/api/playlist").then(r => r.json());
  playlist = data;
  renderPlaylist();
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
      <td class="px-4 py-3 text-center">
        <button class="delete-btn text-gray-400 hover:text-red-500 transition" data-id="${asset.id}">
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

function escHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

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
  await fetch(`/api/asset/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

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
      xhr.onload = () => (xhr.status < 300 ? resolve() : reject());
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
JSEOF

# ---------------------------------------------------------------------------
# Write static/js/player.js
# ---------------------------------------------------------------------------
cat > "$APP_DIR/static/js/player.js" <<'JSEOF'
const stage   = document.getElementById("stage");
const idle    = document.getElementById("idle");
const idleMsg = document.getElementById("idleMsg");

let currentId    = null;
let currentLayer = null;
let pollTimer    = null;
let forceNext    = false;

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
    vid.muted       = true;
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
      try { void frame.contentDocument.title; } catch (_) {}
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

function showAsset(asset) {
  const prev = currentLayer;

  const next = buildLayer(asset);
  stage.appendChild(next);

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

async function poll() {
  clearTimeout(pollTimer);

  let asset = null;
  try {
    const res = await fetch("/api/next", { cache: "no-store" });
    asset = await res.json();
  } catch (_) {
    pollTimer = setTimeout(poll, 5000);
    return;
  }

  if (asset && asset.bg_color) {
    document.body.style.background = asset.bg_color;
    stage.style.background = asset.bg_color;
  }

  if (!asset || !asset.type) {
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

poll();
JSEOF

# ---------------------------------------------------------------------------
# Write VERSION and save install config
# ---------------------------------------------------------------------------
echo "1.0" > "$APP_DIR/VERSION"
echo "INSTALL_USER=$INSTALL_USER" > "$APP_DIR/install.conf"

# ---------------------------------------------------------------------------
# Write auto-update script
# ---------------------------------------------------------------------------
cat > "$APP_DIR/update.sh" <<UPDATEEOF
#!/bin/bash
# Pi Signage auto-updater — must run as root
GITHUB_RAW="$GITHUB_RAW"
INSTALLED=\$(cat /opt/pi-signage/VERSION 2>/dev/null || echo "0")
LATEST=\$(curl -fsSL --max-time 10 "\${GITHUB_RAW}/VERSION" 2>/dev/null || echo "")

if [ -z "\$LATEST" ] || [ "\$LATEST" = "\$INSTALLED" ]; then
    echo "Pi Signage is up to date (v\${INSTALLED})"
    exit 0
fi

echo "Updating Pi Signage: v\${INSTALLED} -> v\${LATEST}"
if [ -f /opt/pi-signage/install.conf ]; then
    source /opt/pi-signage/install.conf
    export SUDO_USER="\$INSTALL_USER"
fi
curl -fsSL "\${GITHUB_RAW}/install.sh" | bash
UPDATEEOF
chmod 750 "$APP_DIR/update.sh"

# ---------------------------------------------------------------------------
# Set permissions
# ---------------------------------------------------------------------------
chown -R "$INSTALL_USER:$INSTALL_USER" "$APP_DIR"
chmod -R 755 "$APP_DIR"

# ---------------------------------------------------------------------------
# Install systemd services
# ---------------------------------------------------------------------------
echo "==> Installing systemd services…"

cat > "/etc/systemd/system/$SERVICE_FLASK" <<SVCEOF
[Unit]
Description=Pi Signage Flask Server
After=network.target

[Service]
Type=simple
User=$INSTALL_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/python3 $APP_DIR/app.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
SVCEOF

cat > "/etc/systemd/system/$SERVICE_KIOSK" <<SVCEOF
[Unit]
Description=Pi Signage Chromium Kiosk
After=graphical.target $SERVICE_FLASK
Wants=graphical.target
Requires=$SERVICE_FLASK

[Service]
Type=simple
User=$INSTALL_USER
Environment=DISPLAY=:0
Environment=XAUTHORITY=/home/$INSTALL_USER/.Xauthority
ExecStartPre=/bin/sleep 5
ExecStart=/bin/sh -c 'unclutter -idle 1 -root & exec /usr/bin/chromium \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --disable-session-crashed-bubble \
    --disable-translate \
    --check-for-update-interval=31536000 \
    --autoplay-policy=no-user-gesture-required \
    http://localhost:8080/'
Restart=on-failure
RestartSec=10

[Install]
WantedBy=graphical.target
SVCEOF

chmod 644 "/etc/systemd/system/$SERVICE_FLASK"
chmod 644 "/etc/systemd/system/$SERVICE_KIOSK"

# ---------------------------------------------------------------------------
# Install update systemd service + nightly timer
# ---------------------------------------------------------------------------
cat > "/etc/systemd/system/$SERVICE_UPDATE" <<SVCEOF
[Unit]
Description=Pi Signage Auto-Updater
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/pi-signage/update.sh
StandardOutput=journal
StandardError=journal
SVCEOF

cat > "/etc/systemd/system/$TIMER_UPDATE" <<SVCEOF
[Unit]
Description=Pi Signage Daily Update Check

[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true

[Install]
WantedBy=timers.target
SVCEOF

chmod 644 "/etc/systemd/system/$SERVICE_UPDATE"
chmod 644 "/etc/systemd/system/$TIMER_UPDATE"

# ---------------------------------------------------------------------------
# Enable and start services
# ---------------------------------------------------------------------------
echo "==> Enabling services…"
systemctl daemon-reload
systemctl enable "$SERVICE_FLASK"
systemctl enable "$SERVICE_KIOSK"
systemctl enable "$TIMER_UPDATE"
systemctl start "$TIMER_UPDATE"

echo "==> Starting Flask service…"
systemctl restart "$SERVICE_FLASK"

echo "==> Flask service status:"
systemctl status "$SERVICE_FLASK" --no-pager -l || true

echo ""
echo "====================================================="
echo " Pi Signage installed successfully!"
echo "====================================================="
echo " Player     : http://$(hostname -I | awk '{print $1}'):8080/"
echo " Admin UI   : http://$(hostname -I | awk '{print $1}'):8080/admin"
echo ""
echo " The Chromium kiosk will launch automatically on"
echo " next graphical login / reboot."
echo ""
echo " To start kiosk now (from the desktop session):"
echo "   sudo systemctl start pi-signage-kiosk.service"
echo ""
echo " Auto-updates: nightly via pi-signage-update.timer"
echo " Update now  : sudo bash /opt/pi-signage/update.sh"
echo ""
echo " Logs:"
echo "   journalctl -u pi-signage -f"
echo "   journalctl -u pi-signage-kiosk -f"
echo "====================================================="
