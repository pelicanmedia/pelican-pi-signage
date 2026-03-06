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
# Routes – pages
# ---------------------------------------------------------------------------

@app.route("/")
def player():
    return render_template("index.html")


@app.route("/admin")
def admin():
    return render_template("admin.html")


# ---------------------------------------------------------------------------
# API – playlist CRUD
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

        # Remove uploaded file from disk
        if row["type"] in ("image", "video") and row["src"].startswith("/static/uploads/"):
            filepath = os.path.join(BASE_DIR, row["src"].lstrip("/"))
            if os.path.exists(filepath):
                os.remove(filepath)

        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        conn.commit()

    return jsonify({"success": True})


@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    items = request.get_json(force=True) or []  # [{id, position}, ...]
    with get_db() as conn:
        for item in items:
            conn.execute(
                "UPDATE assets SET position=? WHERE id=?",
                (item["position"], item["id"]),
            )
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API – settings
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
# API – version
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

    # Guard against out-of-range index after playlist edits
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
