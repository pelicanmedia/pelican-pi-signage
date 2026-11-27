# Pelican Pi Signage

A self-contained digital signage system for Raspberry Pi. One install script sets up everything: a Flask media server, a Chromium kiosk, and optional sync with a central controller.

---

## Features

- **Drag-and-drop playlist** — images, videos, web pages, live streams, YouTube videos and built-in slides
- **Built-in slides** — clock, weather and text screens, no design work needed
- **YouTube downloads** — paste a link; the Pi downloads the video so it plays smoothly, even offline
- **Live streams** — HLS (`.m3u8`) streams play full-screen
- **Auto-refreshing web pages** — dashboards reload in the background without a flash
- **Web-based admin UI** — manage content from any device on your network
- **Auto-advance** — server-side timing keeps the display in sync
- **Scheduling** — limit any item to a date range, days of the week and a time of day
- **Video duration detection** — ffprobe sets display time automatically
- **Automatic video conversion** — videos the Pi can't play smoothly (AVI, MKV, HEVC, 10-bit, 4K…) are converted to H.264 1080p after upload
- **Gapless playback** — the next video or image is preloaded a few seconds before it's due
- **Wayland and X11** — the kiosk works with Raspberry Pi OS's Wayland desktop (Pi 4/5) and X11
- **Device controls** — health (temperature, power, disk, memory), reboot / shut down / restart display, now showing with skip back and forward
- **Screen schedule** — turn the screen (and optionally the TV, via HDMI-CEC) off outside opening hours
- **Background colour** — customisable per-playlist
- **One-click updates** — the admin UI shows the current version and an **Update** button when a new release is out; a connected controller can update Pis too
- **Password-protected admin** — the admin UI and its API require a login; the player page stays open for the kiosk
- **Controller sync** *(optional)* — connect to a Pelican Pi Signage Controller to manage multiple Pis centrally

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
- **Add Web URL** — display any webpage that allows iframe embedding, with optional auto-refresh; `.m3u8` addresses play as live streams
- **Add YouTube Video** — paste a link and the Pi downloads it (see below)
- **Add Slide** — clock, weather or text screens
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

## More content types

### Web pages and auto-refresh

Any web page that allows embedding can be shown. For dashboards and other pages that change, pick an **Auto-refresh** interval when adding the page, or click the refresh icon on its playlist row. The page reloads in the background while it's on screen and replaces the old copy once loaded, so the screen doesn't flash. If the Pi was offline, a refresh brings the page back when the connection returns.

### Live streams (HLS)

Add a stream's `.m3u8` address with **Add Web URL**. Streams play for the duration you set (they never "finish" like a video), at no higher resolution than the screen. Streams need a working internet connection.

### YouTube videos

Paste a YouTube link into **Add YouTube Video**. The Pi downloads it (up to 1080p, preferring H.264 so no conversion is needed), names it after the video's title, and adds it to the playlist. Other sites supported by [yt-dlp](https://github.com/yt-dlp/yt-dlp) work too.

