# Pelican Pi Signage

A self-contained digital signage system for Raspberry Pi. One install script sets up everything: a Flask media server, a Chromium kiosk, and a nightly auto-updater.

---

## Features

- **Drag-and-drop playlist** — images, videos, and web URLs
- **Web-based admin UI** — manage content from any device on your network
- **Auto-advance** — server-side timing keeps the display in sync
- **Video duration detection** — ffprobe sets display time automatically
- **Background colour** — customisable per-playlist
- **Auto-updates** — nightly systemd timer pulls the latest version from GitHub
- **Version badge** — admin UI shows current version and alerts when an update is available

---

## Requirements

- Raspberry Pi running **Raspberry Pi OS Bookworm (or newer)**
- Desktop environment (for the Chromium kiosk)
- Internet connection (for install and auto-updates)

---

## Install

Run this single command on your Pi:

```bash
curl -fsSL https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main/install.sh | sudo bash
```

The script will:
1. Install all dependencies (`python3-flask`, `chromium`, `ffmpeg`, etc.)
2. Write the app to `/opt/pi-signage/`
3. Install and enable two systemd services:
   - `pi-signage.service` — Flask media server (starts on boot)
   - `pi-signage-kiosk.service` — Chromium in kiosk mode (starts on graphical login)
4. Install and enable a nightly update timer (`pi-signage-update.timer`)

After install, open a browser on another device and go to:

| Page | URL |
|------|-----|
| Player | `http://<pi-ip>:8080/` |
| Admin | `http://<pi-ip>:8080/admin` |

The kiosk will launch automatically on next reboot or graphical login. To start it immediately from the desktop:

```bash
sudo systemctl start pi-signage-kiosk.service
```

---

## Admin UI

Navigate to `/admin` from any device on your network.

- **Upload Media** — drag and drop images or videos (JPG, PNG, GIF, WebP, MP4, WebM, MOV)
- **Add Web URL** — display any webpage that allows iframe embedding
- **Playlist** — reorder items by dragging, toggle enabled/disabled, adjust duration
- **Background colour** — set the player background for letterboxed content

> **Note:** Sites like Google, YouTube, and most major platforms block iframe embedding. Use URLs from local apps, dashboards, or sites you control.

---

## Updates

### Automatic
A nightly systemd timer checks GitHub for a new version. If one is found, it re-runs the install script automatically. The admin UI will show an **Update Available** badge when a newer version is detected.

### Manual
SSH into the Pi and run:

```bash
sudo bash /opt/pi-signage/update.sh
```

Or to force a full reinstall:

```bash
curl -fsSL https://raw.githubusercontent.com/pelicanmedia/pelican-pi-signage/main/install.sh | sudo bash
```

---

## Logs

```bash
# Flask server
journalctl -u pi-signage -f

# Chromium kiosk
journalctl -u pi-signage-kiosk -f

# Auto-updater
journalctl -u pi-signage-update -f
```

---

## File Layout (on device)

```
/opt/pi-signage/
├── app.py              # Flask application
├── VERSION             # Installed version
├── install.conf        # Saved install user
├── update.sh           # Auto-update script
├── signage.db          # SQLite database (playlist + settings)
├── templates/
│   ├── index.html      # Player page
│   └── admin.html      # Admin UI
└── static/
    ├── css/
    ├── js/
    └── uploads/        # Uploaded media files
```

Uploaded media and the database are **not touched by updates** — only application files are overwritten.

---

## License

MIT
