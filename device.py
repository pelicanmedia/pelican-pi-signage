"""
Device health and controls for Pelican Pi Signage.

- system_info()      uptime, temperature, memory, disk, IPs, kiosk state, warnings
- run_action(name)   reboot / shutdown / restart-kiosk (via the sudoers rule the
                     installer adds in /etc/sudoers.d/pi-signage)
- screen schedule    a background loop turns the screen off and on at the times
                     set in settings (display_on / display_off / display_days),
                     using display.sh (Wayland, X11 and optional HDMI-CEC)

Everything degrades gracefully when not running on a Pi (values become None).
"""

import datetime
import os
import shutil
import socket
import sqlite3
import subprocess
import threading
import time

from schedule import ScheduleError, is_active, normalize_schedule

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH  = os.path.join(BASE_DIR, "signage.db")
DISPLAY_SCRIPT = os.path.join(BASE_DIR, "display.sh")

KIOSK_SERVICE = "pi-signage-kiosk.service"
SYSTEMCTL     = "/usr/bin/systemctl"

# Commands allowed without a password by /etc/sudoers.d/pi-signage
ACTIONS = {
    "reboot":        [SYSTEMCTL, "reboot"],
    "shutdown":      [SYSTEMCTL, "poweroff"],
    "restart-kiosk": [SYSTEMCTL, "restart", KIOSK_SERVICE],
}
REMOTE_ACTIONS = {"reboot", "restart-kiosk"}  # what the controller may ask for

DISPLAY_KEYS = ("display_on", "display_off", "display_days", "display_cec")

HOT_C         = 80    # warn above this CPU temperature
DISK_LOW_PCT  = 90    # warn above this disk usage

_now_showing = {"id": None, "name": None, "since": None}
_display = {"off": False, "applied": None, "error": None}
_display_lock = threading.Lock()
_display_thread = None


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _run(cmd, timeout=5):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def _get_settings():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {k: v for k, v in rows}
    except sqlite3.Error:
        return {}


def set_now_showing(asset):
    """Called by the playlist logic whenever the item on screen changes."""
    _now_showing.update({
        "id":    asset["id"] if asset else None,
        "name":  asset["name"] if asset else None,
        "since": time.time() if asset else None,
    })


# ---------------------------------------------------------------------------
# System information
# ---------------------------------------------------------------------------

def _cpu_temp():
    raw = _read("/sys/class/thermal/thermal_zone0/temp")
    try:
        return round(int(raw) / 1000, 1)
    except (TypeError, ValueError):
        return None


def _throttled():
    """Decode `vcgencmd get_throttled` (Pi only). None if unavailable."""
    out = _run(["vcgencmd", "get_throttled"])
    if not out or "=" not in out:
        return None
    try:
        bits = int(out.split("=", 1)[1], 16)
    except ValueError:
        return None
    return {
        "under_voltage_now":  bool(bits & 0x1),
        "throttled_now":      bool(bits & 0x4),
        "under_voltage_seen": bool(bits & 0x10000),
        "throttled_seen":     bool(bits & 0x40000),
    }


def _memory():
    info = {}
    for line in (_read("/proc/meminfo") or "").splitlines():
        key, _, rest = line.partition(":")
        try:
            info[key] = int(rest.split()[0]) * 1024
        except (IndexError, ValueError):
            pass
    if "MemTotal" not in info:
        return None
    total = info["MemTotal"]
    avail = info.get("MemAvailable", 0)
    return {"total": total, "used": total - avail,
            "percent": round((total - avail) / total * 100) if total else None}


def _disk():
    try:
        u = shutil.disk_usage(BASE_DIR)
    except OSError:
        return None
    return {"total": u.total, "used": u.used, "free": u.free,
            "percent": round(u.used / u.total * 100) if u.total else None}


def _ips():
    out = _run(["hostname", "-I"])
    if out:
        return out.split()
    try:
        return [socket.gethostbyname(socket.gethostname())]
    except OSError:
        return []


def _os_name():
    for line in (_read("/etc/os-release") or "").splitlines():
        if line.startswith("PRETTY_NAME="):
            return line.split("=", 1)[1].strip('"')
    return None


def _uptime():
    raw = _read("/proc/uptime")
    try:
        return int(float(raw.split()[0]))
    except (AttributeError, ValueError, IndexError):
        return None


