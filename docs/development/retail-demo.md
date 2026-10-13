# Recorded retail demonstration

The browser shows masks directly on each camera video. VLM descriptions update
independently with their own progress and freshness indicators; see
[the real-time viewing guide](realtime-demo.md).

The **Store demo** preset supplies recorded shopping footage to the two- or
four-camera workspace. Each view runs local YOLOE segmentation and can receive
its own VLM description. No physical cameras or public camera URLs are needed.

The preferred preset uses **four separate native-25-FPS shopping shots** by
Kampus Production via Pexels. They are recorded scenes, not synchronized
footage from four cameras or actual Micro Center surveillance. The older Intel
clips remain selectable; those are four excerpts from one camera at 24 FPS.
Each selection keeps its own source, creator, and license links.

## Install and run the store demo

Set up the feature checkout and segmentation runtime using the
[Thor installation guide](../setup/thor-deployment.md). Configure the VLM API,
model name, and backend in `thor-demo.json` for the model server you are using.
The optional [Cosmos setup](cosmos-edge-demo.md) describes running that model
locally; the footage and segmentation do not require Cosmos.

From the repository root, install the sample clips and start the prepared WebUI:

```bash
python3 scripts/download_retail_25fps.py
python3 scripts/thor_demo.py webui --skip-build
```

These commands download the checksum-verified public sample videos and start
the application. They do not start or change the configured VLM server. Change
`ui_port` in `thor-demo.json` if another application already uses port 8090.
See [Thor segmentation setup](segmentation.md) for model assets, GPU runtime,
and HTTPS details.

Open `https://localhost:8090/multi-camera` in a browser on this Jetson and
accept the development certificate. With the example configuration, you can also
open `https://JETSON_IP:8090/multi-camera` from the same network. Choose
**2 cameras** or **4 cameras**, click **Store demo**, then
click **Start cameras**. The preset selects different available clips, sets
segmentation classes to `person`, `shopping cart`, `shopping basket`, and
`backpack`, and supplies a neutral shopping-activity prompt. Keep **Live
descriptions** enabled to use the configured VLM, or disable it for segmentation
alone. **Stop all** closes the session and releases its resources.

Treat generated descriptions as model output. The selected prompt asks for
visible actions and objects; this demonstration does not infer theft or intent.
Playback has a 25 FPS cap, matching the preferred Pexels source files. The
optional Intel clips retain their native 24 FPS cadence. Segmentation separately
targets 25 completed frames per second, reporting the actual completed rate.
Neither configured value is a measured segmentation, VLM, or browser-delivery
rate. No input is retimed or padded with duplicate frames to increase its FPS.

