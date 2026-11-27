#!/usr/bin/env bash
# Pi Signage installer
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
    ffmpeg \
    unzip

# Screen on/off schedule: wlr-randr (Wayland) and cec-ctl (HDMI-CEC, in
# v4l-utils). Optional — install separately so a missing package can't stop
# the install.
apt-get install -y wlr-randr v4l-utils \
    || echo "    WARNING: couldn't install wlr-randr/v4l-utils — the screen schedule may not switch the screen off"

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
echo "==> Creating application directory at ${APP_DIR}…"
mkdir -p "$APP_DIR/static/uploads"
mkdir -p "$APP_DIR/static/css"
mkdir -p "$APP_DIR/static/js/vendor"
mkdir -p "$APP_DIR/templates/widgets"

# ---------------------------------------------------------------------------
# Download application files from GitHub
# ---------------------------------------------------------------------------
echo "==> Downloading application files…"
dl() { curl -fsSL "$GITHUB_RAW/$1" -o "$APP_DIR/$1"; }

dl app.py
dl sync.py
dl schedule.py
dl content.py
dl youtube.py
dl media.py
dl device.py
dl kiosk.sh
dl display.sh
dl VERSION
dl templates/index.html
dl templates/admin.html
dl templates/login.html
dl templates/setup.html
dl templates/account.html
dl templates/widgets/clock.html
dl templates/widgets/weather.html
dl templates/widgets/text.html
dl reset-password.sh
dl static/css/admin.css
dl static/css/player.css
dl static/js/admin.js
dl static/js/player.js
dl static/js/schedule-ui.js
dl static/js/content-ui.js
dl static/js/device-ui.js
dl static/js/vendor/hls.light.min.js
dl static/js/vendor/hls.js-LICENSE.txt
dl static/splash.png

# ---------------------------------------------------------------------------
# Save install config
# ---------------------------------------------------------------------------
echo "INSTALL_USER=$INSTALL_USER" > "$APP_DIR/install.conf"

# Create .env template if it doesn't already exist (preserve existing config on update)
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" << 'EOF'
# Pelican Pi Signage — local configuration
# Edit this file then restart the service: sudo systemctl restart pi-signage

# Controller sync (leave blank to run standalone)
CONTROLLER_URL=
CONTROLLER_API_KEY=

# Optional: GitHub token for update checks (not needed — the repo is public)
GITHUB_TOKEN=

# Optional: extra Chromium flags for the kiosk, space-separated
CHROMIUM_EXTRA_FLAGS=

# Optional: extra yt-dlp arguments for YouTube downloads
YTDLP_EXTRA_ARGS=
EOF
fi

# ---------------------------------------------------------------------------
# YouTube downloads: yt-dlp, plus Deno (yt-dlp needs it for YouTube)
# Re-running the installer updates both. yt-dlp also updates itself daily.
# ---------------------------------------------------------------------------
echo "==> Installing yt-dlp…"
mkdir -p "$APP_DIR/bin"
if curl -fsSL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp \
        -o "$APP_DIR/bin/yt-dlp.new"; then
    mv "$APP_DIR/bin/yt-dlp.new" "$APP_DIR/bin/yt-dlp"
else
    rm -f "$APP_DIR/bin/yt-dlp.new"
    echo "    WARNING: couldn't download yt-dlp — YouTube downloads won't work"
fi

case "$(dpkg --print-architecture 2>/dev/null)" in
    arm64) DENO_TARGET="aarch64-unknown-linux-gnu" ;;
    amd64) DENO_TARGET="x86_64-unknown-linux-gnu" ;;
    *)     DENO_TARGET="" ;;
esac
if [ -n "$DENO_TARGET" ]; then
    echo "==> Installing Deno (used by yt-dlp for YouTube)…"
    DENO_ZIP="$(mktemp)"
    if curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-$DENO_TARGET.zip" \
            -o "$DENO_ZIP" && unzip -o -q "$DENO_ZIP" -d "$APP_DIR/bin"; then
        echo "    Deno $("$APP_DIR/bin/deno" --version 2>/dev/null | head -n1 | awk '{print $2}') installed"
    else
        echo "    WARNING: couldn't install Deno — YouTube downloads may fail"
    fi
    rm -f "$DENO_ZIP"
