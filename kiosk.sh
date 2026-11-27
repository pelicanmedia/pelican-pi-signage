#!/usr/bin/env bash
# Pelican Pi Signage — Chromium kiosk launcher.
#
# Started by pi-signage-kiosk.service as the desktop user. Waits for the
# desktop session, detects Wayland (Pi 4/5 Bookworm default: labwc/wayfire)
# or X11, then runs Chromium full-screen on the player page.
#
# Extra Chromium flags can be added without editing this file by setting
# CHROMIUM_EXTRA_FLAGS in /opt/pi-signage/.env, e.g.
#   CHROMIUM_EXTRA_FLAGS=--force-device-scale-factor=1

set -u

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PLAYER_URL="http://localhost:8081/"
WAIT_SECONDS=120

log() { echo "kiosk: $*"; }

# --- Read optional settings from .env -------------------------------------
EXTRA_FLAGS=""
if [ -f "$APP_DIR/.env" ]; then
    EXTRA_FLAGS="$(sed -n 's/^CHROMIUM_EXTRA_FLAGS=//p' "$APP_DIR/.env" | tail -n1 | sed "s/^[\"']//; s/[\"']$//")"
fi

# --- Find Chromium ---------------------------------------------------------
CHROMIUM="$(command -v chromium || command -v chromium-browser || true)"
if [ -z "$CHROMIUM" ]; then
    log "Chromium not found"
    exit 1
fi

# --- Wait for the desktop session -----------------------------------------
UID_NUM="$(id -u)"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$UID_NUM}"

SESSION=""
for _ in $(seq 1 "$WAIT_SECONDS"); do
    WL_SOCKET="$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -n1 || true)"
    if [ -n "$WL_SOCKET" ]; then
        SESSION="wayland"
        export WAYLAND_DISPLAY="$(basename "$WL_SOCKET")"
        break
    fi
    if [ -S /tmp/.X11-unix/X0 ]; then
        SESSION="x11"
        export DISPLAY="${DISPLAY:-:0}"
        export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
        break
    fi
    sleep 1
done

if [ -z "$SESSION" ]; then
    log "No Wayland or X11 desktop found after ${WAIT_SECONDS}s (is the Pi set to boot to desktop with auto-login?)"
    exit 1
fi
log "Desktop session: $SESSION"

# --- Wait for the local media server --------------------------------------
for _ in $(seq 1 60); do
    curl -fs -o /dev/null "$PLAYER_URL" && break
    sleep 1
done

# --- Session-specific setup ------------------------------------------------
FLAGS=(
    --kiosk
    --noerrdialogs
    --disable-infobars
    --no-first-run
    --disable-session-crashed-bubble
    --disable-translate
    --disable-features=Translate,MediaSessionService
    --check-for-update-interval=31536000
    --autoplay-policy=no-user-gesture-required
    --overscroll-history-navigation=0
    --password-store=basic
    # GPU: compositing/rasterisation on the Pi's GPU instead of the CPU
    --ignore-gpu-blocklist
    --enable-gpu-rasterization
    # Avoids video flicker/tearing seen on some Pi models
    --disable-gpu-memory-buffer-video-frames
)

if [ "$SESSION" = "wayland" ]; then
    FLAGS+=(--ozone-platform=wayland --enable-features=UseOzonePlatform)
else
    FLAGS+=(--ozone-platform=x11)
    # Stop the screen blanking and hide the idle mouse pointer
    xset s off 2>/dev/null || true
    xset s noblank 2>/dev/null || true
    xset -dpms 2>/dev/null || true
    if command -v unclutter >/dev/null; then
        unclutter -idle 1 -root &
    fi
fi

# Clear Chromium's "didn't shut down correctly" state after power cuts
PREFS="$HOME/.config/chromium/Default/Preferences"
if [ -f "$PREFS" ]; then
    sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"[^"]*"/"exit_type":"Normal"/' "$PREFS" || true
fi

# shellcheck disable=SC2086  # EXTRA_FLAGS is intentionally word-split
exec "$CHROMIUM" "${FLAGS[@]}" $EXTRA_FLAGS "$PLAYER_URL"
