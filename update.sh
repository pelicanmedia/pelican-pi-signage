#!/bin/bash
# Pi Signage auto-updater — must run as root
GITHUB_RAW="https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main"
INSTALLED=$(cat /opt/pi-signage/VERSION 2>/dev/null || echo "0")
LATEST=$(curl -fsSL --max-time 10 "${GITHUB_RAW}/VERSION" 2>/dev/null || echo "")

if [ -z "$LATEST" ] || [ "$LATEST" = "$INSTALLED" ]; then
    echo "Pi Signage is up to date (v${INSTALLED})"
    exit 0
fi

echo "Updating Pi Signage: v${INSTALLED} -> v${LATEST}"
if [ -f /opt/pi-signage/install.conf ]; then
    source /opt/pi-signage/install.conf
    export SUDO_USER="$INSTALL_USER"
fi
curl -fsSL "${GITHUB_RAW}/install.sh" | bash