else
    echo "    NOTE: Deno isn't available for this 32-bit system, so YouTube downloads"
    echo "          won't work (other video sites may). Use 64-bit Raspberry Pi OS for YouTube."
fi


# ---------------------------------------------------------------------------
# Set permissions
# ---------------------------------------------------------------------------
chown -R "$INSTALL_USER:$INSTALL_USER" "$APP_DIR"
chmod -R 755 "$APP_DIR"
# .env holds API keys/tokens and the DB holds the password hash — owner-only
chmod 600 "$APP_DIR/.env"
[ -f "$APP_DIR/signage.db" ] && chmod 600 "$APP_DIR/signage.db"

# ---------------------------------------------------------------------------
# Device controls: let the media server reboot, shut down, restart the kiosk
# and start a software update — and nothing else — without a password.
# The update runs as a root-owned systemd unit, never a script in $APP_DIR
# (which the desktop user owns and could edit).
# ---------------------------------------------------------------------------
echo "==> Allowing reboot / shut down / restart display / update from the admin page…"
SUDOERS_FILE="/etc/sudoers.d/pi-signage"
SUDOERS_TMP="$(mktemp)"
cat > "$SUDOERS_TMP" << EOF
# Pelican Pi Signage — device controls in the admin page and from the controller
$INSTALL_USER ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff, /usr/bin/systemctl restart $SERVICE_KIOSK, /usr/bin/systemctl start --no-block $SERVICE_UPDATE
EOF
if visudo -cf "$SUDOERS_TMP" >/dev/null; then
    install -m 440 -o root -g root "$SUDOERS_TMP" "$SUDOERS_FILE"
else
    echo "    WARNING: sudoers rule failed validation — device controls won't work"
fi
rm -f "$SUDOERS_TMP"

# ---------------------------------------------------------------------------
# Display: stop the screen blanking (Wayland and X11)
# ---------------------------------------------------------------------------
if command -v raspi-config >/dev/null; then
    echo "==> Disabling screen blanking…"
    raspi-config nonint do_blanking 1 || true
fi

# ---------------------------------------------------------------------------
# Display: hide the mouse pointer on Wayland
# The player page hides the pointer over Chromium, but only after the pointer
# has moved into its window. Until then labwc/wayfire draw their own arrow,
# which stays on screen when nobody touches the mouse. So the compositor gets
# an invisible cursor theme. X11 uses unclutter instead (see kiosk.sh).
# Takes effect after the next reboot.
# ---------------------------------------------------------------------------
echo "==> Hiding the mouse pointer…"
CURSOR_THEME="pi-signage-blank"
CURSOR_DIR="/usr/share/icons/$CURSOR_THEME"
USER_HOME="$(getent passwd "$INSTALL_USER" | cut -d: -f6)"

mkdir -p "$CURSOR_DIR/cursors"
printf '[Icon Theme]\nName=%s\nComment=Invisible mouse pointer (Pelican Pi Signage)\n' \
    "$CURSOR_THEME" > "$CURSOR_DIR/index.theme"

# One fully transparent 1x1 pointer in Xcursor format, then every common
# pointer name linked to it
python3 - "$CURSOR_DIR/cursors/left_ptr" << 'PYEOF'
import struct, sys
IMAGE, SIZE = 0xFFFD0002, 24
header = struct.pack("<4sIII", b"Xcur", 16, 0x10000, 1)
toc = struct.pack("<III", IMAGE, SIZE, 16 + 12)
# chunk header size, type, nominal size, version, w, h, xhot, yhot, delay, pixel
image = struct.pack("<IIIIIIIIII", 36, IMAGE, SIZE, 1, 1, 1, 0, 0, 0, 0)
with open(sys.argv[1], "wb") as f:
    f.write(header + toc + image)
