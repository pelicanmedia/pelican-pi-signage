"""
Video checking and background conversion for Pelican Pi Signage.

Chromium on a Raspberry Pi plays H.264 (and VP8/VP9) in MP4/WebM well, but
can't play AVI/MKV containers, HEVC, ProRes or 10-bit video reliably, and
anything above 1080p is too heavy. Videos that need it are converted to
H.264 1080p MP4 in a single background worker; the asset is hidden from the
player (media_status='processing') until the new file is ready.

Videos from YouTube and similar sites are downloaded by the same worker
(see youtube.py): the asset's src holds the web address until the file is
saved, then it's checked and converted like an upload.

Asset columns used:
    media_status  NULL (ready) | 'processing' | 'failed'
    media_error   short error text when failed
"""

import json
import os
import queue
import sqlite3
import subprocess
import threading

import youtube

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
DB_PATH       = os.path.join(BASE_DIR, "signage.db")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")

PLAYABLE_CONTAINERS = {"mp4", "m4v", "webm"}
PLAYABLE_CODECS     = {"h264", "vp8", "vp9"}
PLAYABLE_PIX_FMTS   = {"yuv420p", "yuvj420p"}
MAX_WIDTH, MAX_HEIGHT = 1920, 1080

TRANSCODE_TIMEOUT = 4 * 60 * 60  # seconds; long videos on a Pi are slow

