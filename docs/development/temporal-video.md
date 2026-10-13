# Temporal video understanding

Both single-camera and [Multi-stream mode](multi-camera.md) offer **Video
understanding** in the existing settings panels. **Single frame** remains the
default. Choose **Temporal video (recent frames)** to give each VLM request a
short, chronological sequence from the same camera.

## Using recent frames

Select a **2, 4, or 8 second** window before starting; the default window is
4 seconds. Sampling is fixed at **2 frames per second**, giving a bounded buffer
of 4, 8, or 16 frames. The first request waits until that buffer is full.
Settings are disabled during startup and streaming; stop and restart to change
the mode or window. Selecting the recorded Store demo preserves these settings
and custom prompts.

Each camera displays collection progress before its first temporal result.
Completed results show their actual context, for example **Recent video ·
8 frames across 3.5 s**. Eight samples at 2 FPS span 3.5 seconds between the
first and last frame, even though the configured window is 4 seconds. The
result label uses measured timestamps. It stays with the previous result while
the next request runs; it does not imply that the displayed description is new.

The buffer contains original visual observations, not previous generated
answers. Cameras have independent buffers, requests, prompts, and results.
The context clears when a source reconnects, a recording loops, or the prompt
or model changes. It then collects a full new window before another temporal
request; observations from opposite sides of that boundary are not combined.
The displayed previous response and its context label remain historical until
a new response arrives.
The camera preview and optional YOLOE masks continue independently of VLM
completion. With segmentation enabled, temporal samples use original frames
whose corresponding masks completed. Video/mask FPS and temporal sample FPS
measure different work; selecting temporal mode does not imply two completed
VLM answers per second.

A useful prompt asks for visible changes across the sequence, such as “Describe
what the person picks up or puts down, using only changes visible across these
frames.” The request also includes a chronological-observation instruction and
frame timestamps. Model descriptions still require checking against the footage.

## API settings

The shared configuration object is:

```json
{
  "temporal": {
    "enabled": true,
    "window_seconds": 4,
    "sample_fps": 2
  }
}
```

Supply it at the top level of `POST /api/multi-camera/sessions` for a
multi-stream session, or in single-camera `POST /offer` and
`POST /api/rtsp/start` requests. Omitted configuration retains the applicable
server defaults; the initial default is disabled with a 4-second window and
2 FPS sampling.

Single-camera clients can change defaults while idle by sending
`{"type":"update_temporal","temporal":{...}}` over `/ws`. The server replies
with `temporal_updated`. Initial `temporal_config` and `server_config` messages
report the current configuration. The browser creates one page-local
`stream_id`, sends it in its offer/start requests, and connects to
`/ws?stream_id=...` so other single-camera clients do not receive its results.
Multi-stream events remain scoped by session and camera IDs.

Camera snapshots and `temporal_status` events expose `temporal`,
`temporal_buffer_frames`, and `temporal_buffer_ready`. Completed VLM metrics
include:

| Field | Meaning |
| --- | --- |
| `temporal_enabled` | Whether this request used temporal context. |
| `temporal_frame_count` | Number of observations supplied to the request. |
| `temporal_window_seconds` | Actual span between the first and last observations. |
| `temporal_sample_fps` | Effective sampling rate, `(frame_count - 1) / actual_span`; it can vary slightly around the configured 2 FPS. |
| `temporal_transport` | `video` for a video request or `images` for ordered image observations. |

The TensorRT Edge-LLM backend encodes the observations into a bounded MP4 and
sends `video_url` with the exact frame count. Other OpenAI-compatible backends
receive ordered, timestamped images in one request. The selected model and
server must support the corresponding input format. Errors are reported;
temporal requests do not silently fall back to a single image.

The MP4 presentation timestamps preserve relative server observation times,
and the prompt lists those same measured times. Native runtime timestamp
labels derived from average FPS can be approximate when sampling intervals
vary; use the supplied observation times and measured span for timing. These
are server observations, not physical camera-capture timestamps.

