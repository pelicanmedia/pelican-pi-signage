#!/usr/bin/env bash
# Pelican Pi Signage — turn the screen on or off.
#
#   display.sh on|off [cec]
#
# Run by the media server (as the desktop user) for the screen on/off
# schedule. Works with Wayland (labwc/wayfire, via wlr-randr) and X11 (DPMS).
# With "cec", also sends HDMI-CEC standby / wake to the TV (cec-ctl from
# v4l-utils) — many TVs then switch fully off instead of showing "no signal".

set -u

STATE="${1:-}"
CEC="${2:-}"
if [ "$STATE" != "on" ] && [ "$STATE" != "off" ]; then
    echo "usage: display.sh on|off [cec]" >&2
    exit 2
fi

export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
status=0

# --- HDMI-CEC: wake the TV before turning the output on ------------------
cec_send() {
    command -v cec-ctl >/dev/null || { echo "cec-ctl not installed" >&2; return 1; }
    local dev ok=1
    for dev in /dev/cec*; do
        [ -e "$dev" ] || continue
        cec-ctl -d "$dev" --playback >/dev/null 2>&1 || continue
        if [ "$1" = "on" ]; then
            cec-ctl -d "$dev" --to 0 --image-view-on >/dev/null 2>&1 && ok=0
        else
            cec-ctl -d "$dev" --to 0 --standby >/dev/null 2>&1 && ok=0
        fi
    done
    [ $ok -eq 0 ] || echo "No HDMI-CEC device answered" >&2
    return $ok
}

if [ "$CEC" = "cec" ] && [ "$STATE" = "on" ]; then
    cec_send on || true   # a TV that ignores CEC shouldn't block the output
fi

# --- The video output itself ---------------------------------------------
WL_SOCKET="$(ls "$XDG_RUNTIME_DIR"/wayland-[0-9] 2>/dev/null | head -n1 || true)"
if [ -n "$WL_SOCKET" ]; then
    export WAYLAND_DISPLAY="$(basename "$WL_SOCKET")"
    if ! command -v wlr-randr >/dev/null; then
        echo "wlr-randr not installed" >&2
        status=1
    else
        # Output names are the unindented lines of `wlr-randr`, e.g. HDMI-A-1
        outputs="$(wlr-randr 2>/dev/null | awk '/^[^ ]/ {print $1}')"
        if [ -z "$outputs" ]; then
            echo "No screens found by wlr-randr" >&2
            status=1
        fi
        for out in $outputs; do
            wlr-randr --output "$out" "--$STATE" || status=1
        done
    fi
elif [ -S /tmp/.X11-unix/X0 ]; then
    export DISPLAY="${DISPLAY:-:0}"
    export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"
    if [ "$STATE" = "off" ]; then
        xset +dpms && xset dpms force off || status=1
    else
        xset dpms force on && xset -dpms && xset s reset || status=1
    fi
else
    # No desktop yet (e.g. still booting): nothing to switch on. Turning off
    # is retried by the media server until the desktop is up.
    if [ "$STATE" = "off" ]; then
        echo "No desktop session yet" >&2
        status=1
    fi
fi

if [ "$CEC" = "cec" ] && [ "$STATE" = "off" ]; then
    cec_send off || status=1
fi

exit $status