_queue  = queue.Queue()
_worker = None
_worker_lock = threading.Lock()


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def probe(filepath):
    """Return {duration, codec, width, height, pix_fmt} via ffprobe, or None."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", "-select_streams", "v:0", filepath],
            capture_output=True, text=True, timeout=30,
        )
        data = json.loads(result.stdout)
    except Exception:
        return None

    stream = (data.get("streams") or [{}])[0]
    try:
        duration = max(1, round(float(data["format"]["duration"])))
    except (KeyError, TypeError, ValueError):
        duration = None
    return {
        "duration": duration,
        "codec":    stream.get("codec_name"),
        "width":    stream.get("width") or 0,
        "height":   stream.get("height") or 0,
        "pix_fmt":  stream.get("pix_fmt"),
    }


def needs_transcode(filepath, info):
    """Decide whether Chromium on a Pi needs this video converted."""
    if info is None or not info.get("codec"):
        return True  # unreadable: let ffmpeg try (it fails cleanly if hopeless)
    ext = os.path.splitext(filepath)[1].lstrip(".").lower()
    if ext not in PLAYABLE_CONTAINERS:
        return True
    if info["codec"] not in PLAYABLE_CODECS:
        return True
    if info["pix_fmt"] and info["pix_fmt"] not in PLAYABLE_PIX_FMTS:
        return True  # e.g. 10-bit or 4:2:2, no hardware decode
    w, h = info["width"], info["height"]
    if max(w, h) > MAX_WIDTH or min(w, h) > MAX_HEIGHT:
        return True
    return False


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _unique_mp4_path(src_path):
    base = os.path.splitext(os.path.basename(src_path))[0]
    candidate = os.path.join(UPLOAD_FOLDER, f"{base}.mp4")
    n = 1
    while os.path.exists(candidate) and os.path.abspath(candidate) != os.path.abspath(src_path):
        candidate = os.path.join(UPLOAD_FOLDER, f"{base}_{n}.mp4")
        n += 1
    if os.path.abspath(candidate) == os.path.abspath(src_path):
        candidate = os.path.join(UPLOAD_FOLDER, f"{base}_converted.mp4")
    return candidate


def transcode(src_path, dest_path):
    """Convert to H.264 High / yuv420p / AAC MP4, fitting inside 1920x1080."""
    tmp_path = dest_path + ".part"
    scale = (
        f"scale='min({MAX_WIDTH},iw)':'min({MAX_HEIGHT},ih)'"
        ":force_original_aspect_ratio=decrease,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    )
    cmd = [
        "nice", "-n", "10",
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", src_path,
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", scale,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        "-f", "mp4", tmp_path,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TRANSCODE_TIMEOUT)
        if result.returncode != 0:
            raise RuntimeError((result.stderr or "ffmpeg failed").strip().splitlines()[-1][:300])
        os.replace(tmp_path, dest_path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def _src_to_path(src):
    return os.path.join(BASE_DIR, src.lstrip("/"))


def _process(asset_id):
    with _get_db() as conn:
        row = conn.execute("SELECT id, name, src FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not row:
        return  # deleted meanwhile
    src = row["src"] or ""
    downloaded_path = None

    try:
        if src.startswith("/static/uploads/"):
            src_path = _src_to_path(src)
            if not os.path.exists(src_path):
                raise RuntimeError("Source file is missing")
            title = None
        elif youtube.is_valid_url(src):
            # Video from YouTube etc.: src holds the web address until downloaded
            youtube.maybe_self_update()
            src_path, title = youtube.download(src, UPLOAD_FOLDER)
            downloaded_path = src_path
        else:
            return

        final_path = src_path
        if needs_transcode(src_path, probe(src_path)):
            final_path = _unique_mp4_path(src_path)
            transcode(src_path, final_path)
        info = probe(final_path) or {}
        new_src = f"/static/uploads/{os.path.basename(final_path)}"

        with _get_db() as conn:
            current = conn.execute("SELECT id, name, src FROM assets WHERE id=?", (asset_id,)).fetchone()
            if not current:
                # Asset was deleted while working on it
                for p in {final_path, downloaded_path}:
                    if p and os.path.exists(p):
                        os.remove(p)
                return
            # Name defaults to the web address until we know the video's title
            name = title if (title and current["name"] == src) else current["name"]
            conn.execute(
                "UPDATE assets SET name=?, src=?, duration=COALESCE(?, duration),"
                " media_status=NULL, media_error=NULL WHERE id=?",
                (name, new_src, info.get("duration"), asset_id),
            )
            conn.commit()
        if os.path.abspath(final_path) != os.path.abspath(src_path):
            os.remove(src_path)
    except Exception as e:
        # A download that couldn't be converted is useless; keep the web address
        if downloaded_path and os.path.exists(downloaded_path):
            os.remove(downloaded_path)
        with _get_db() as conn:
            conn.execute(
                "UPDATE assets SET media_status='failed', media_error=? WHERE id=?",
                (str(e)[:300], asset_id),
            )
            conn.commit()


def _worker_loop():
    while True:
        asset_id = _queue.get()
        try:
            _process(asset_id)
        finally:
            _queue.task_done()


def _ensure_worker():
    global _worker
    with _worker_lock:
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_worker_loop, daemon=True, name="media-worker")
            _worker.start()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def prepare_video(asset_id, filepath):
    """
    Call after a video file is saved for an asset. Sets the duration and,
    if the file needs converting, marks the asset as processing and queues it.
    Returns True if a conversion was queued.
    """
    info = probe(filepath)
    convert = needs_transcode(filepath, info)
    with _get_db() as conn:
        if info and info.get("duration"):
            conn.execute("UPDATE assets SET duration=? WHERE id=?", (info["duration"], asset_id))
        conn.execute(
            "UPDATE assets SET media_status=?, media_error=NULL WHERE id=?",
            ("processing" if convert else None, asset_id),
        )
        conn.commit()
    if convert:
        _ensure_worker()
        _queue.put(asset_id)
    return convert


def prepare_download(asset_id):
    """Mark an asset processing and queue it (downloads web addresses, converts uploads)."""
    with _get_db() as conn:
        conn.execute(
            "UPDATE assets SET media_status='processing', media_error=NULL WHERE id=?",
            (asset_id,),
        )
        conn.commit()
    _ensure_worker()
    _queue.put(asset_id)


def resume_pending():
    """Re-queue conversions and downloads interrupted by a restart."""
    youtube.cleanup_partial(UPLOAD_FOLDER)
    try:
        with _get_db() as conn:
            rows = conn.execute(
                "SELECT id FROM assets WHERE media_status='processing'"
            ).fetchall()
    except sqlite3.OperationalError:
        return
    if rows:
        _ensure_worker()
        for r in rows:
            _queue.put(r["id"])
