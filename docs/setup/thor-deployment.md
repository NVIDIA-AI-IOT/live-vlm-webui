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

The dedicated Chromium profile grants camera permission automatically and uses
real camera video. The browser must run **on the Thor** to use cameras attached
to it. The kiosk opens `http://localhost:8090/multi-camera`, binds the WebUI to
loopback, and avoids self-signed certificate prompts. This localhost exception
does not enable camera access from a LAN HTTP address; the normal remote WebUI
continues to use HTTPS.

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
prerequisites may require a sudo password during installation. GPU drivers and
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

Asset preparation has passed on the development Thor, and browser behavior was
checked with mocked cameras and backends. The user services were not installed
or enabled during that validation. A fresh physical Thor, full public-model
conversion, and unattended reboot still need end-to-end verification.

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
