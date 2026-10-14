# Install the multi-stream kiosk on Jetson Thor

From a checkout containing this feature, run as the desktop user:

```bash
./scripts/kiosk/install_kiosk.sh --multi-stream
```

The installer prepares the host tools and browser, models, runtime libraries,
segmentation engine, and all four store clips. It then configures the upstream
kiosk helpers, enables the user services, and opens the multi-stream UI in the
current graphical session. If run over SSH, the browser opens at the next
desktop login. No separate model or browser launch command is needed.

The browser selects inputs automatically:

| Available USB cameras | Selected demo |
| --- | --- |
| Two or three | Two USB streams. |
| Four or more | Four USB streams. |
| Zero or one | Four recorded store streams. |

The automatic kiosk captures USB cameras directly on the Thor. The kiosk
browser runs **on the Thor**. The normal multi-stream UI also offers
**Jetson USB camera** for access to attached cameras from a remote browser:
choose **2 cameras**, click **Jetson USB cameras** in
**Camera and App Control**, then **Open Cameras and Start VLM Analysis**. **Browser camera** always refers to the computer running the
browser. By default, the kiosk opens `http://localhost:8090/multi-camera`, binds
the WebUI to loopback, and avoids self-signed certificate prompts. Optional LAN
access is described below; ordinary remote WebUI deployments use HTTPS.

Each USB stream needs a distinct physical camera. If the Thor detects fewer
cameras than the selected layout needs, the unassigned tile shows a waiting
message and Start remains disabled. The list updates automatically when another
camera appears. A camera already selected in one USB tile cannot be selected in
another. If a connected camera never appears, check its USB cable, port, or hub.