## Validation status and timing scope

The live browser smoke checks passed in single-camera mode and with two and
four streams, using real WebRTC connections and the existing Cosmos3-Nano INT4
TensorRT Edge-LLM runtime. All collected successful responses used native
`video` transport with **8 frames**, spanning approximately **3.5 seconds**.
Every camera returned separate results, the context labels matched the metrics,
no JavaScript errors occurred, and stopping released the sessions.

The checks used a **4-second window, 2 FPS sampling, and a 32-token output cap**,
with the prompt “In one short sentence, describe the visible actions and
changes across this video.” Multi-stream runs used the recorded store sources
at 640×360 with the existing YOLOE retail TensorRT segmentation engine. The
single-camera run used recorded footage as browser-camera input without
segmentation. These were short functional checks, ending after at least four
results per camera, rather than a sustained performance benchmark.

| Streams | Complete results per camera | Median full-result latency per camera | Latest rolling segmentation FPS per camera | Skipped segmentation frames per camera |
| --- | --- | --- | --- | --- |
| 1 | 4 | 1.283 s | Segmentation off | — |
| 2 | 4 each | 1.318–1.566 s | About 25.0 | 0 |
| 4 | 4–5 each | 1.903–2.526 s | About 21.7 | 32 after about 13 seconds |

Full-result latency starts when the **newest selected frame reaches the
server** and ends at the complete VLM response. It includes preparation,
queueing, and the request, but excludes the preceding lookback window,
physical capture, network transfer to the server, and browser display. The
first result also requires collecting roughly 3.5 seconds of observations for
an eight-frame window, before its request can complete. The four-stream run
did **not** meet a one-second full-result target or 25 completed segmentation
frames per second.

[The initial live report](../../artifacts/temporal-video/live-ui-initial-report.json)
preserves the per-camera measurements in the table. [Single-camera](../../artifacts/temporal-video/live-1-events.json),
[two-stream](../../artifacts/temporal-video/live-2-events.json), and
[four-stream](../../artifacts/temporal-video/live-4-events.json) event records and
[one](../../artifacts/temporal-video/live-1.png),
[two](../../artifacts/temporal-video/live-2.png), and
[four](../../artifacts/temporal-video/live-4.png) stream screenshots preserve
the corresponding initial functional evidence. Final deployment checks are
stored separately so these measurements remain reproducible. These artifacts are local validation outputs, not distributed
model assets.

Separate [native capability checks](../../artifacts/temporal-video/native-smoke.json)
accepted actual MP4 `video_url` requests with explicit **4, 8, and 16** frames;
all returned HTTP 200 without rebuilding the runtime. A [known-direction
check](../../artifacts/temporal-video/direction-smoke.json) correctly identified
a red square moving left to right and the reversed sequence moving right to
left. This is a simple temporal-order sanity check; it does not establish
accuracy on natural shopping actions or other real-world events.

A separate 23-second [source-loop runtime check](../../artifacts/temporal-video/source-boundary-runtime.json)
passed with two streams, 2-second windows, and segmentation disabled. Both
recordings looped twice; each buffer cleared and collected four fresh frames,
becoming ready again roughly 1.5 seconds after each reset. Each camera produced
15 successful native-video results. Session deletion returned HTTP 200 and a
subsequent lookup returned 404. This validates reset and cleanup behavior; it
is not a throughput measurement.

The final unit/integration run passed **384 tests**, including **51 adapter
tests** and source-boundary regressions. The [mocked browser checks](../../artifacts/temporal-video/mock-ui-report.json)
cover single-frame defaults, temporal payloads, Store demo and custom-prompt
preservation, isolated progress/results, actual-span labels, locked controls,
and cleanup on stop or failed startup. Initial status messages with no
completed inference keep the collection indicator visible; a failed request
cannot be presented as a successful context after an earlier success.

The separate [real-time performance measurements](realtime-demo.md#validation-status-and-measurement-scope)
use single-frame VLM analysis and should not be read as temporal-mode results.
