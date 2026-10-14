# Boot-to-Demo Kiosk Setup

This guide describes a same-device kiosk setup for retail displays, lab benches,
and event demos. The goal is for a Jetson, DGX Spark, or Linux PC to reboot into
a running Live VLM WebUI demo without requiring staff to accept a self-signed
certificate, choose a model, or click the start button.

On Jetson Thor, install and prepare this fork's multi-stream feature with:

```bash
./scripts/kiosk/install_kiosk.sh --multi-stream
```

This extends the upstream installer described below. It prepares host/browser
prerequisites, models, runtime libraries, the segmentation engine, and all store
clips; configures the existing kiosk helpers; then enables the user services.
Chromium opens in the current graphical session or at the next desktop login.
The browser automatically selects two USB streams for two/three available
cameras, four for four or more, and four store clips for zero/one. Its dedicated
profile grants real-camera permission and starts inference automatically.

Use `--source auto|usb|store` and `--cameras auto|2|4` to set the inputs explicitly,
`--config PATH` for another Thor configuration, or `--no-start` to prepare and
install without enabling/starting services now. **`--no-start` still installs
the desktop autostart entry**; it does not disable future browser startup after
login. The installer requires Docker access without an interactive password for
its user services and does not modify groups or sudoers. See the
[Thor guide](thor-deployment.md) for hardware, model downloads, storage, and
validation limits.

With no options, `install_kiosk.sh` preserves upstream's single-camera workflow:
install helpers, edit configuration, then enable the server manually. The
remaining manual installation steps describe that workflow. Both modes use a
systemd user service for the WebUI and an XDG desktop autostart entry for Chromium.
The backend remains a separate service; multi-stream setup adds a local Cosmos
service and reuses a healthy matching backend. One YOLOE worker in the WebUI
serves all multi-stream cameras.

The fork ports Chitoku YATO's upstream
[kiosk startup](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/commit/ca34a14d422f068b35d9cad489af6c67840d4f27)
and [desktop/window handling](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/commit/cd082a446fd01be4699b93c15207e6d2be318081)
without merging all of upstream main. Fullscreen/window selection and desktop
login behavior remain the same. This is a demo launcher, not an operating-system
lockdown solution.

## Why Localhost HTTP for Kiosks

