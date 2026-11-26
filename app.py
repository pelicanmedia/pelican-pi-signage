import os
import time
import datetime
import json
import sqlite3
import secrets
import subprocess
from functools import wraps
from flask import (Flask, request, jsonify, render_template, redirect,
                   url_for, session)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

from schedule import SCHEDULE_FIELDS, ScheduleError, is_active, normalize_schedule

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
DB_PATH = os.path.join(BASE_DIR, "signage.db")
ENV_FILE = os.path.join(BASE_DIR, ".env")


def read_env():
    """Parse KEY=VALUE pairs from .env, ignoring comments and blank lines."""
    cfg = {}
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    cfg[key.strip()] = val.strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    return cfg

ALLOWED_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "webp", "bmp",
    "mp4", "webm", "mov", "avi"
}

VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "avi"}

app = Flask(__name__)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024  # 512 MB
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_NAME"] = "pi_signage_session"

MIN_PASSWORD_LENGTH = 8

# Server-side playlist state (single display device assumption).
# The current item is tracked by id (not list index) so that scheduled items
# appearing or disappearing don't make the display jump.
_state = {"current_id": None, "sort_key": None, "started_at": time.time()}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _column_exists(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == column for r in rows)


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

        # Migration: add updated_at and sync_uuid to assets
        if not _column_exists(conn, "assets", "updated_at"):
            conn.execute("ALTER TABLE assets ADD COLUMN updated_at TEXT")
            conn.execute(
                "UPDATE assets SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                " WHERE updated_at IS NULL"
            )
        if not _column_exists(conn, "assets", "sync_uuid"):
            conn.execute("ALTER TABLE assets ADD COLUMN sync_uuid TEXT")

        # Migration: per-asset scheduling (v1.3.0)
        for col in SCHEDULE_FIELDS:
            if not _column_exists(conn, "assets", col):
                conn.execute(f"ALTER TABLE assets ADD COLUMN {col} TEXT")

        # Migration: add updated_at to settings
        if not _column_exists(conn, "settings", "updated_at"):
            conn.execute("ALTER TABLE settings ADD COLUMN updated_at TEXT")
            conn.execute(
                "UPDATE settings SET updated_at = strftime('%Y-%m-%dT%H:%M:%fZ','now')"
                " WHERE updated_at IS NULL"
            )

        # Sync configuration table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Admin authentication
        conn.execute("""
            CREATE TABLE IF NOT EXISTS auth (
                id            INTEGER PRIMARY KEY CHECK (id = 1),
                password_hash TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_config (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # Stable secret key for Flask sessions (survives restarts)
        row = conn.execute("SELECT value FROM app_config WHERE key='secret_key'").fetchone()
        if row:
            app.secret_key = row["value"]
        else:
            app.secret_key = secrets.token_hex(32)
            conn.execute(
                "INSERT INTO app_config (key, value) VALUES ('secret_key', ?)",
                (app.secret_key,),
            )

        conn.commit()

    # Start sync thread if configured
    from sync import maybe_start_sync_thread
    maybe_start_sync_thread()


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
# Auth helpers
# ---------------------------------------------------------------------------

def is_setup_complete():
    with get_db() as conn:
        row = conn.execute("SELECT id FROM auth WHERE id=1").fetchone()
    return row is not None


def _wants_json():
    return request.path.startswith("/api/")


def login_required(f):
    """Protect admin pages (redirect) and admin APIs (401 JSON)."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not is_setup_complete():
            if _wants_json():
                return jsonify({"error": "Setup required"}), 401
            return redirect(url_for("setup"))
        if not session.get("logged_in"):
            if _wants_json():
                return jsonify({"error": "Login required"}), 401
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)
    return decorated


def _is_local_request():
    return request.remote_addr in ("127.0.0.1", "::1")


def _safe_next(target):
    """Only allow redirects to local paths (avoid open redirects)."""
    if target and target.startswith("/") and not target.startswith("//"):
        return target
    return url_for("admin")


def _set_password(password):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO auth (id, password_hash) VALUES (1, ?)",
            (generate_password_hash(password),),
        )
        conn.commit()


def _check_password(password):
    with get_db() as conn:
        row = conn.execute("SELECT password_hash FROM auth WHERE id=1").fetchone()
    return bool(row) and check_password_hash(row["password_hash"], password)


# ---------------------------------------------------------------------------
# Routes – auth
# ---------------------------------------------------------------------------

@app.route("/setup", methods=["GET", "POST"])
def setup():
    if is_setup_complete():
        return redirect(url_for("admin"))
    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if len(password) < MIN_PASSWORD_LENGTH:
            error = f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
        elif password != confirm:
            error = "Passwords do not match."
        else:
            _set_password(password)
            session.clear()
            session["logged_in"] = True
            return redirect(url_for("admin"))
    return render_template("setup.html", error=error, min_length=MIN_PASSWORD_LENGTH)


