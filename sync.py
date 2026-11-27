"""
Background sync thread for pelican-pi-signage-client.

Polls the controller every POLL_INTERVAL seconds, performs last-write-wins
merge of assets and settings, and downloads staged asset files.
"""

import os
import json
import sqlite3
import threading
import time
import urllib.request
import urllib.error

from schedule import SCHEDULE_FIELDS

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH   = os.path.join(BASE_DIR, "signage.db")
ENV_FILE  = os.path.join(BASE_DIR, ".env")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")


def _read_env():
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

POLL_INTERVAL = 30  # seconds between syncs

_lock   = threading.Lock()
_thread = None


# ---------------------------------------------------------------------------
# DB helpers (standalone, no Flask context needed)
# ---------------------------------------------------------------------------

def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _read_sync_config():
    """Merge .env credentials with runtime state from DB."""
    with _get_db() as conn:
        rows = conn.execute("SELECT key, value FROM sync_config").fetchall()
    cfg = {r["key"]: r["value"] for r in rows}
    env = _read_env()
    cfg["controller_url"] = env.get("CONTROLLER_URL", "").rstrip("/")
    cfg["api_key"]        = env.get("CONTROLLER_API_KEY", "")
    cfg["sync_enabled"]   = "1" if cfg["controller_url"] and cfg["api_key"] else "0"
    return cfg