The default WebUI uses HTTPS with a self-signed certificate because browser
camera APIs normally require a secure context. For a same-device kiosk,
`http://localhost:8090` avoids the certificate warning while remaining a
[trustworthy local origin in Chromium](https://www.chromium.org/Home/chromium-security/deprecating-powerful-features-on-insecure-origins/).

Use localhost HTTP only when the browser and WebUI server are on the same
machine. If users browse to the demo from another device over the network, keep
HTTPS enabled and install a trusted certificate.

Use the literal `localhost` URL on the demo device. A LAN IP or device hostname
over HTTP does not get the same camera-access exception. `--localhost-http`
binds to `127.0.0.1` and disables TLS, even if a different `--host` was supplied.
The existing `--no-ssl` flag remains available and HTTPS remains the default.

## Prepare the Device

Install Chromium/Chrome and `curl` on the host, connect the USB webcam, and use
a dedicated non-root desktop account. First verify a working
[VLM backend](vlm-backends.md) and a downloaded vision model that fits the device.
Test a real camera image and complete model warm-up before deployment.

Use a checkout containing this guide. The Python installation or Docker image
must also include these changes; an older released image still requires Start.
For Python 3.10+ with the native dependencies described in the
[README](../../README.md), install the checkout in a virtual environment:

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

Set `KIOSK_SERVER_BIN` below to the absolute `.venv/bin/live-vlm-webui` path.
The Docker alternative is documented below.

## Install the Kiosk Helpers

Install as the desktop demo user, not with `sudo`:

```bash
./scripts/kiosk/install_kiosk.sh
```

The installer copies:

- helper scripts to `~/.local/lib/live-vlm-webui-kiosk/`
- systemd user units to `~/.config/systemd/user/`
- a browser autostart entry to `~/.config/autostart/`
- an example config to `~/.config/live-vlm-webui/kiosk.env`

For this no-option single-camera installation, review
`~/.config/live-vlm-webui/kiosk.env` before enabling services. Existing
configuration is preserved; this mode does not install software or start services
immediately. The multi-stream option prepares software and enables services as
described above. Neither mode changes automatic login or power settings. Re-run
the installer after updating the helpers.

## Configure the Demo

Example `kiosk.env` values:

```bash
KIOSK_SERVER_BIN="$HOME/.local/bin/live-vlm-webui"
KIOSK_PORT=8090
KIOSK_API_BASE=http://localhost:11434/v1
KIOSK_MODEL=llama3.2-vision:11b
KIOSK_PROMPT='Briefly describe what you see and call out interesting objects.'
KIOSK_PROCESS_EVERY=30
KIOSK_MAX_TOKENS=128
KIOSK_BACKEND_READY_URL=http://localhost:11434/v1/models
KIOSK_BROWSER=
KIOSK_BROWSER_PROFILE="$HOME/live-vlm-webui-kiosk-profile"
KIOSK_DISPLAY_MODE=kiosk
```

If you installed with `pipx`, `KIOSK_SERVER_BIN` will usually be:

```bash
KIOSK_SERVER_BIN="$HOME/.local/bin/live-vlm-webui"
```

`kiosk.env` is sourced as shell code by the demo account; keep it private and
quote values containing spaces. The browser waits for both WebUI and
`KIOSK_BACKEND_READY_URL` to return a successful HTTP response. Change the latter
for your backend (for example `http://localhost:8000/v1/models` for vLLM), or set
it empty to skip that check. HTTP readiness does not prove the configured model
is loaded or inference works. `KIOSK_MAX_TOKENS=128` keeps demo responses short.

### Fullscreen or App Window

Set `KIOSK_DISPLAY_MODE` in `~/.config/live-vlm-webui/kiosk.env`:

| Value | Display |
| --- | --- |
| `kiosk` (default) | Fullscreen, without desktop panels or a title bar. |
| `full` | Alias for `kiosk`; the same fullscreen behavior. |
| `window` | Movable app window with a title bar, without browser tabs or an address bar. On X11, it opens on the left half of the current desktop work area. |

Apply a change by restarting the browser:

```bash
systemctl --user restart live-vlm-kiosk-browser.service
```

The saved mode also applies after desktop login or reboot. Both modes retain
camera/inference autostart and use the same dedicated profile. Window positioning
is applied once at startup; users can subsequently move or resize the app.

On X11, install the optional positioning tools (`sudo apt install wmctrl x11-utils`).
The post-start helper targets only this user's dedicated browser profile. It
corrects Chromium/X11 fullscreen state mismatches in kiosk mode and accounts for
desktop panels and window decorations in window mode. It uses the current
desktop work area, which may span multiple monitors; it is not a monitor selector.
On Wayland, or without those tools, the browser launch flags still select the
mode, but window placement follows the compositor; the window-size hint is
960×1080. The X11 fullscreen workaround is not applied there.

## Enable Boot-to-Demo

Enable the WebUI server user service:

```bash
systemctl --user daemon-reload
systemctl --user enable --now live-vlm-kiosk-server.service
```

The browser autostart entry installed by `install_kiosk.sh` starts at the next
desktop login. To test it immediately:

```bash
~/.local/lib/live-vlm-webui-kiosk/start_kiosk_browser.sh --session
```

Run this inside the demo user's graphical session. The autostart entry imports
the actual X11/Wayland environment into the user service manager without assuming
`DISPLAY=:0`. The server starts at login; Chromium restarts after process exit.
User lingering is unnecessary because the browser needs a graphical login.

Enable automatic login for the demo account in Ubuntu **Settings → Users**.
Disable suspend, screen blanking, and screen locking in **Power** and
**Privacy / Screen Lock** as appropriate for the display. Reboot and confirm
Chromium opens the camera demo without interaction.

The single-camera `?autostart=1` URL waits for server configuration and camera
enumeration, then calls the existing Start flow. It skips service/model discovery
so a slow backend cannot replace the chosen server defaults. The multi-stream
installer opens `/multi-camera?kiosk=1` with the selected input, camera-count,
and autostart parameters; it waits for the configured models and segmentation,
then starts the existing multi-stream flow. The normal URLs retain manual Start
behavior. Stop remains available. Kiosk reconnection recreates the video session
when the server returns.

## VLM Backend

Make sure the backend is available before the WebUI starts. For Ollama:

```bash
sudo systemctl enable --now ollama
ollama pull llama3.2-vision:11b
```

For vLLM, SGLang, or NIM, run the backend as a separate service and update
`KIOSK_API_BASE` and `KIOSK_MODEL` in `kiosk.env`.

## Docker Alternative

Follow the [Docker guide](docker.md) for platform prerequisites. Build this
checkout so the image includes autostart support:

```bash
# Thor; use docker/Dockerfile.jetson-orin for Orin, docker/Dockerfile for a PC.
docker build -f docker/Dockerfile.jetson-thor -t live-vlm-webui:kiosk .
docker run -d --name live-vlm-kiosk --restart unless-stopped \
  --network host \
  --health-cmd='python -c "import urllib.request; urllib.request.urlopen(\"http://localhost:8090\", timeout=5)"' \
  live-vlm-webui:kiosk \
  python -m live_vlm_webui.server --localhost-http --port 8090 \
  --api-base http://localhost:11434/v1 --model llava:7b \
  --prompt "Describe what you see in this image in one sentence." \
  --process-every 30 --max-tokens 128
```

Replace the model with one tested on your device. The HTTP health check replaces
the image's HTTPS check. GPU inference runtime options belong on the backend
container; optional WebUI GPU monitoring options are in the Docker guide. The
host browser accesses the webcam, so the WebUI container needs no camera mapping.

Do not enable the Python server unit when using Docker. Set model/prompt/API
defaults in the container command; keep the browser port and readiness URL in
`kiosk.env` consistent. Enable Docker at boot and give the backend a restart
policy too.

## HTTPS Fallback

For same-device kiosks, prefer:

```bash
live-vlm-webui --localhost-http --api-base http://localhost:11434/v1 --model llama3.2-vision:11b
```

If HTTPS is required, configure a certificate valid for the intended hostname
and trust its issuing CA in the browser/system trust store. For a self-signed
deployment, distribute and trust the demo CA explicitly. Open a dedicated
Chromium profile outside kiosk mode to verify trust and camera permission,
then use that profile for fullscreen operation. Clicking through a certificate
warning is not a reliable unattended startup procedure.

An HTTPS adaptation must update both the browser URL and `curl` readiness/trust
configuration (for example `curl --cacert /path/to/demo-ca.pem`). The supplied
scripts implement only localhost HTTP and do not bypass certificate checks.

The dedicated profile at `~/live-vlm-webui-kiosk-profile` is also accessible to
Ubuntu's Chromium snap. `--use-fake-ui-for-media-stream` automatically grants
permission and selects a **real** webcam; it does not generate fake video.
Use the profile only for this demo since that permission bypass applies to
other sites opened in the same browser. `--autoplay-policy=no-user-gesture-required`
allows playback without a click. The browser sandbox remains enabled.

## USB cameras connected after startup

The multi-stream installer includes USB hot-plug support. Cameras connected after
boot become available to the running WebUI automatically. The automatic kiosk
uses server-side USB capture and updates its own camera session when devices
arrive or leave; model services keep running. **Stop** cancels automatic camera
restarts until the page is reloaded. Manual sessions use Stop/Start to reconnect
an unplugged camera. No WebUI or model-service restart is needed for hot-plug.

The host setup installs `/etc/udev/rules.d/99-live-vlm-cameras.rules`,
`/etc/tmpfiles.d/live-vlm-cameras.conf`, and a root-owned helper under
`/usr/local/libexec/live-vlm-webui/`. They maintain only the camera device view at
`/dev/live-vlm-cameras`. To check the installed setup without changing it, run
`bash scripts/prepare_usb_cameras.sh --check` from the repository.

## Recovery Commands

These commands are suitable for a one-page staff recovery sheet:

```bash
systemctl --user restart live-vlm-kiosk-server.service
systemctl --user restart live-vlm-kiosk-browser.service
journalctl --user -u live-vlm-kiosk-server.service -f
```

If the display is at the Ubuntu desktop, restarting the browser service should
bring the kiosk window back after the graphical session is running.

For the manual Docker alternative, use `docker restart live-vlm-kiosk` and
`docker logs live-vlm-kiosk`. The multi-stream installer uses its server user
unit to run the prepared WebUI container; manage that unit instead. Its local
backend is managed by `live-vlm-kiosk-cosmos.service`.
If Chromium waits indefinitely, check the configured port and backend readiness
URL. For a profile-in-use error, close the other instance using that profile.

## Verification and Removal

Verify on the target device before unattended use:

1. Reboot and confirm automatic login, fullscreen camera video, configured
   model/prompt, and actual VLM responses without interaction.
2. Restart the WebUI and confirm video and responses resume. Close Chromium and
   confirm the browser service relaunches it.
3. Test a cold backend start, the intended network access, and camera reconnects.
   Camera permission errors, device removal, and hung inference/browser tabs can
   need operator intervention; this setup does not supervise model inference.

After physically reconnecting a C920, a second Thor with prepared model assets
passed an unattended reboot with two distinct USB camera streams, automatic
desktop login, the normal fullscreen kiosk, and stable WebUI/Cosmos services.
Both streams received new frames, completed segmentation, and returned fresh
Cosmos results using native video clips containing four frames. In a roughly
3-second observation, reported segmentation was about 24.2 FPS per stream and
the latest VLM end-to-end latencies were 1.34 and 1.48 seconds. This startup check
used server counters; it did not inspect displayed browser pixels or establish
sustained performance.

A separate test started the WebUI without cameras, then attached two USB cameras
in software and verified both live previews, temporal VLM results, and advancing
segmentation counters without restarting the app or models. Four physical USB
cameras and their unattended boot, full public Cosmos conversion from scratch,
and sustained performance remain unverified.

Optional icons and Markdown libraries load from CDNs. If unavailable, the demo
can still start with plain text and missing decorative icons, although initial
loading may wait for network timeouts. Bundling these assets for fully offline
operation is a separate improvement.

Stop the browser service before closing Chromium for maintenance, otherwise it
relaunches. To remove automatic startup:

```bash
systemctl --user stop live-vlm-kiosk-browser.service
systemctl --user disable --now live-vlm-kiosk-server.service
# If the multi-stream installer enabled the local Cosmos backend:
systemctl --user disable --now live-vlm-kiosk-cosmos.service
rm ~/.config/autostart/live-vlm-kiosk.desktop
rm ~/.config/systemd/user/live-vlm-kiosk-{server,browser}.service
# Also remove live-vlm-kiosk-cosmos.service when removing the multi-stream install.
systemctl --user daemon-reload
# Docker alternative: docker stop live-vlm-kiosk && docker rm live-vlm-kiosk
```

Configuration, scripts, and the dedicated profile remain for reuse. Restore
Ubuntu login and power settings when finished.

## Notes for Store and Event Demos

- Keep API keys out of screenshots and printed instructions.
- Use a dedicated Linux user and Chromium profile for the kiosk.
- Prefer wired power and a fixed USB camera port.
- Keep the WebUI URL on `localhost` unless remote devices need to connect.
- Avoid browser automation for normal startup. Use environment variables,
  command-line flags, and `autostart=1` so the setup survives UI changes.