@app.route("/login", methods=["GET", "POST"])
def login():
    if not is_setup_complete():
        return redirect(url_for("setup"))
    error = None
    if request.method == "POST":
        if _check_password(request.form.get("password", "")):
            session.clear()
            session["logged_in"] = True
            return redirect(_safe_next(request.args.get("next")))
        time.sleep(1)  # slow down password guessing
        error = "Incorrect password."
    return render_template("login.html", error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    error = message = None
    if request.method == "POST":
        current  = request.form.get("current", "")
        password = request.form.get("password", "")
        confirm  = request.form.get("confirm", "")
        if not _check_password(current):
            error = "Current password is incorrect."
        elif len(password) < MIN_PASSWORD_LENGTH:
            error = f"New password must be at least {MIN_PASSWORD_LENGTH} characters."
        elif password != confirm:
            error = "New passwords do not match."
        else:
            _set_password(password)
            message = "Password updated."
    return render_template("account.html", error=error, message=message,
                           min_length=MIN_PASSWORD_LENGTH)


# ---------------------------------------------------------------------------
# Routes – pages
# ---------------------------------------------------------------------------

@app.route("/")
def player():
    return render_template("index.html")


@app.route("/admin")
@login_required
def admin():
    return render_template("admin.html")


# ---------------------------------------------------------------------------
# API – playlist CRUD
# ---------------------------------------------------------------------------

@app.route("/api/playlist")
@login_required
def api_playlist():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM assets ORDER BY position ASC, id ASC"
        ).fetchall()
    out = []
    for r in rows:
        a = dict(r)
        a["active_now"] = is_active(a)
        out.append(a)
    return jsonify(out)


@app.route("/api/upload", methods=["POST"])
@login_required
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
            "INSERT INTO assets (name, type, src, duration, enabled, position, updated_at)"
            " VALUES (?,?,?,?,1,?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (name, asset_type, src, duration, pos),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (cur.lastrowid,)).fetchone()

    return jsonify(dict(asset)), 201


@app.route("/api/url", methods=["POST"])
@login_required
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
            "INSERT INTO assets (name, type, src, duration, enabled, position, updated_at)"
            " VALUES (?,?,?,?,1,?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
            (name, "url", src, duration, pos),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (cur.lastrowid,)).fetchone()

    return jsonify(dict(asset)), 201


@app.route("/api/asset/<int:asset_id>", methods=["PATCH"])
@login_required
def api_update_asset(asset_id):
    data = request.get_json(force=True) or {}
    with get_db() as conn:
        row = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            return jsonify({"error": "Not found"}), 404

        name     = data.get("name",     row["name"])
        duration = int(data.get("duration", row["duration"]))
        enabled  = int(data.get("enabled",  row["enabled"]))
        try:
            sched = normalize_schedule(data, dict(row))
        except ScheduleError as e:
            return jsonify({"error": str(e)}), 400

        conn.execute(
            "UPDATE assets SET name=?, duration=?, enabled=?,"
            " start_date=?, end_date=?, play_days=?, play_time_from=?, play_time_to=?,"
            " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
            (name, duration, enabled,
             sched["start_date"], sched["end_date"], sched["play_days"],
             sched["play_time_from"], sched["play_time_to"], asset_id),
        )
        conn.commit()
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    return jsonify(dict(asset))


@app.route("/api/asset/<int:asset_id>", methods=["DELETE"])
@login_required
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

        # Record sync_uuid so the next sync tells the controller this asset was deleted
        if row["sync_uuid"]:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS deleted_sync_uuids (
                    sync_uuid  TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                )
            """)
            conn.execute(
                "INSERT OR IGNORE INTO deleted_sync_uuids (sync_uuid, deleted_at)"
                " VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (row["sync_uuid"],),
            )

        conn.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        conn.commit()

    return jsonify({"success": True})


@app.route("/api/reorder", methods=["POST"])
@login_required
def api_reorder():
    items = request.get_json(force=True) or []  # [{id, position}, ...]
    with get_db() as conn:
        for item in items:
            # Bump updated_at so the new order wins last-write-wins sync
            conn.execute(
                "UPDATE assets SET position=?,"
                " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?",
                (item["position"], item["id"]),
            )
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API – settings
# ---------------------------------------------------------------------------

@app.route("/api/settings")
@login_required
def api_get_settings():
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    return jsonify({r["key"]: r["value"] for r in rows})


@app.route("/api/settings", methods=["POST"])
@login_required
def api_save_settings():
    data = request.get_json(force=True) or {}
    allowed_keys = {"bg_color"}
    with get_db() as conn:
        for key, value in data.items():
            if key in allowed_keys:
                conn.execute(
                    "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                    " updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",
                    (key, str(value)),
                )
        conn.commit()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# API – version
# ---------------------------------------------------------------------------

GITHUB_RELEASES_API = "https://api.github.com/repos/pelicanmedia/pelican-pi-signage/releases/latest"
VERSION_CACHE_SECONDS = 3600
_version_cache = {"latest": None, "checked_at": 0.0}


@app.route("/api/version")
@login_required
def api_version():
    import urllib.request
    version_file = os.path.join(BASE_DIR, "VERSION")
    try:
        with open(version_file) as f:
            installed = f.read().strip()
    except FileNotFoundError:
        installed = "unknown"

    # The repo is public, so no token is needed. Results are cached because
    # unauthenticated GitHub API calls are limited to 60/hour per IP address
    # (shared by every Pi behind the same router). An optional GITHUB_TOKEN
    # in .env is still sent if present.
    now = time.time()
    if now - _version_cache["checked_at"] < VERSION_CACHE_SECONDS:
        latest = _version_cache["latest"]
    else:
        latest = None
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "pi-signage-client",
        }
        token = read_env().get("GITHUB_TOKEN", "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            req = urllib.request.Request(GITHUB_RELEASES_API, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            latest = data.get("tag_name", "").lstrip("v") or None
            _version_cache["latest"] = latest
            _version_cache["checked_at"] = now
        except Exception:
            # Keep the last known value; retry on the next request
            latest = _version_cache["latest"]

    return jsonify({
        "installed": installed,
        "latest": latest,
        "update_available": bool(latest and latest != installed),
    })


# ---------------------------------------------------------------------------
# API – player
# ---------------------------------------------------------------------------

def _active_assets():
    """Enabled assets whose schedule allows them to play right now, in order."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM assets WHERE enabled=1 ORDER BY position ASC, id ASC"
        ).fetchall()
    now = datetime.datetime.now()
    return [a for a in (dict(r) for r in rows) if is_active(a, now)]