PYEOF
for name in default arrow top_left_arrow pointer hand1 hand2 text xterm \
            wait watch progress left_ptr_watch crosshair cross move grab \
            grabbing not-allowed help question_arrow all-scroll; do
    ln -sf left_ptr "$CURSOR_DIR/cursors/$name"
done
chmod -R a+rX "$CURSOR_DIR"

# labwc (Raspberry Pi OS default): XCURSOR_THEME in its environment file.
# Start from the system file so its settings (e.g. keyboard layout) are kept.
LABWC_DIR="$USER_HOME/.config/labwc"
LABWC_ENV="$LABWC_DIR/environment"
mkdir -p "$LABWC_DIR"
if [ ! -f "$LABWC_ENV" ] && [ -f /etc/xdg/labwc/environment ]; then
    cp /etc/xdg/labwc/environment "$LABWC_ENV"
fi
touch "$LABWC_ENV"
sed -i '/^XCURSOR_THEME=/d' "$LABWC_ENV"
echo "XCURSOR_THEME=$CURSOR_THEME" >> "$LABWC_ENV"
chown "$INSTALL_USER:$INSTALL_USER" "$USER_HOME/.config"
chown -R "$INSTALL_USER:$INSTALL_USER" "$LABWC_DIR"

# wayfire (older Bookworm installs): cursor_theme in the [input] section
WAYFIRE_INI="$USER_HOME/.config/wayfire.ini"
if [ -f "$WAYFIRE_INI" ]; then
    python3 - "$WAYFIRE_INI" "$CURSOR_THEME" << 'PYEOF'
import re, sys
path, theme = sys.argv[1], sys.argv[2]
lines = open(path).read().splitlines()
out, in_input, done = [], False, False
for line in lines:
    section = re.match(r"\s*\[(.+)\]\s*$", line)
    if section:
        if in_input and not done:
            out.append(f"cursor_theme = {theme}")
            done = True
        in_input = section.group(1).strip() == "input"
    elif in_input and re.match(r"\s*cursor_theme\s*=", line):
        if not done:
            out.append(f"cursor_theme = {theme}")
            done = True
        continue
    out.append(line)
if not done:
    if not in_input:
        out += ["", "[input]"]
    out.append(f"cursor_theme = {theme}")
open(path, "w").write("\n".join(out) + "\n")
PYEOF
fi

# ---------------------------------------------------------------------------
# Display: Pelican boot splash and desktop wallpaper
# The boot splash replaces the image in Raspberry Pi OS's "pix" Plymouth
# theme; the original is kept as splash.png.bk. Rebuilding the initramfs is
# slow, so it only happens when the image has changed — updates re-run this
# installer. The wallpaper is set in pcmanfm's config, so it works when the
# installer runs over SSH or from a software update.
# ---------------------------------------------------------------------------
SPLASH_SRC="$APP_DIR/static/splash.png"
PIX_DIR="/usr/share/plymouth/themes/pix"

if [ -d "$PIX_DIR" ] && command -v plymouth-set-default-theme >/dev/null; then
    if ! cmp -s "$SPLASH_SRC" "$PIX_DIR/splash.png"; then
        echo "==> Setting the boot splash (rebuilding initramfs — this takes a minute)…"
        if [ -f "$PIX_DIR/splash.png" ] && [ ! -f "$PIX_DIR/splash.png.bk" ]; then
            cp "$PIX_DIR/splash.png" "$PIX_DIR/splash.png.bk"
        fi
        install -m 644 "$SPLASH_SRC" "$PIX_DIR/splash.png"
        plymouth-set-default-theme --rebuild-initrd pix \
            || echo "    WARNING: couldn't rebuild the initramfs — the boot splash may not change"
    fi
else
    echo "    NOTE: Plymouth 'pix' theme not found — skipping the boot splash"
fi

echo "==> Setting the desktop wallpaper…"
WALLPAPER="$USER_HOME/Pictures/splash.png"
install -d -o "$INSTALL_USER" -g "$INSTALL_USER" "$USER_HOME/Pictures"
install -m 644 -o "$INSTALL_USER" -g "$INSTALL_USER" "$SPLASH_SRC" "$WALLPAPER"

