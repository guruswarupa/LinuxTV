# LinuxTV (Linux Media Launcher)

## Overview

LinuxTV is a fullscreen, remote-friendly launcher for home media setups (10-foot UI). It replaces the desktop/login UX with a focused launcher showing native apps + curated web services.

> This README is inside `linuxtvdesktop/` and documents the desktop launcher configuration, systemd service setup, and boot flow.


- Full-screen, Android-TV-style home screen (PySide6/PyQt5 hosting a Qt Quick UI)
- Config-driven web apps (`config.yaml`)
- Native app detection + hide-unavailable behavior
- Kiosk web app launch via chromium/firefox
- Auto-login workflow (no password prompts at Runtime)

---

## Files

- `launcher.py`: main launcher app — window/process management, config, remote control, and the `HomeBackend` view-model exposed to the QML UI
- `qml/`: the home screen UI (Qt Quick) — `Home.qml` is the root; `AppCard`/`AppRow` render the rows of tiles, `TopBar`/`SettingsPanel`/`ContextMenu`/`Toast`/`LaunchSplash`/`AmbientBackground` are the surrounding chrome
- `config.yaml`: sample config with `native_apps` + `web_apps`
- `linuxtv.service`: example systemd unit
- `setup.sh`: install dependencies + instructions message
- `README.md`: this documentation

---

## `config.yaml` schema

- `native_apps`: list of objects
  - `name`: display name
  - `cmd`: executable name
  - `icon`: optional icon path in project

- `web_apps`: list of objects
  - `name`: display name
  - `url`: URL to launch
  - `icon`: optional icon path

- `ui` (optional):
  - `reduced_effects`: `"auto"` (default), `"true"`, or `"false"` — turns off the focus zoom/glow and ambient backdrop image on weak GPUs. Can also be toggled live from the in-app Settings panel, which persists the choice back to this file.

---

## The home screen

- Rows of tiles (Favorites, Apps, Streaming, any custom categories from `categories` in `config.yaml`), each tile tinted by its icon's dominant color with the name fading in on focus.
- Search: press Up from the top row, or click the search pill in the top bar.
- Long-press Enter, the remote's Menu action, or right-click a tile for its context menu (favorite, reorder, edit, remove).
- The gear icon in the top-right opens the Settings panel (network, Bluetooth, sound, brightness, remote-login, auto-open, updates, restart/shutdown, reduced-effects toggle) — this replaces the old row of colored icon buttons.
- Keyboard navigation:
  - Arrow keys: move
  - Enter/Space: launch
  - Esc: back out of an overlay, or close the launcher (with confirmation) from the home screen
- On app exit, focus returns to the tile that was launched.

---

## Web app launch behavior

- Detects system browser in order: chromium*, chromium-browser, brave-browser, google-chrome, firefox
- Chromium-based launches in kiosk + app mode
- Firefox launches in kiosk

---

## Setup flow (recommended, Deb-based)

1. Run `setup.sh` as the normal user you want LinuxTV to use at boot.
2. The installer uses that same user as the runtime/autologin account, installs to `~/LinuxTV`, and creates `~/.linuxtv_venv`.
3. Required Debian packages are installed automatically.
4. `~/.xinitrc` is written for that runtime user and starts `~/LinuxTV/linuxtvdesktop/launcher.py`.
5. Setup autologin tty1:
   - `sudo mkdir -p /etc/systemd/system/getty@tty1.service.d`
   - create `/etc/systemd/system/getty@tty1.service.d/override.conf`:
     ```ini
     [Service]
     ExecStart=
     ExecStart=-/sbin/agetty --autologin <your-user> --noclear %I $TERM
     ```
6. Disable desktop managers:
   - `sudo systemctl disable gdm3 lightdm sddm` (adjust according to installed DM)
7. Option A: Auto-start from `.bash_profile`/`.profile` (for tty1):
   ```bash
   if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
       startx
   fi
   ```
8. Option B: systemd service (if running in graphical target with X):
   - `sudo cp ~/LinuxTV/linuxtvdesktop/linuxtv.service /etc/systemd/system/linuxtv.service`
   - `sudo systemctl daemon-reload`
   - `sudo systemctl enable linuxtv.service`

Reboot.

---

## Optional enhancements

- Add `favorites` and `categories` sections in `config.yaml` (then extend `launcher.py`).
- Add remote control/gamepad mapping using `inputs` or `evdev`.
- Add app icon downloads and fallback icons.

---

## Quick run

`python3 ~/LinuxTV/linuxtvdesktop/launcher.py`

---

## Remote control (WebSocket)

- `launcher.py` starts a WebSocket server on `ws://0.0.0.0:8765`.
- Use the gear button in the top-right corner of LinuxTV to configure phone login and choose which app auto-opens after the idle timeout.
- Expected payload: `{ "action": "UP" }`, `DOWN`, `LEFT`, `RIGHT`, `SELECT`, `BACK`, `HOME`, `CLOSE_APP`, `SHUTDOWN`, `REBOOT`.
- Server sends acknowledgment JSON `{ "status": "ok", "action": "UP" }`.
- UI navigation is mapped in real time. When an app is already open, navigation commands are forwarded to that window and `CLOSE_APP` terminates it.

## Expo mobile app (`linuxtvremote`)

1. `cd linuxtvremote`
2. `npm install` (or `yarn`)
3. `npm run start`
4. In Expo Go, open the project.

Controls:
- Input TV IP/port (e.g. `192.168.1.100:8765`)
- Connect / Disconnect
- Sign in once with the desktop username/password and save it securely on the phone
- D-pad and OK / BACK / HOME
- Close App
- Shutdown and Reboot with confirmation prompts

---

## Notes

- `launcher.py` uses `pyyaml` if config is YAML and falls back to JSON.
- No root auth/prompt is required at runtime.
- If running in a secure environment, use a dedicated runtime account or restrict autologin appropriately.