def _sort_key(asset):
    return (asset["position"], asset["id"])


def _next_after(assets, key):
    """First asset ordered after `key`, wrapping round to the start."""
    if key is not None:
        for a in assets:
            if _sort_key(a) > tuple(key):
                return a
    return assets[0]


def _set_current(asset, now):
    _state["current_id"] = asset["id"] if asset else None
    _state["sort_key"]   = _sort_key(asset) if asset else None
    _state["started_at"] = now


@app.route("/api/advance", methods=["POST"])
def api_advance():
    """Force the playlist to move to the next asset immediately.

    Open to the local kiosk browser; remote callers must be logged in.
    """
    if not (_is_local_request() or session.get("logged_in")):
        return jsonify({"error": "Login required"}), 401
    assets = _active_assets()
    if assets:
        _set_current(_next_after(assets, _state["sort_key"]), time.time())
    return jsonify({"success": True})


@app.route("/api/next")
def api_next():
    assets = _active_assets()
    with get_db() as conn:
        bg_row = conn.execute(
            "SELECT value FROM settings WHERE key='bg_color'"
        ).fetchone()
    bg_color = bg_row["value"] if bg_row else "#000000"

    if not assets:
        with get_db() as conn:
            any_enabled = conn.execute(
                "SELECT 1 FROM assets WHERE enabled=1 LIMIT 1"
            ).fetchone()
        message = ("Nothing is scheduled to show right now." if any_enabled
                   else "No content enabled in playlist.")
        return jsonify({"bg_color": bg_color, "idle_message": message})

    now = time.time()
    current = next((a for a in assets if a["id"] == _state["current_id"]), None)

    if current is None:
        # Current item was removed, disabled or went out of schedule:
        # carry on from where it was in the order.
        current = _next_after(assets, _state["sort_key"]) if _state["sort_key"] else assets[0]
        _set_current(current, now)
    elif now - _state["started_at"] >= current["duration"]:
        current = _next_after(assets, _sort_key(current))
        _set_current(current, now)
    else:
        # Keep the stored order key fresh in case the item was reordered
        _state["sort_key"] = _sort_key(current)

    remaining = current["duration"] - (now - _state["started_at"])
    return jsonify({**current, "bg_color": bg_color, "remaining": round(remaining, 2)})


# ---------------------------------------------------------------------------
# API – sync config (used by admin UI to configure controller connection)
# ---------------------------------------------------------------------------

@app.route("/api/sync/status")
@login_required
def api_sync_status():
    env = read_env()
    enabled = bool(env.get("CONTROLLER_URL") and env.get("CONTROLLER_API_KEY"))
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM sync_config").fetchall()
    cfg = {r["key"]: r["value"] for r in rows}
    return jsonify({
        "enabled":         enabled,
        "connected":       cfg.get("sync_connected", "1") == "1",
        "controller_url":  env.get("CONTROLLER_URL", ""),
        "last_sync_at":    cfg.get("last_sync_at"),
        "last_sync_error": cfg.get("last_sync_error"),
    })


@app.route("/api/sync/reconnect", methods=["POST"])
@login_required
def api_sync_reconnect():
    with get_db() as conn:
        conn.execute(
            "INSERT INTO sync_config (key, value) VALUES ('sync_connected','1')"
            " ON CONFLICT(key) DO UPDATE SET value='1'"
        )
        conn.execute(
            "INSERT INTO sync_config (key, value) VALUES ('last_sync_error','')"
            " ON CONFLICT(key) DO UPDATE SET value=''"
        )
        conn.commit()

    from sync import maybe_start_sync_thread
    maybe_start_sync_thread()
    return jsonify({"success": True})


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(host="0.0.0.0", port=8081, debug=False)
