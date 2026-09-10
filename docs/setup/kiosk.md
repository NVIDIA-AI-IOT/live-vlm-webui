# Boot-to-Demo Kiosk Setup

This guide describes a same-device kiosk setup for retail displays, lab benches,
and event demos. The goal is for a Jetson, DGX Spark, or Linux PC to reboot into
a running Live VLM WebUI demo without requiring staff to accept a self-signed
certificate, choose a model, or click the start button.

The setup uses two launchers:

- a systemd user service starts the Live VLM WebUI server
- an XDG desktop autostart entry launches Chromium in kiosk mode after login

Keep the VLM backend as a separate service. Ollama, vLLM, SGLang, or NIM can be
used with these helpers when the local backend does not require an API key.
This is a demo launcher, not an operating-system lockdown solution.

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

Review `~/.config/live-vlm-webui/kiosk.env` before enabling services.
An existing configuration is preserved. The installer does not change login or
power settings, install software, or start services immediately. Re-run it after
updating the launchers.

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

The `?autostart=1` URL waits for server configuration and camera enumeration,
then calls the existing Start flow. It skips service/model discovery so a slow
backend cannot replace the chosen server defaults. The normal URL keeps manual
Start behavior. Stop remains available. After a WebSocket outage, the kiosk
reloads when the server reconnects and creates a new video session.

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

## Recovery Commands

These commands are suitable for a one-page staff recovery sheet:

```bash
systemctl --user restart live-vlm-kiosk-server.service
systemctl --user restart live-vlm-kiosk-browser.service
journalctl --user -u live-vlm-kiosk-server.service -f
```

If the display is at the Ubuntu desktop, restarting the browser service should
bring the kiosk window back after the graphical session is running.

For Docker, use `docker restart live-vlm-kiosk` and `docker logs live-vlm-kiosk`.
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

Optional icons and Markdown libraries load from CDNs. If unavailable, the demo
can still start with plain text and missing decorative icons, although initial
loading may wait for network timeouts. Bundling these assets for fully offline
operation is a separate improvement.

Stop the browser service before closing Chromium for maintenance, otherwise it
relaunches. To remove automatic startup:

```bash
systemctl --user stop live-vlm-kiosk-browser.service
systemctl --user disable --now live-vlm-kiosk-server.service  # Python installation
rm ~/.config/autostart/live-vlm-kiosk.desktop
rm ~/.config/systemd/user/live-vlm-kiosk-{server,browser}.service
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
