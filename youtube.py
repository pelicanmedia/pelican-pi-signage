"""
Download videos from YouTube (and other sites yt-dlp supports), shared by
the client and the controller (keep both copies identical).

The installer puts the standalone yt-dlp release in <app>/bin/yt-dlp (owned
by the service user, so it can update itself) and, on 64-bit systems, Deno
in <app>/bin/deno, which yt-dlp needs for YouTube. A system-wide yt-dlp is
used if the bundled one is missing.

Optional .env setting:
    YTDLP_EXTRA_ARGS   extra yt-dlp arguments, e.g. --cookies /path/cookies.txt
"""

import os
import shlex
import shutil
import subprocess
import tempfile
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR  = os.path.join(BASE_DIR, "bin")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# Prefer H.264 (hardware-decoded on Pi 4, no conversion needed), max 1080p
FORMAT = (
    "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/"
    "b[height<=1080][vcodec^=avc1]/"
    "bv*[height<=1080]+ba/b[height<=1080]/b"
)
DOWNLOAD_TIMEOUT = 2 * 60 * 60   # seconds
UPDATE_EVERY     = 24 * 60 * 60  # self-update the bundled yt-dlp at most daily

_last_update_check = 0.0


def _binary():
    bundled = os.path.join(BIN_DIR, "yt-dlp")
    if os.access(bundled, os.X_OK):
        return bundled
    return shutil.which("yt-dlp")


def available():
    return _binary() is not None


def _env():
    env = dict(os.environ)
    env["PATH"] = BIN_DIR + os.pathsep + env.get("PATH", "/usr/bin:/bin")
    return env


def _extra_args():
    try:
        with open(ENV_FILE) as f:
            for line in f:
                line = line.strip()
                if line.startswith("YTDLP_EXTRA_ARGS="):
                    value = line.partition("=")[2].strip().strip('"').strip("'")
                    return shlex.split(value)
    except FileNotFoundError:
        pass
    return []


def is_valid_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://")) and len(url) < 2000


def maybe_self_update():
    """YouTube changes often; keep the bundled yt-dlp current (best effort)."""
    global _last_update_check
    binary = _binary()
    if not binary or os.path.dirname(binary) != BIN_DIR:
        return
    if time.time() - _last_update_check < UPDATE_EVERY:
        return
    _last_update_check = time.time()
    try:
        subprocess.run([binary, "-U"], capture_output=True, timeout=120, env=_env())
    except Exception:
        pass


def _unique_path(folder, filename):
    base, ext = os.path.splitext(filename)
    dest = os.path.join(folder, filename)
    n = 1
    while os.path.exists(dest):
        dest = os.path.join(folder, f"{base}_{n}{ext}")
        n += 1
    return dest


def cleanup_partial(out_dir):
    """Remove temp folders left by downloads interrupted by a restart."""
    try:
        for name in os.listdir(out_dir):
            if name.startswith(".yt-"):
                shutil.rmtree(os.path.join(out_dir, name), ignore_errors=True)
    except FileNotFoundError:
        pass


def download(url, out_dir):
    """
    Download `url` into `out_dir`. Returns (filepath, title).
    Raises RuntimeError with yt-dlp's error message on failure.
    """
    binary = _binary()
    if not binary:
        raise RuntimeError("yt-dlp is not installed — re-run the installer")
    if not is_valid_url(url):
        raise RuntimeError("Not a valid web address")

    os.makedirs(out_dir, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix=".yt-", dir=out_dir)
    try:
        cmd = [
            binary,
            "--no-playlist", "--no-progress", "--no-mtime",
            "--restrict-filenames",
            "-f", FORMAT,
            "--merge-output-format", "mp4",
            "-o", os.path.join(tmp, "%(title).60B-%(id)s.%(ext)s"),
            "--print", "after_move:title",
            *_extra_args(),
            url,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=DOWNLOAD_TIMEOUT, env=_env())
        except subprocess.TimeoutExpired:
            raise RuntimeError("Download took too long and was stopped")

        if result.returncode != 0:
            lines = [l for l in (result.stderr or "").splitlines() if l.strip()]
            errors = [l for l in lines if l.startswith("ERROR")]
            raise RuntimeError(((errors or lines or ["yt-dlp failed"])[-1])[:300])

        files = [
            os.path.join(tmp, f) for f in os.listdir(tmp)
            if not f.endswith((".part", ".ytdl")) and os.path.isfile(os.path.join(tmp, f))
        ]
        if not files:
            raise RuntimeError("yt-dlp finished but no video file was produced")
        downloaded = max(files, key=os.path.getsize)

        dest = _unique_path(out_dir, os.path.basename(downloaded))
        shutil.move(downloaded, dest)
        title = (result.stdout or "").strip().splitlines()
        return dest, (title[-1].strip() if title else None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