**Fast actions** selects a 32-token cap and a 512-pixel maximum VLM image edge;
**Detailed actions** selects 96 tokens and a 640-pixel maximum edge. Both use
temperature 0. Each view can use its own prompt and shows three previous
observations below the latest result. Custom prompts survive preset changes.
Descriptions can be wrong, especially about small products or partly visible
people; observations are not guaranteed facts. See the [Cosmos performance
measurements](cosmos-edge-demo.md#performance-measurements-and-timing-scope) for
full-result timing and the limits of the isolated one-second tests.

## Optional demo playback size

`LIVE_VLM_DEMO_MAX_SIZE` controls the maximum decoded demo-frame dimensions.
Its default is **960x540**. To evaluate smaller frames, set it to **640x360** in
the web UI server's environment before startup. When launching Docker directly,
pass `-e LIVE_VLM_DEMO_MAX_SIZE=640x360` to the UI container. The setting is read
once when the application starts; changing the environment later does not
change existing or new sessions in that process.

The format is strictly `WIDTHxHEIGHT`: even integer width 320–1920 and even
integer height 180–1080, with no spaces. Invalid settings fail at startup.
Frames fit within those bounds with their aspect ratio retained; smaller
sources are not enlarged. All recorded demo streams share the configured
bound. RTSP and browser-camera sources are unaffected.

Both segmentation and VLM sampling receive this same resized camera frame;
masks are still composed on their matching main-video frame. The recorded
source files, clip IDs, frame order, looping, and source cadence remain
unchanged. This is spatial resizing, not frame-rate conversion. A native
25 FPS source retains 40 ms between frames.

`GET /api/demo/videos` reports `playback_max_width` and `playback_max_height`
from the same settings used by playback. The interface displays, for example,
**Demo video up to 640×360; source 25 FPS**, separately from the model's fixed
480-pixel analysis setting. These dimensions are bounds; portrait clips may
produce narrower frames. The original downloaded Pexels files remain 1920×1080.

Smaller frames may reduce CPU conversion, mask compositing, and video-encoding
work. They also reduce displayed detail. Original-file decoding and the fixed
model input size are unchanged; a smaller playback bound is not evidence of a
particular inference or browser frame rate. Compare the combined pipeline
before choosing a demonstration profile.

## Native 25 FPS footage and verification — October 6, 2026

The preferred clips are supplied by **Kampus Production via Pexels**, under the
[Pexels License](https://www.pexels.com/license/). Its
[full terms](https://www.pexels.com/terms-of-service/) permit commercial and
noncommercial use and adaptation, subject to their restrictions. The demo
credits each clip, does not imply creator/person/brand endorsement, and does
not redistribute the unaltered footage as stock media. This is a different
license from the optional Intel dataset; Pexels footage is not relabeled CC BY-SA.

| Demo ID | Original source | Duration | Verified frames |
| --- | --- | --- | --- |
| `store-25-1` | [Browsing the shelves, #8420772](https://www.pexels.com/video/woman-looking-at-a-product-inside-a-grocery-store-8420772/) | 10.24 s | 256 |
| `store-25-2` | [Shopping by produce, #8420775](https://www.pexels.com/video/an-elderly-people-on-a-grocery-store-8420775/) | 15.60 s | 390 |
| `store-25-3` | [Choosing a bell pepper, #8420778](https://www.pexels.com/video/an-elderly-man-looking-at-a-red-bell-pepper-8420778/) | 10.68 s | 267 |
| `store-25-4` | [Paying at checkout, #8421358](https://www.pexels.com/video/couple-paying-for-their-groceries-8421358/) | 8.00 s | 200 |

The source pages list 25 FPS. `ffprobe` reports both `r_frame_rate` and
`avg_frame_rate` as `25/1` in the original UHD files and the official 1920×1080
renditions installed here. Full decoding of the four installed renditions
verified **1,113 unique frames**, **40 ms between consecutive presentation
timestamps**, and **zero identical adjacent frames**. Original and installed
renditions have matching frame counts and durations. This establishes the
supplied video cadence; it does not independently establish the capture-device
settings. No 24-to-25 conversion, interpolation, frame duplication, speed
change, or local re-encoding was applied.

The standard-library downloader fetches approximately **20.45 MB** total from
Pexels' official video CDN. It checks each pinned byte length and SHA256 before
atomic publication in `artifacts/retail-demo/pexels-<id>-1080p.mp4`. A failed or
partial download cannot become an installed clip. Existing files are verified,
not silently replaced; unrelated clips and Intel's `NOTICE.txt` are preserved.
Creator/source/asset hashes and license terms are recorded separately in
`artifacts/retail-demo/NOTICE-PEXELS.txt`. Video files remain ignored by Git.

```bash
python3 scripts/download_retail_25fps.py
python3 scripts/download_retail_25fps.py --offline
```

The second command rechecks all installed hashes with no network access.
`--output-dir` supports another destination; set `LIVE_VLM_DEMO_DIR` to that
same directory when running the server. The catalog marks these four entries
with `source_fps: 25` and `preferred_for_realtime: true`, so **Store demo**
requires enough installed 25 FPS clips for the selected camera count. With only
two installed preferred clips, choose two cameras; missing preferred clips do
not silently fall back to 24 FPS sources. The existing Intel options remain
usable through each camera's recorded-clip selector.

Local verification evidence is in `artifacts/retail-native25/verified-selection.json`,
with the CPU-only reproduction script `verify_sources.py` in that directory.
The new installer and catalog/session tests use synthetic files and mock
network responses; no models or GPU inference are required. Real source-file
rate verification and browser throughput are separate measurements: **25 FPS
source footage does not prove 25 FPS masks or one-second VLM responses**.
Earlier performance measurements below used the Intel 24 FPS footage.

## Optional Intel dataset download and verification

To keep or install the original four Intel clips as additional options:

```bash
python3 scripts/download_retail_demo.py
```

The standard-library-only setup script downloads the official
[Intel Video Summarization For Retail dataset](https://huggingface.co/datasets/Intel/Video_Summarization_For_Retail)
at revision `1fe8033097b49d07415b866bcba6ba7d7a4442c4`.

| Artifact | Location |
| --- | --- |
| Original ZIP, 179,739,885 bytes | `artifacts/retail-dataset/VideoSumForRetailData.zip` |
| Selected videos | `artifacts/retail-demo/clip1.mp4`, `clip3.mp4`, `clip8.mp4`, `clip12.mp4` |
| Footage attribution and license notice | `artifacts/retail-demo/NOTICE.txt` |

The pinned archive SHA256 is:

```text
543d53bc8af96d4d5b5dd180799e93cb615f0e417f777157e777ae7328f76073
```

The script verifies that digest before reading the ZIP. It opens only the four
exact allowlisted members, verifies their lengths and SHA256 digests, and
publishes each file with an atomic rename. It never extracts archive-provided
paths or executes the dataset's Python loader. Re-running verifies and reuses
the cached archive and selected clips; unrelated files are preserved. Partial
downloads are cleaned up and cannot become usable media files. A mismatched
existing file causes a clear failure rather than silently replacing it.

All media, the archive, and generated notices remain under the repository's
ignored `artifacts/` directory. They are not source files and should not be
committed to Git. This downloader does not install any packages.

A repeatable offline CLI check verifies the existing archive and all selected
files without requesting the network:

```bash
python3 scripts/download_retail_demo.py --offline
```

Success ends with `Ready: 4 recorded retail demo clips` and the output directory.
The optional `--archive` and `--output-dir` arguments support a separate cache
or test destination; the server's default catalog still reads
`artifacts/retail-demo/` in this checkout.

## Intel footage attribution and license

The publisher's dataset card credits **Parker Lischwe** as curator and marks the
dataset **Creative Commons Attribution-ShareAlike 4.0 International**. It does
not impose a research-only or noncommercial limitation. The
[CC BY-SA 4.0 license](https://creativecommons.org/licenses/by-sa/4.0/) permits
commercial sharing and adaptation subject to attribution, change notices, and
ShareAlike. Keep the source and license links visible with the footage; the
workspace shows them beside recorded clip selections, and setup writes a
companion `NOTICE.txt`.

The selected MP4 files are extracted without re-encoding or editing. During
demonstration, playback loops and may resize/rate-limit frames and add live
segmentation overlays. The footage and shared rendered adaptations remain
under CC BY-SA 4.0. This notice applies to those media assets, not to the
independently maintained application source code. Do not present the publisher,
curator, or depicted people or brands as endorsing the demo.

The [source card](https://huggingface.co/datasets/Intel/Video_Summarization_For_Retail)
acknowledges identifiable people and does not document original filming
provenance or participant releases. Its copyright license is published; those
additional rights are not verified by this setup. The card also recommends
against using the dataset to directly classify actions such as theft. The demo
uses neutral shopping descriptions and should not label footage as actual
Micro Center surveillance.

## Earlier segmented-preview measurements — October 5, 2026

The four-view browser test measured 21.65–21.80 completed mask frames/s per view
with descriptions disabled. With Cosmos Fast actions enabled it measured
13.53–13.57 FPS, with complete VLM response latency of 1,064 ms median and
1,367 ms p95 across 71 responses in 30 seconds. Detailed actions measured
11.67–11.70 FPS and 2,011 / 2,538 ms across 49 responses. The simultaneous
25 FPS and one-second targets were not achieved; the native clips themselves
supply only 24 fresh frames/s.

These latency figures start at server frame receipt and include preparation,
queue waiting, and the complete model answer. They exclude physical capture,
camera-to-server transfer, and browser painting. See [the Cosmos guide's
performance section](cosmos-edge-demo.md#performance-measurements-and-timing-scope)
for browser reports, the separate render-opportunity proxy, and the tested
alternatives. Recorded-video correctness and target performance are separate
results.

## Historical correctness verification — October 5, 2026

Before the 25 FPS target and batched segmentation changes, the two- and
four-stream demo passed a browser check at the former 15 FPS cap using the
actual selected MP4 clips, local YOLOE-26 Small on CUDA, and the existing Nemotron vision server.
Every stream produced mask results and its own description through WebRTC;
playback continued across multiple clip loops, and stopping removed each session.
The browser reported no JavaScript errors, and its mobile layout did not overflow.
The backend suite at that integration stage passed 157 tests after adding the
demo; this is a historical count, not the later performance-change suite.

The local evidence marker is `RETAIL_VIDEO_YOLOE_NEMOTRON_WEBRTC_PASS` in
`artifacts/retail-demo/browser-report.json`; screenshots are saved alongside it
as `ui-2-streams.png` and `ui-4-streams.png`. These are recorded-video correctness
checks on the previously verified Thor/CUDA runtime, not a sustained performance
benchmark or physical-camera validation. File decode and resizing use CPU PyAV;
YOLOE receives the resized frame on the existing serialized CUDA worker, and the
VLM receives that frame without segmentation overlays. The source keeps continuous
90 kHz timestamps across loops and at most one decoded output frame per read.