PCMANFM_DIR="$USER_HOME/.config/pcmanfm/LXDE-pi"
PCMANFM_CONF="$PCMANFM_DIR/desktop-items-0.conf"
mkdir -p "$PCMANFM_DIR"
if [ ! -f "$PCMANFM_CONF" ] && [ -f /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf ]; then
    cp /etc/xdg/pcmanfm/LXDE-pi/desktop-items-0.conf "$PCMANFM_CONF"
fi
touch "$PCMANFM_CONF"
if ! grep -q '^\[\*\]' "$PCMANFM_CONF"; then
    { printf '[*]\n'; cat "$PCMANFM_CONF"; } > "$PCMANFM_CONF.tmp"
    mv "$PCMANFM_CONF.tmp" "$PCMANFM_CONF"
fi
awk -v wp="$WALLPAPER" '
    /^wallpaper(_mode)?=/ { next }
    { print }
    /^\[\*\]/ && !done { print "wallpaper_mode=center"; print "wallpaper=" wp; done = 1 }
' "$PCMANFM_CONF" > "$PCMANFM_CONF.tmp"
mv "$PCMANFM_CONF.tmp" "$PCMANFM_CONF"
chown -R "$INSTALL_USER:$INSTALL_USER" "$USER_HOME/.config/pcmanfm"

# If the desktop is running now, change the wallpaper straight away too
if pgrep -u "$INSTALL_USER" -x pcmanfm >/dev/null; then
    USER_UID="$(id -u "$INSTALL_USER")"
    sudo -u "$INSTALL_USER" env DISPLAY=:0 WAYLAND_DISPLAY=wayland-0 \
        XDG_RUNTIME_DIR="/run/user/$USER_UID" \
        timeout 10 pcmanfm --wallpaper-mode=center --set-wallpaper="$WALLPAPER" \
        >/dev/null 2>&1 || true
fi

# ---------------------------------------------------------------------------
# Install systemd services
# ---------------------------------------------------------------------------
echo "==> Installing systemd services…"

install_service() {
    local name="$1"
    curl -fsSL "$GITHUB_RAW/services/$name" \
        | sed "s/INSTALL_USER/$INSTALL_USER/g" \
        > "/etc/systemd/system/$name"
    chmod 644 "/etc/systemd/system/$name"
}

install_service "$SERVICE_FLASK"
install_service "$SERVICE_KIOSK"
install_service "$SERVICE_UPDATE"   # started on demand only, never enabled

# ---------------------------------------------------------------------------
# Enable and start services
# ---------------------------------------------------------------------------
echo "==> Enabling services…"
systemctl daemon-reload
systemctl enable "$SERVICE_FLASK"
systemctl enable "$SERVICE_KIOSK"

echo "==> Starting Flask service…"
systemctl restart "$SERVICE_FLASK"

echo "==> Flask service status:"
systemctl status "$SERVICE_FLASK" --no-pager -l || true

echo ""
echo "====================================================="
echo " Pi Signage installed successfully!"
echo "====================================================="
echo " Player     : http://$(hostname -I | awk '{print $1}'):8081/"
echo " Admin UI   : http://$(hostname -I | awk '{print $1}'):8081/admin"
echo "              (first visit asks you to set an admin password —"
echo "               do this straight away so nobody else on the network can)"
echo ""
echo " Timezone   : $(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone)"
echo "              Playlist schedules use this. If it's wrong, fix it with:"
echo "              sudo raspi-config  (Localisation Options > Timezone)"
echo ""
echo " The Chromium kiosk will launch automatically on"
echo " next graphical login / reboot. Reboot once after"
echo " installing or updating so the mouse pointer is hidden."
echo ""
echo " To start kiosk now (from the desktop session):"
echo "   sudo systemctl start pi-signage-kiosk.service"
echo ""
echo " Logs:"
echo "   journalctl -u pi-signage -f"
echo "   journalctl -u pi-signage-kiosk -f"
echo "====================================================="
