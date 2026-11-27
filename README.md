# Pelican Pi Signage

A self-contained digital signage system for Raspberry Pi. One install script sets up everything: a Flask media server, a Chromium kiosk, and optional sync with a central [controller](https://github.com/pelicanmedia/pelican-pi-signage-controller).

---

## Features

- **Drag-and-drop playlist** — images, videos, and web URLs
- **Web-based admin UI** — manage content from any device on your network
- **Auto-advance** — server-side timing keeps the display in sync
- **Scheduling** — limit any item to a date range, days of the week and a time of day
- **Video duration detection** — ffprobe sets display time automatically
- **Automatic video conversion** — videos the Pi can't play smoothly (AVI, MKV, HEVC, 10-bit, 4K…) are converted to H.264 1080p after upload
- **Gapless playback** — the next video or image is preloaded a few seconds before it's due
- **Wayland and X11** — the kiosk works with Raspberry Pi OS's Wayland desktop (Pi 4/5) and X11
- **Background colour** — customisable per-playlist
- **Version badge** — admin UI shows current version and alerts when an update is available
- **Password-protected admin** — the admin UI and its API require a login; the player page stays open for the kiosk
- **Controller sync** *(optional)* — connect to a [Pelican Pi Signage Controller](https://github.com/pelicanmedia/pelican-pi-signage-controller) to manage multiple Pis centrally

---

## Requirements

- Raspberry Pi running **Raspberry Pi OS Bookworm (or newer)**
- Desktop environment with **auto-login** (for the Chromium kiosk) — Wayland (labwc/wayfire) or X11
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

- **Upload Media** — drag and drop images or videos (JPG, PNG, GIF, WebP, MP4, WebM, MOV, MKV, AVI)
- **Add Web URL** — display any webpage that allows iframe embedding
- **Playlist** — reorder items by dragging, toggle enabled/disabled, adjust duration
- **Background colour** — set the player background for letterboxed content
- **Schedule** — click the calendar icon on an item (see below)

---

## Video

**Best results:** upload **H.264 MP4 at 1080p or below**. These play straight away with no conversion.

Anything else the Pi can't play smoothly is converted automatically after upload. That includes AVI, MKV, HEVC/H.265, ProRes, 10-bit video, and anything larger than 1080p.

- While converting, the item shows **Converting…** in the playlist and isn't played. It joins the rotation once it's ready.
- Conversion runs in the background at low priority, so the current playlist keeps playing.
- **It's slow on a Pi.** Expect anywhere from a little faster than real time to several times longer than the video, depending on the model and the source. For long videos, convert to H.264 MP4 on a computer first.
- Videos sent from the controller are converted the same way.
- If a file can't be converted, the item shows **Conversion failed**. Re-export it as H.264 MP4 and upload it again.

---

## Kiosk display

The kiosk starts Chromium full-screen on the player page after the desktop logs in. It detects whether the desktop is **Wayland** (default on Pi 4/5 with Bookworm) or **X11**, and the installer turns off screen blanking.

To pass extra flags to Chromium, for example while tuning a particular display, add them to `/opt/pi-signage/.env`, then restart the kiosk with `sudo systemctl restart pi-signage-kiosk`:

```
CHROMIUM_EXTRA_FLAGS=--force-device-scale-factor=1
```

To check video decoding on the Pi, open `chrome://media-internals` in a normal Chromium window while a video plays.

---

## Scheduling

Each playlist item can be limited to:

| Setting | Example | Blank means |
|---------|---------|-------------|
| Start / end date | 1 Dec – 24 Dec | No date limit |
| Days of the week | Mon–Fri | Every day |
| Time of day | 09:00 until 17:00 | All day |

- Dates are inclusive. The end time is exclusive: 09:00–17:00 stops at 17:00.
- If the end time is earlier than the start time, the window runs past midnight. For example, **Fri 22:00 until 02:00** still plays at 01:00 on Saturday morning.
- Scheduled items show their schedule under the name. A **Not showing now** badge appears when an item is enabled but outside its schedule.
- If nothing is scheduled to show, the screen says "Nothing is scheduled to show right now."
- Schedules use the **Pi's own clock and timezone**. The installer prints the timezone. To change it, run `sudo raspi-config` → Localisation Options → Timezone.
- Schedules sync with the controller both ways (controller v1.1.0 or later).

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
├── .env                # Controller URL/API key, optional GitHub token and Chromium flags (owner-only)
├── sync.py             # Controller sync thread
├── schedule.py         # Playlist scheduling rules
├── media.py            # Video checking and background conversion
├── kiosk.sh            # Chromium kiosk launcher (Wayland/X11)
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