def _write_sync_config(conn, key, value):
    conn.execute(
        "INSERT INTO sync_config (key, value) VALUES (?,?)"
        " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


# ---------------------------------------------------------------------------
# Build the outbound sync payload
# ---------------------------------------------------------------------------

def build_sync_request():
    with _get_db() as conn:
        assets = conn.execute(
            "SELECT id, name, type, src, duration, enabled, position,"
            " updated_at, sync_uuid, start_date, end_date, play_days,"
            " play_time_from, play_time_to, options"
            " FROM assets ORDER BY position ASC, id ASC"
        ).fetchall()
        settings = conn.execute(
            "SELECT key, value, updated_at FROM settings"
        ).fetchall()

    asset_list = []
    for a in assets:
        a = dict(a)
        local_only = a["sync_uuid"] is None or a["sync_uuid"].startswith("local:")
        entry = {
            "sync_uuid":      a["sync_uuid"],
            "client_asset_id": a["id"],
            "name":           a["name"],
            "type":           a["type"],
            "src":            a["src"],
            "duration":       a["duration"],
            "enabled":        a["enabled"],
            "position":       a["position"],
            "updated_at":     a["updated_at"] or "",
            "local_only":     local_only,
        }
        for f in SCHEDULE_FIELDS:
            entry[f] = a[f]
        entry["options"] = a["options"]  # JSON text or None
        asset_list.append(entry)

    settings_dict = {}
    for s in settings:
        settings_dict[s["key"]] = {
            "value":      s["value"],
            "updated_at": s["updated_at"] or "",
        }

    version_file = os.path.join(BASE_DIR, "VERSION")
    try:
        with open(version_file) as f:
            version = f.read().strip()
    except FileNotFoundError:
        version = "unknown"

    try:
        import device
        system = device.system_info()
    except Exception:
        system = None

    return {
        "client_version":       version,
        "assets":               asset_list,
        "settings":             settings_dict,
        "deleted_asset_uuids":  [],  # populated below
        "system":               system,
    }


def _get_and_clear_deleted_uuids():
    """
    Returns sync_uuids for assets that were deleted locally since last sync
    (assets with sync_uuid set but no longer in the assets table are tracked
    in a deleted_uuids table created here on first use).
    """
    with _get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deleted_sync_uuids (
                sync_uuid TEXT PRIMARY KEY,
                deleted_at TEXT NOT NULL
            )
        """)
        rows = conn.execute("SELECT sync_uuid FROM deleted_sync_uuids").fetchall()
        uuids = [r["sync_uuid"] for r in rows]
        if uuids:
            conn.execute("DELETE FROM deleted_sync_uuids")
            conn.commit()
    return uuids


# ---------------------------------------------------------------------------
# Apply the controller's response
# ---------------------------------------------------------------------------

def apply_sync_response(response, cfg):
    """
    Apply assets_to_upsert, assets_to_delete, settings from the controller.
    Returns list of file tokens that need to be downloaded.
    """
    assets_to_upsert = response.get("assets_to_upsert", [])
    assets_to_delete = response.get("assets_to_delete", [])
    settings_updates = response.get("settings", {})

    pending_downloads = []  # [(token, original_name, asset_sync_uuid)]

    with _lock:
        with _get_db() as conn:
            # Upsert assets pushed by controller
            for a in assets_to_upsert:
                sync_uuid = a["sync_uuid"]
                token     = a.get("pending_file_token")

                # Determine final src: if file needs downloading, we'll update
                # src after download; use a placeholder for now
                if token:
                    original_name = a.get("original_name") or a.get("name", "")
                    pending_downloads.append((token, original_name, sync_uuid))
                    src = None  # will be set after download
                else:
                    src = a.get("src")

                existing = conn.execute(
                    "SELECT id, updated_at FROM assets WHERE sync_uuid=?", (sync_uuid,)
                ).fetchone()

                if existing:
                    # Only update if controller's updated_at is genuinely newer
                    if a["updated_at"] >= (existing["updated_at"] or ""):
                        updates = {
                            "name":       a["name"],
                            "duration":   a["duration"],
                            "enabled":    a["enabled"],
                            "position":   a["position"],
                            "updated_at": a["updated_at"],
                        }
                        # Controller sends src "" for uploaded files (the file lives
                        # only on the Pi), so never overwrite a real local path with it.
                        if src:
                            updates["src"] = src
                        # Only apply schedule fields the controller actually sent,
                        # so an older controller can't wipe a schedule set here.
                        for f in SCHEDULE_FIELDS:
                            if f in a:
                                updates[f] = a[f]
                        if "options" in a:
                            updates["options"] = a["options"]
                        set_clause = ", ".join(f"{k}=?" for k in updates)
                        conn.execute(
                            f"UPDATE assets SET {set_clause} WHERE sync_uuid=?",
                            (*updates.values(), sync_uuid),
                        )
                else:
                    # New asset from controller. An image/video with no file to
                    # download (an older controller that no longer has it) would
                    # never play, so wait: it's offered again on every sync.
                    if a["type"] in ("image", "video") and not token and not src:
                        continue
                    if src is None:
                        src = ""  # placeholder; updated after download
                    # Find next available position
                    max_pos = conn.execute(
                        "SELECT COALESCE(MAX(position), -1) FROM assets"
                    ).fetchone()[0]
                    conn.execute(
                        "INSERT INTO assets"
                        " (name, type, src, duration, enabled, position, updated_at, sync_uuid,"
                        "  start_date, end_date, play_days, play_time_from, play_time_to, options)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            a["name"], a["type"], src,
                            a["duration"], a["enabled"],
                            a.get("position", max_pos + 1),
                            a["updated_at"], sync_uuid,
                            *(a.get(f) for f in SCHEDULE_FIELDS),
                            a.get("options"),
                        ),
                    )

            # Delete assets marked for removal by controller
            for sync_uuid in assets_to_delete:
                row = conn.execute(
                    "SELECT id, type, src FROM assets WHERE sync_uuid=?", (sync_uuid,)
                ).fetchone()
                if row:
                    if row["type"] in ("image", "video") and row["src"].startswith("/static/uploads/"):
                        filepath = os.path.join(BASE_DIR, row["src"].lstrip("/"))
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except OSError:
                                pass
                    conn.execute("DELETE FROM assets WHERE sync_uuid=?", (sync_uuid,))

            # Apply settings (last-write-wins per key)
            for key, item in settings_updates.items():
                existing = conn.execute(
                    "SELECT value, updated_at FROM settings WHERE key=?", (key,)
                ).fetchone()
                if not existing or item["updated_at"] >= (existing["updated_at"] or ""):
                    conn.execute(
                        "INSERT INTO settings (key, value, updated_at) VALUES (?,?,?)"
                        " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                        " updated_at=excluded.updated_at",
                        (key, item["value"], item["updated_at"]),
                    )

            conn.commit()

    return pending_downloads


# ---------------------------------------------------------------------------
# File download helpers
# ---------------------------------------------------------------------------

def _download_staged_file(controller_url, api_key, token, original_name):
    """Download a staged file from the controller. Returns the saved src path or None."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)

    # Determine a safe, unique destination filename
    from werkzeug.utils import secure_filename
    filename = secure_filename(original_name) or f"{token}.bin"
    base, ext = os.path.splitext(filename)
    dest = os.path.join(UPLOAD_FOLDER, filename)
    counter = 1
    while os.path.exists(dest):
        filename = f"{base}_{counter}{ext}"
        dest = os.path.join(UPLOAD_FOLDER, filename)
        counter += 1

    url = f"{controller_url}/api/stage/{token}"
    req = urllib.request.Request(url, headers={"X-Api-Key": api_key})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
    except Exception as e:
        # Clean up partial download
        if os.path.exists(dest):
            os.remove(dest)
        raise e

    return f"/static/uploads/{filename}", filename


def _update_asset_src(sync_uuid, src):
    with _lock:
        with _get_db() as conn:
            conn.execute(
                "UPDATE assets SET src=? WHERE sync_uuid=?", (src, sync_uuid)
            )
            conn.commit()


def _prepare_if_video(sync_uuid, filepath):
    """Convert controller-sent videos the same way as local uploads."""
    with _get_db() as conn:
        row = conn.execute(
            "SELECT id, type FROM assets WHERE sync_uuid=?", (sync_uuid,)
        ).fetchone()
    if row and row["type"] == "video":
        import media
        media.prepare_video(row["id"], filepath)


def _run_commands(controller_url, api_key, commands):
    """
    Carry out commands from the controller (reboot, restart-kiosk, update).

    Each command is acknowledged *before* it runs, and only run if the
    controller accepted the acknowledgement, so a reboot can't repeat in a
    loop. Command ids already done are remembered as a second safeguard.
    An update runs in its own systemd unit, which restarts this app.
    """
    import device
    with _get_db() as conn:
        row = conn.execute("SELECT value FROM sync_config WHERE key='done_command_ids'").fetchone()
    try:
        done = json.loads(row["value"]) if row else []
    except ValueError:
        done = []

    valid = [c for c in commands
             if isinstance(c, dict) and isinstance(c.get("id"), int)
             and c.get("command") in device.REMOTE_ACTIONS]
    todo = [c for c in valid if c["id"] not in done]
    # Already done but sent again: the earlier acknowledgement was lost, so
    # acknowledge again (without running it twice)
    repeat_ids = [c["id"] for c in valid if c["id"] in done]
    if not valid:
        return

    ids = [c["id"] for c in todo]
    req = urllib.request.Request(
        f"{controller_url}/api/sync/ack",
        data=json.dumps({"downloaded": [], "commands": ids + repeat_ids}).encode(),
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status != 200:
                return
    except Exception:
        return  # not acknowledged: the controller will send it again
    if not todo:
        return

    with _get_db() as conn:
        _write_sync_config(conn, "done_command_ids", json.dumps((done + ids)[-50:]))
        conn.commit()

    # Restart the display before a reboot makes it pointless
    for c in sorted(todo, key=lambda c: c["command"] == "reboot"):
        error = device.run_action(c["command"], delay=3)
        if error:
            _record_error(f"Remote {c['command']} failed: {error}")


def _send_ack(controller_url, api_key, tokens):
    if not tokens:
        return
    data = json.dumps({"downloaded": tokens}).encode()
    req = urllib.request.Request(
        f"{controller_url}/api/sync/ack",
        data=data,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # ack failure is non-critical; controller will retry on next sync


# ---------------------------------------------------------------------------
# Core sync cycle
# ---------------------------------------------------------------------------

def _do_sync(cfg):
    controller_url = cfg.get("controller_url", "").rstrip("/")
    api_key        = cfg.get("api_key", "")

    payload = build_sync_request()
    payload["deleted_asset_uuids"] = _get_and_clear_deleted_uuids()

    data = json.dumps(payload).encode()
    req  = urllib.request.Request(
        f"{controller_url}/api/sync",
        data=data,
        headers={"Content-Type": "application/json", "X-Api-Key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        response = json.loads(resp.read().decode())

    # If controller manually disconnected this client
    if not response.get("connected", True):
        with _get_db() as conn:
            _write_sync_config(conn, "sync_connected", "0")
            _write_sync_config(conn, "last_sync_error", "Disconnected by controller")
            conn.commit()
        return False  # signal thread to stop

    # Apply the response
    pending_downloads = apply_sync_response(response, cfg)

    # Download any staged files
    acked_tokens = []
    for token, original_name, sync_uuid in pending_downloads:
        try:
            src, filename = _download_staged_file(controller_url, api_key, token, original_name)
            _update_asset_src(sync_uuid, src)
            acked_tokens.append(token)
            _prepare_if_video(sync_uuid, os.path.join(UPLOAD_FOLDER, filename))
        except Exception as exc:
            # Log but keep going; file will be retried on next sync
            _record_error(f"Download failed for {token}: {exc}")

    _send_ack(controller_url, api_key, acked_tokens)

    # Record success
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with _get_db() as conn:
        _write_sync_config(conn, "last_sync_at", now)
        _write_sync_config(conn, "last_sync_error", "")
        conn.commit()

    # Remote commands last, so everything above is saved before a reboot
    if response.get("commands"):
        _run_commands(controller_url, api_key, response["commands"])

    return True  # keep running


def _record_error(msg):
    try:
        with _get_db() as conn:
            _write_sync_config(conn, "last_sync_error", str(msg)[:500])
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Thread management
# ---------------------------------------------------------------------------

class SyncThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="signage-sync")
        self._stop_event = threading.Event()

    def stop(self):
        self._stop_event.set()

    def run(self):
        global _thread
        while not self._stop_event.is_set():
            try:
                cfg = _read_sync_config()

                if cfg.get("sync_enabled", "0") != "1":
                    break
                if cfg.get("sync_connected", "1") != "1":
                    break

                keep_running = _do_sync(cfg)
                if not keep_running:
                    break
            except urllib.error.HTTPError as e:
                _record_error(f"HTTP {e.code}: {e.reason}")
            except urllib.error.URLError as e:
                _record_error(f"Network error: {e.reason}")
            except Exception as e:
                _record_error(str(e))

            self._stop_event.wait(POLL_INTERVAL)

        _thread = None  # allow a new thread to be started later


def maybe_start_sync_thread():
    """Start the sync thread if it isn't already running and .env is configured."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return

    env = _read_env()
    if not env.get("CONTROLLER_URL") or not env.get("CONTROLLER_API_KEY"):
        return

    cfg = _read_sync_config()
    if cfg.get("sync_connected", "1") != "1":
        return

    _thread = SyncThread()
    _thread.start()
