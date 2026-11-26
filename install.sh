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
    ffmpeg

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
echo "==> Creating application directory at $APP_DIR…"
mkdir -p "$APP_DIR/static/uploads"
mkdir -p "$APP_DIR/static/css"
mkdir -p "$APP_DIR/static/js"
mkdir -p "$APP_DIR/templates"

# ---------------------------------------------------------------------------
# Download application files from GitHub
# ---------------------------------------------------------------------------
echo "==> Downloading application files…"
dl() { curl -fsSL "$GITHUB_RAW/$1" -o "$APP_DIR/$1"; }

dl app.py
dl sync.py
dl VERSION
dl templates/index.html
dl templates/admin.html
dl templates/login.html
dl templates/setup.html
dl templates/account.html
dl reset-password.sh
dl static/css/admin.css
dl static/css/player.css
dl static/js/admin.js
dl static/js/player.js

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
EOF
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
