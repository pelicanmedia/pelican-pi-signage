#!/usr/bin/env bash
# Clears the Pi Signage admin password so the next visit to /admin
# shows the first-time setup page. Playlist and media are not touched.
# Usage: sudo /opt/pi-signage/reset-password.sh

set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
DB="$APP_DIR/signage.db"

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Run with sudo: sudo $0" >&2
    exit 1
fi

if [ ! -f "$DB" ]; then
    echo "ERROR: Database not found at $DB" >&2
    exit 1
fi

python3 - "$DB" <<'EOF'
import sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
conn.execute("CREATE TABLE IF NOT EXISTS auth (id INTEGER PRIMARY KEY, password_hash TEXT NOT NULL)")
conn.execute("DELETE FROM auth")
# Rotate the session key so any existing logins are signed out
conn.execute("CREATE TABLE IF NOT EXISTS app_config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
conn.execute("DELETE FROM app_config WHERE key='secret_key'")
conn.commit()
EOF

systemctl restart pi-signage.service
echo "Admin password cleared. Open http://<pi-ip>:8081/admin to set a new one."
