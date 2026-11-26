# Pelican Pi Signage

A self-contained digital signage system for Raspberry Pi. One install script sets up everything: a Flask media server, a Chromium kiosk, and optional sync with a central [controller](https://github.com/pelicanmedia/pelican-pi-signage-controller).

---

## Features

- **Drag-and-drop playlist** — images, videos, and web URLs
- **Web-based admin UI** — manage content from any device on your network
- **Auto-advance** — server-side timing keeps the display in sync
- **Video duration detection** — ffprobe sets display time automatically
- **Background colour** — customisable per-playlist
- **Version badge** — admin UI shows current version and alerts when an update is available
- **Password-protected admin** — the admin UI and its API require a login; the player page stays open for the kiosk
- **Controller sync** *(optional)* — connect to a [Pelican Pi Signage Controller](https://github.com/pelicanmedia/pelican-pi-signage-controller) to manage multiple Pis centrally

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

After install, open a browser on another device and go to:

| Page | URL |
|------|-----|
| Player | `http://<pi-ip>:8081/` |
| Admin | `http://<pi-ip>:8081/admin` |

The kiosk will launch automatically on next reboot or graphical login. To start it immediately from the desktop:

```bash
sudo systemctl start pi-signage-kiosk.service
```

---

## Admin UI

Navigate to `/admin` from any device on your network.

On first visit you'll be asked to **set an admin password** (minimum 8 characters) — do this straight after install. Use **Account** in the header to change it, and **Log out** when finished.

Forgotten the password? SSH into the Pi and run:

```bash
sudo /opt/pi-signage/reset-password.sh
```

This clears the password (playlist and media are untouched) and the next visit to `/admin` shows the setup page again.

- **Upload Media** — drag and drop images or videos (JPG, PNG, GIF, WebP, MP4, WebM, MOV)
- **Add Web URL** — display any webpage that allows iframe embedding
- **Playlist** — reorder items by dragging, toggle enabled/disabled, adjust duration
- **Background colour** — set the player background for letterboxed content

---

## Controller Sync *(optional)*

A Pi can be connected to a [Pelican Pi Signage Controller](https://github.com/pelicanmedia/pelican-pi-signage-controller) for central management of multiple displays over a Hamachi VPN.

**To connect:**
1. Register the Pi on the controller and copy the generated API key
2. SSH into the Pi and edit `/opt/pi-signage/.env`:
   ```
   CONTROLLER_URL=http://<controller-ip>:8080
   CONTROLLER_API_KEY=<paste key here>
   ```
3. Restart the service: `sudo systemctl restart pi-signage`
4. The **Sync** badge in the Pi's admin header shows the connection status

Once connected:
- The Pi polls the controller every 30 seconds and syncs any playlist or settings changes
- Media uploaded via the controller is automatically downloaded to this Pi
- Changes made locally on the Pi sync back to the controller (last-write-wins)
- If the controller is unreachable the Pi continues playing its current playlist unaffected

To disconnect a Pi from the controller, use the Disconnect button on the controller's Pi detail page. The Pi will continue operating autonomously.

---

## Updates

When the admin UI shows an **Update Available** badge, re-run the installer to update:

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
```

---

## File Layout (on device)

```
/opt/pi-signage/
├── app.py              # Flask application
├── reset-password.sh   # Clears the admin password
├── .env                # Controller URL/API key, optional GitHub token (owner-only)
├── sync.py             # Controller sync thread
├── VERSION             # Installed version
├── install.conf        # Saved install user
├── signage.db          # SQLite database (playlist, settings, sync config, admin password hash)
├── templates/
│   ├── index.html      # Player page
│   ├── admin.html      # Admin UI
│   ├── login.html      # Admin login
│   ├── setup.html      # First-run password setup
│   └── account.html    # Change password
└── static/
    ├── css/
    ├── js/
    └── uploads/        # Uploaded media files
```

Uploaded media and the database are **not touched by updates** — only application files are overwritten.

---

## License

MIT