def system_info():
    temp = _cpu_temp()
    thr = _throttled()
    disk = _disk()
    kiosk = _run(["systemctl", "is-active", KIOSK_SERVICE]) or (
        "inactive" if shutil.which("systemctl") else None)

    warnings = []
    if temp is not None and temp >= HOT_C:
        warnings.append(f"Running hot ({temp:.0f}°C)")
    if thr and thr["under_voltage_now"]:
        warnings.append("Under-voltage — check the power supply")
    elif thr and thr["under_voltage_seen"]:
        warnings.append("Under-voltage since last boot — check the power supply")
    if thr and thr["throttled_now"]:
        warnings.append("CPU is being throttled")
    if disk and disk["percent"] is not None and disk["percent"] >= DISK_LOW_PCT:
        warnings.append(f"Disk {disk['percent']}% full")
    if kiosk and kiosk != "active":
        warnings.append("Display (kiosk) isn't running")
    if _display["error"]:
        warnings.append(f"Screen schedule: {_display['error']}")

    return {
        "hostname":     socket.gethostname(),
        "model":        (_read("/proc/device-tree/model") or "").rstrip("\x00") or None,
        "os":           _os_name(),
        "uptime":       _uptime(),
        "cpu_temp":     temp,
        "load":         round(os.getloadavg()[0], 2) if hasattr(os, "getloadavg") else None,
        "memory":       _memory(),
        "disk":         disk,
        "ips":          _ips(),
        "throttled":    thr,
        "kiosk":        kiosk,
        "screen_off":   _display["off"],
        "now_showing":  _now_showing["name"],
        "warnings":     warnings,
        "collected_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------

def run_action(name, delay=1.5):
    """
    Run reboot / shutdown / restart-kiosk shortly after returning, so the
    HTTP response (or sync acknowledgement) gets out first.
    Returns None on success, or an error message.
    """
    cmd = ACTIONS.get(name)
    if not cmd:
        return "Unknown action"
    if not os.path.exists(SYSTEMCTL) or not shutil.which("sudo"):
        return "Not supported on this system"

    def go():
        time.sleep(delay)
        subprocess.run(["sudo", "-n", *cmd], capture_output=True, timeout=60)

    # Check sudo permission now, so the user gets a useful error
    check = subprocess.run(["sudo", "-n", "-l", *cmd], capture_output=True, timeout=10)
    if check.returncode != 0:
        return "Not permitted — re-run the installer to add the sudoers rule"
    threading.Thread(target=go, daemon=True, name=f"action-{name}").start()
    return None


# ---------------------------------------------------------------------------
# Screen on/off schedule
# ---------------------------------------------------------------------------

def normalize_display_settings(data, current):
    """
    Validate display schedule settings from the admin UI / controller.
    Returns a dict of the four DISPLAY_KEYS as strings ("" = not set).
    Raises ScheduleError.
    """
    merged = {k: data.get(k, current.get(k, "")) for k in DISPLAY_KEYS}
    on, off = (merged["display_on"] or "").strip(), (merged["display_off"] or "").strip()
    days = "".join(str(merged["display_days"] or "").split(","))
    # Same rules as playlist schedules: both times or neither; days alone
    # mean "on all day on those days"; an off time before the on time runs
    # past midnight.
    sched = normalize_schedule(
        {"play_time_from": on or None, "play_time_to": off or None, "play_days": days or None})
    cec = str(merged["display_cec"]).lower() in ("1", "true", "on", "yes")
    return {
        "display_on":   sched["play_time_from"] or "",
        "display_off":  sched["play_time_to"] or "",
        "display_days": sched["play_days"] or "",
        "display_cec":  "1" if cec else "0",
    }


def screen_should_be_on(settings=None, now=None):
    s = settings if settings is not None else _get_settings()
    on, off, days = s.get("display_on"), s.get("display_off"), s.get("display_days")
    if not on and not off and not days:
        return True
    return is_active({
        "play_time_from": on or None,
        "play_time_to":   off or None,
        "play_days":      days or None,
    }, now)


def screen_is_off():
    return _display["off"]


def _apply_screen(on, cec):
    if not os.path.exists(DISPLAY_SCRIPT):
        return "display.sh is missing — re-run the installer"
    cmd = ["bash", DISPLAY_SCRIPT, "on" if on else "off"]
    if cec:
        cmd.append("cec")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return str(e)[:200]
    if r.returncode != 0:
        lines = (r.stderr or r.stdout or "failed").strip().splitlines()
        return (lines[-1] if lines else "failed")[:200]
    return None


def check_screen(force=False):
    """Apply the schedule now if the wanted state differs from the last applied."""
    settings = _get_settings()
    want_on = screen_should_be_on(settings)
    cec = settings.get("display_cec") == "1"
    with _display_lock:
        _display["off"] = not want_on
        key = (want_on, cec)
        if force or _display["applied"] != key:
            _display["error"] = _apply_screen(want_on, cec)
            # Only remember success, so a failure (e.g. desktop not up yet at
            # boot) is retried on the next check
            _display["applied"] = key if _display["error"] is None else None


def _display_loop():
    first = True
    while True:
        try:
            check_screen(force=first)
            first = False
        except Exception as e:  # never let the loop die
            _display["error"] = str(e)[:200]
        time.sleep(30)


def start_display_thread():
    global _display_thread
    if _display_thread is None or not _display_thread.is_alive():
        _display_thread = threading.Thread(target=_display_loop, daemon=True, name="screen-schedule")
        _display_thread.start()


__all__ = [
    "system_info", "run_action", "ACTIONS", "REMOTE_ACTIONS", "DISPLAY_KEYS",
    "normalize_display_settings", "screen_should_be_on", "screen_is_off",
    "check_screen", "start_display_thread", "set_now_showing", "ScheduleError",
]
