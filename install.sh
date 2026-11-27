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

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
echo "==> Creating application directory at $APP_DIR…"
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
dl kiosk.sh
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
dl static/js/vendor/hls.light.min.js
dl static/js/vendor/hls.js-LICENSE.txt

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
# Display: stop the screen blanking (Wayland and X11)
# ---------------------------------------------------------------------------
if command -v raspi-config >/dev/null; then
    echo "==> Disabling screen blanking…"
    raspi-config nonint do_blanking 1 || true
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
echo " next graphical login / reboot."
echo ""
echo " To start kiosk now (from the desktop session):"
echo "   sudo systemctl start pi-signage-kiosk.service"
echo ""
echo " Logs:"
echo "   journalctl -u pi-signage -f"
echo "   journalctl -u pi-signage-kiosk -f"
echo "====================================================="