**Branch availability:** use a checkout containing `feat/multi-stream`. Locally
committed changes are unavailable from a fresh clone of
[the fork](https://github.com/adsahu-nv/live-vlm-webui) until the branch is pushed.
No commit or push is part of setup.

## Requirements

The automatic host setup targets **Jetson AGX Thor T5000, L4T 39.2.1, Ubuntu
24.04** with JetPack already installed. Allow internet access and **100 GB free
storage** for first-time public Cosmos conversion. Other hardware or JetPack
versions require adaptation and validation.

Run without sudo as the desktop demo user. Missing Ubuntu/Docker/browser
prerequisites and USB hot-plug rules may require a sudo password during installation. GPU drivers and
JetPack are not upgraded. If Docker needs reconfiguration while other containers
are running, preparation stops instead of restarting those workloads.

The user services also need **Docker access without an interactive password**,
either directly or through already configured noninteractive sudo. A password
entered during setup does not provide that access to later user services. The
installer checks access before starting services; it does not change user groups
or sudoers policy.

Use a graphical desktop login on the Thor. Automatic login, suspend, screen
locking, and display power settings remain under your control; see
[the upstream kiosk guide](kiosk.md#enable-boot-to-demo). The same guide describes
fullscreen and window modes.

## Installation options

Camera flags are optional. The UI includes **2 cameras** and **4 cameras**
controls; the installer selects automatically when no camera option is given.
USB cameras can be connected before or after the service starts, including
a boot with no cameras attached. The installer creates a small host udev helper
and a persistent camera-only device directory; the container sees additions
and removals without restarting the WebUI, Cosmos, or segmentation runtime.
The camera list refreshes automatically. An automatic USB kiosk waits for enough
cameras; an automatic source kiosk switches from store clips to live USB cameras
when enough are connected. Removing/reconnecting cameras replaces only that
kiosk camera session. Selecting **Stop** disables automatic restarts for that page.
Append options to the same installer command:

| Option | Behavior |
| --- | --- |
| `--source auto` | Select USB cameras when at least two are available; otherwise use store clips. Default. |
| `--source usb` | Require USB cameras. |
| `--source store` | Use recorded store clips. |
| `--cameras auto` | Choose two or four USB cameras, or four store streams. Default. |
| `--cameras 2` or `--cameras 4` | Keep the requested stream count. USB mode requires that many distinct cameras. |
| `--config /path/to/settings.json` | Use a separate Thor configuration. |
| `--no-start` | Prepare and install without enabling or starting services now. |

`--no-start` still installs the desktop autostart entry, just as the upstream
installer does. At a later desktop login, that entry may start the browser
service, which waits for the configured servers. It is not a switch that disables
all future startup. Run the installer again without `--no-start` when ready to
enable the server services.

With no `--multi-stream` flag, the installer keeps upstream's single-camera
behavior: install helpers and an example configuration, then leave configuration
and server enablement to the user. See [single-camera setup](kiosk.md).

## Optional access from another computer

To expose the same kiosk on a trusted local network, set `"kiosk_lan_access": true`
and `"ui_host": "0.0.0.0"` in your local `thor-demo.json`, then rerun
`./scripts/kiosk/install_kiosk.sh --multi-stream`. The installer refreshes the
WebUI and browser while reusing Cosmos. Open `http://JETSON_IP:8090/multi-camera`
(use the configured `ui_port` if different). The kiosk on the Thor continues to
use localhost; no second WebUI or model server is started.

**Jetson USB cameras** work from that remote HTTP page because capture runs on
Thor. **Browser cameras** still require HTTPS or localhost. This optional HTTP
endpoint has no authentication or encryption; use it only on a trusted demo
network. The default `kiosk_lan_access: false` keeps the kiosk on localhost.

## Models and prepared assets

Asset preparation uses the repository's `thor_demo.py kiosk-prepare` helper
internally. Existing validated files are reused. It prepares YOLOE weights and
its text encoder, the pinned TensorRT/CUDA runtime, a retail segmentation engine
built on this Thor, the Cosmos Edge-LLM runtime, and all four recorded store clips,
even when USB mode is selected. Cosmos serving engines compile on its first start.

An installed complete Cosmos checkpoint is checked locally. Otherwise, setup
tries the ready-made INT4 checkpoint using existing account access. If that
private checkpoint is unavailable, it automatically downloads NVIDIA's public
model and converts it to INT4, without a login or model-choice prompt. Conversion
uses the GPU and can take a long time. Model weights are not included in Git.

| Component | Source | Default location |
| --- | --- | --- |
| Cosmos3-Nano Reasoner INT4 AWQ | [Pinned ready-made checkpoint](https://huggingface.co/adsahu-nv/Cosmos3-Nano-Reasoner-INT4-AWQ/tree/80a3d4750b9d8b4674cc13342c58bb5c1d308184), private; used when already accessible. | `.models/cosmos/model/` |
| Original Cosmos3-Nano | [Pinned public NVIDIA model](https://huggingface.co/nvidia/Cosmos3-Nano/tree/e59a53c25979a090fa8706c9acc0c254a6e89b92), converted when the ready checkpoint is unavailable. | `.models/cosmos/source-nano/`, then `.models/cosmos/model/` |
| YOLOE-26 Small segmentation | [Official `yoloe-26s-seg.pt`](https://github.com/ultralytics/assets/releases/download/v8.4.0/yoloe-26s-seg.pt). | `.models/yoloe-26s-seg.pt` |
| YOLOE text encoder | [Official `mobileclip2_b.ts`](https://github.com/ultralytics/assets/releases/download/v8.4.0/mobileclip2_b.ts). | `.models/mobileclip2_b.ts` |

Access was checked on October 12, 2026. Sharing the application does not grant
private checkpoint access. The public model requires conversion; its original
weights cannot be placed directly in the INT4 folder. See [the Cosmos recipe](../development/cosmos-edge-demo.md#build-int4-from-the-public-nvidia-model)
and [segmentation details](../development/segmentation.md) for pinned dependencies,
model notices, and performance limits.

## Services and configuration

Cosmos runs as a separate user service; a healthy matching backend is reused.
The WebUI runs one shared YOLOE worker for all cameras. The browser waits for
WebUI/backend HTTP readiness and available segmentation before opening. The
preset enables masks, short action descriptions, and a 2-second recent-video
context per camera. Readiness does not establish successful inference or a
particular frame rate. Segmentation targets 25 FPS.

The installer creates `thor-demo.json` when absent and selects the generated
segmentation engine while preserving other settings. Relative model paths resolve
beside that file; see [the example](../../config/thor-demo.example.json). Kiosk
settings are written to a managed block in
`~/.config/live-vlm-webui/kiosk.env`; the upstream browser profile and display-mode
settings remain available. Keep the repository and configured asset paths in
place for subsequent service starts.

The localhost kiosk does not replace an existing HTTPS server occupying its
port. Stop the old deployment before migrating that port to the kiosk, or choose
a free `ui_port` for a separate installation. Ordinary HTTPS deployment remains
available separately. Changing a camera preset through the installer refreshes
the kiosk browser; changing its service configuration restarts only the named
kiosk units. An incompatible occupied endpoint must be resolved first.

Asset logs are under `artifacts/kiosk-setup/`. Service logs are available through
`journalctl --user` for `live-vlm-kiosk-server.service`,
`live-vlm-kiosk-cosmos.service`, and `live-vlm-kiosk-browser.service`. The kiosk
runs through user services; the installer's terminal does not need to remain open.
See [recovery and removal](kiosk.md#recovery-commands) for service controls.

## Validation and upstream relationship

This feature extends Chitoku YATO's upstream
[localhost kiosk startup](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/commit/ca34a14d422f068b35d9cad489af6c67840d4f27)
and [desktop autostart/display modes](https://github.com/NVIDIA-AI-IOT/live-vlm-webui/commit/cd082a446fd01be4699b93c15207e6d2be318081).
Those changes were ported into this feature branch; this is not a full merge of
upstream main.

Validation includes unit/integration checks, browser checks with mocked devices,
and a real Thor test detecting two USB cameras after the WebUI had started with
none. Both streams produced temporal VLM results and segmentation. After a
physical camera reconnect, a second Thor with prepared model assets passed an
unattended reboot with automatic desktop login, the normal fullscreen kiosk,
and two USB streams completing segmentation and fresh four-frame video inference.
The post-boot observation used server counters over roughly 3 seconds; it was
not a sustained performance benchmark or a check of displayed browser pixels.
See [the hardware validation scope](kiosk.md#verification-and-removal). Full
public-model conversion from scratch and four physical USB cameras, including
their unattended boot, remain unverified.

On the destination, verify live video, mask alignment, independent descriptions,
**Stop**, and recovery after a service restart. After stopping browser streams,
an optional recorded-stream check is:

```bash
python3 scripts/check_thor_demo.py --url http://localhost:8090 --cameras 4 --temporal
```

Use `--cameras 2` for two streams. The check creates and deletes its own temporary
session. Successful installation and 25 FPS source video do not guarantee 25
processed frames per second or one-second VLM latency. See
[the measured performance and its scope](../development/realtime-demo.md).