- The item shows **Downloading…** until it's ready. If it fails, the error is shown with a **Retry** button.
- The installer adds yt-dlp and [Deno](https://deno.com) (yt-dlp needs Deno for YouTube) to `/opt/pi-signage/bin/`. yt-dlp updates itself daily; re-running the installer updates both.
- **YouTube needs 64-bit Raspberry Pi OS**, because Deno isn't available for 32-bit.
- If YouTube blocks downloads from your network, yt-dlp options (for example a cookies file) can be added to `.env` as `YTDLP_EXTRA_ARGS=`.
- Only download videos you have the right to show.

### Built-in slides

**Add Slide** offers three ready-made screens, each with its own colours and display time:

- **Clock** — time and date, in the Pi's time zone or another one, with an optional label (e.g. "New York").
- **Weather** — current conditions and a 3-day forecast from [Open-Meteo](https://open-meteo.com) (free, no API key). Search for a place when adding it. The Pi needs internet access; if the connection drops it keeps showing the last forecast, marked "Offline".
- **Text** — a heading and message, sized automatically to fill the screen.

Use **Preview** in the form to see a slide before adding it. Slides are served by the Pi at `/widgets/clock`, `/widgets/weather` and `/widgets/text`.

---

## Device controls

The **Now showing** bar above the playlist shows what's on screen, with **Back** and **Skip** buttons.

The **Device** section shows the Pi's health: temperature, uptime, memory, disk space, IP addresses, whether the display is running, and whether the screen is on. Warnings appear for overheating, under-voltage (a weak power supply, a common cause of Pi problems), throttling, a nearly full disk, or the display not running. The controller shows the same information for each Pi.

- **Restart display** restarts Chromium, which fixes most display glitches.
- **Reboot** restarts the Pi (about a minute).
- **Shut down** turns the Pi off. It won't come back on until someone unplugs and replugs the power, so it's only on the Pi's own admin page, not the controller.

The installer adds `/etc/sudoers.d/pi-signage`, which lets the media server run exactly these four commands (`systemctl reboot`, `systemctl poweroff`, `systemctl restart pi-signage-kiosk.service`, `systemctl start --no-block pi-signage-update.service`) and nothing else.

### Screen schedule

Set **On at** and **Off at** times and the days the screen should be on (it stays off all day on the others). Outside those hours the player goes black and stops playing, and the screen's output is switched off (Wayland via `wlr-randr`, X11 via DPMS). Tick **HDMI-CEC** to also put the TV into standby and wake it again; many TVs then switch fully off instead of showing "no signal", but not every TV supports it. An off time earlier than the on time runs past midnight. The schedule uses the Pi's time zone and can also be set from the controller.

---

## Kiosk display

The kiosk starts Chromium full-screen on the player page after the desktop logs in. It detects whether the desktop is **Wayland** (default on Pi 4/5 with Bookworm) or **X11**, and the installer turns off screen blanking.

The mouse pointer is hidden. On Wayland the installer gives the desktop (labwc or wayfire) an invisible pointer theme, `pi-signage-blank`, which takes effect after a reboot. This also hides the pointer in the desktop itself. To get it back, remove the `XCURSOR_THEME=` line from `~/.config/labwc/environment` (or the `cursor_theme` line from `~/.config/wayfire.ini`) and reboot. On X11, `unclutter` hides it. The player page also hides it and doesn't respond to clicks or touches, because it's meant for signage, not interactive screens.

The installer also sets the Pelican boot splash and desktop wallpaper (`static/splash.png`). The boot splash replaces the image in Raspberry Pi OS's `pix` Plymouth theme. The original is kept as `/usr/share/plymouth/themes/pix/splash.png.bk`, and the initramfs is only rebuilt when the image changes. The wallpaper is copied to `~/Pictures/splash.png` and set, centred, in `~/.config/pcmanfm/LXDE-pi/desktop-items-0.conf`.

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

A Pi can be connected to a Pelican Pi Signage Controller for central management of multiple displays over a Hamachi VPN.

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

When a new version is out, the admin UI shows an **Update to vX.Y.Z** button. Click it and the Pi downloads and runs the installer itself. This takes a few minutes, the screen goes blank briefly while the software restarts, and the page reloads when it's done. If the Pi is connected to a controller, it can be updated from there too.

The update runs as the `pi-signage-update` systemd service. To follow it or see why one failed:

```bash
journalctl -u pi-signage-update -f
```

To update by hand, or to update a Pi older than v1.7.0 (which has no update button yet), re-run the installer:

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

# Software updates
journalctl -u pi-signage-update -f
```

---

## File Layout (on device)

```
/opt/pi-signage/
├── app.py              # Flask application
├── reset-password.sh   # Clears the admin password
├── .env                # Controller URL/API key, optional GitHub token, Chromium flags and yt-dlp options (owner-only)
├── sync.py             # Controller sync thread
├── schedule.py         # Playlist scheduling rules
├── media.py            # Video checking, conversion and downloads (background worker)
├── content.py          # Per-item options (auto-refresh etc.)
├── youtube.py          # YouTube downloads via yt-dlp
├── bin/                # yt-dlp and Deno
├── kiosk.sh            # Chromium kiosk launcher (Wayland/X11)
├── device.py           # Health info, reboot/shutdown/update, screen schedule
├── display.sh          # Turns the screen on/off (wlr-randr, DPMS, HDMI-CEC)
├── VERSION             # Installed version
├── install.conf        # Saved install user
├── signage.db          # SQLite database (playlist, settings, sync config, admin password hash)
├── templates/
│   ├── index.html      # Player page
│   ├── admin.html      # Admin UI
│   ├── login.html      # Admin login
│   ├── setup.html      # First-run password setup
│   ├── account.html    # Change password
│   └── widgets/        # Built-in slides (clock, weather, text)
└── static/
    ├── css/
    ├── js/             # incl. vendor/hls.light.min.js for live streams
    └── uploads/        # Uploaded and downloaded media files
```

Uploaded media and the database are **not touched by updates** — only application files are overwritten.

---

## License

MIT
