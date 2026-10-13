# Multi-stream mode and camera API

Select **Multi-stream** in the Live VLM WebUI header to configure **two or four
independent camera streams**. The direct link remains `/multi-camera`.
Multi-stream mode uses the same WebUI layout and **Auto / Light / Dark** theme
as single-camera mode; the theme preference carries between modes.
Opening single-camera mode uses the server’s configured model and connection;
it does not replace them with an automatically detected local service.

## Using the WebUI

Choose **2 cameras** or **4 cameras** in the toolbar above the streams.
These options stay visible in Focus view; stop the streams before changing
the count. Use the sidebar to configure optional YOLOE segmentation. The **VLM API** and **Prompt** panels provide the shared model
connection, sampling settings, and default prompt. Each camera tile can use a
browser camera, an RTSP stream, or an installed recorded sample, with its own
prompt override and separate results. Browser-camera access requires browser
permission. Recorded samples are labeled as looping recordings; see the
[retail demo guide](retail-demo.md) for setup and attribution.

The settings sidebar remains visible by default. On desktop screens, four
active cameras automatically use a compact 2×2 grid sized to the viewport.
Videos scale to fit without cropping; each tile has a scrollable analysis area
for longer descriptions and previous results. Disabled per-camera setup fields
are hidden while running and return after stopping. Two-camera mode retains
its larger previews, and narrow screens keep a scrolling layout.

Select **Focus view** to hide the sidebar and give more room to the camera
views; leave it to return to settings. Stop the streams before changing session
settings. Each tile shows the latest VLM description, its full-result latency,
and measured video and YOLO FPS. Expand **Details** for observation age,
video context, timing breakdowns, and previous results. The sidebar keeps API
configuration, generation options, and mask settings collapsed by default.
Recorded clips retain their on-video label; source and license links are under
**Video sources** below the grid.

**Video understanding** defaults to **Single frame**. Select **Recent video** for an independent recent-video buffer per camera, using a
2-, 4-, or 8-second window sampled at 2 FPS. Stop streams before changing this
setting; the Store demo preserves it. The [temporal video guide](temporal-video.md)
explains collection progress, native Edge-LLM video versus ordered-image
transport, and measured response times. These controls also exist in
single-camera mode.

With segmentation enabled, masks appear directly on their matching main-video
frames. In the default single-frame mode, the VLM samples the unmasked original
of a frame whose segmentation completed, so masks and descriptions refer to the
same source frame. Temporal mode collects a recent sequence of these originals.
The video continues while the sampled description is being generated; descriptions do
not cover every displayed frame. See the [real-time viewing
guide](realtime-demo.md) for frame identity, indicators, and measured performance.

## Implementation boundaries

The two modes share `static/webui.css` for the base WebUI styling and
`static/webui.js` for shared theme behavior. Multi-stream markup, scoped styles,
and its feature controller remain in `static/multi-camera.html`,
`static/multi-camera.css`, and `static/multi-camera.js`. The existing plain
HTML, CSS, and JavaScript approach is retained without adding a frontend
framework. Feature-specific session routes and lifecycle handling remain in
`multi_camera.py`, keeping camera coordination separate from shared page
behavior and the single-camera mode endpoints.

The API reuses `VLMService` image analysis and `VideoProcessorTrack` frame
handling. Each camera owns a model client, prompt, latest response, processing
state, and inference metrics; these clients can share one model server.
Frames from different cameras are analyzed separately. Single-camera mode's
`/offer`, `/ws`, and `/api/rtsp/*` endpoints continue to work. A custom
multi-camera client creates a session, connects its cameras, and subscribes to
that session's results socket.

The browser sends `preview_mode: "segmented"`, also the API default. Custom
clients may still request `smooth` for independent raw preview and optional
analysis snapshots; the WebUI uses masks on the main video.

These changes belong to this fork. Sharing the WebUI assets keeps the feature
maintainable alongside single-camera mode; it does not modify upstream
automatically.

## Endpoints

All paths below start with `/api/multi-camera/sessions`.

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/` | Create a two- or four-camera session. |
| `GET` | `/{session_id}` | Read the latest per-camera status, response, and metrics. |
| `GET` | `/{session_id}/ws` | Receive this session's status and camera results over WebSocket. |
| `POST` | `/{session_id}/cameras/{camera_id}/offer` | Negotiate a camera's WebRTC connection. |
| `DELETE` | `/{session_id}` | Stop the session and release all of its resources. |

For the create endpoint, use `/api/multi-camera/sessions` with no trailing slash.

## Create two cameras

With the usual server HTTPS configuration on port 8090:

```bash
curl --insecure --request POST https://localhost:8090/api/multi-camera/sessions \
  --header 'Content-Type: application/json' \
  --data '{
    "session_id": "loading-bay",
    "vlm": {
      "model": "your-vision-model",
      "api_base": "http://localhost:8000/v1",
      "api_key": "EMPTY",
      "max_tokens": 128
    },
    "process_every": 30,
    "max_latency": 0,
    "cameras": [
      {
        "camera_id": "entrance",
        "source": "webrtc",
        "vlm": {"prompt": "Describe activity at the entrance."}
      },
      {
        "camera_id": "exit",
        "source": "webrtc",
        "vlm": {"prompt": "Describe activity at the exit."}
      }
    ]
  }'
```

`--insecure` accommodates the development server's self-signed certificate.
A successful create returns HTTP `201` with the session ID, camera count, and
camera snapshots. Browser cameras start in the `waiting` state until their own
WebRTC connection delivers video. Omit `session_id` to generate an ID.

For four cameras, send exactly four entries in the same `cameras` array. Camera
IDs must be unique within the session. Use simple IDs containing letters,
numbers, underscores, or hyphens for both sessions and cameras.

The server allows up to four multi-camera sessions at a time. The number of
camera slots is fixed for the lifetime of the session. Delete and recreate the session to change the count, input source, or inference settings.

## RTSP cameras

Replace a browser camera entry with an RTSP camera entry:

```json
{
  "camera_id": "warehouse",
  "source": "rtsp",
  "rtsp_url": "rtsp://192.168.1.100:554/stream",
  "vlm": {"prompt": "Describe activity in the warehouse."}
}
```

Browser and RTSP sources can share a session. RTSP ingestion and inference start
when the session is created; a preview connection is optional. If an RTSP source
cannot be opened during creation, the request fails with HTTP `502`, and any
resources opened for that new session are released.

A camera that fails after creation does not stop the other cameras. Inspect its
state and error in the session status. Delete and recreate the session to reset
its camera connections.

## Per-camera inference settings

Settings are resolved in this order: current server VLM defaults, session `vlm`
settings, then camera `vlm` settings. Supported VLM fields are `model`, `api_base`,
`api_key`, `prompt`, `max_tokens`, `backend`, `image_max_edge`, and `temperature`.
They are copied when a session is created; subsequent updates in single-camera mode do not change an
existing multi-camera session. If the server has no configured VLM, provide a model in the
session or each camera's VLM settings. `max_tokens` accepts integers from `1` to
`32768`. `backend` is `openai` or `edgellm`; the latter sends
`reuse_context: false` and `cache_generated_tokens: false`. `image_max_edge`
is `0` (retain input dimensions) or an integer from `128` to `2048`; resizing
preserves aspect ratio. `temperature` is a finite number from `0` to `2`.

The UI's **Fast actions** preset uses 32 tokens, a 512-pixel maximum edge,
and temperature 0. **Detailed actions** uses 96 tokens, a 640-pixel maximum
edge, and temperature 0. Custom shared and per-camera prompts are preserved
when modes change. Descriptions are model observations; small objects and
partially visible activity can be misidentified.

`GET /models` includes `backend`, `api_base`, and `configured_model` for the
server's active defaults, including discovery-fallback responses. A custom
query endpoint is reported separately as `queried_api_base`; it does not change
the configured defaults. API keys are not included in this metadata.

`process_every` and `max_latency` can also be set on each camera to override the
session values:

| Setting | Meaning | Range |
| --- | --- | --- |
| `process_every` | Consider a VLM sample after N additional source frames; in segmented mode the candidate must also have completed segmentation. | Integer `1`–`3600`. |
| `max_latency` | Drop stale video frames beyond this delay, in seconds. | `0`–`10`; `0` disables dropping. |

Each camera owns at most one pending or running inference. Additional sampled
frames are skipped while that camera is busy, preventing an unbounded queue.
When it becomes available, the next eligible frame can run as soon as the
source-frame interval has elapsed; dropped segmentation frames do not restart
that interval. The VLM receives unmasked original pixels, independently resized
for its model. It can finish after newer masked frames have reached the preview.
The shared limit is selected when the application starts: four simultaneous
requests when its configured backend is `edgellm`, and two for other
OpenAI-compatible backends. Per-camera overrides do not resize this shared
limit. All streams can remain active; requests wait for available capacity.
This bound covers the multi-camera API; single-camera mode retains its existing
processing behavior. Multi-camera model requests use a 30-second HTTP timeout with no automatic retries, so a stalled
backend releases its capacity; subsequent sampled frames can retry.

Each camera uses the original image-to-JPEG and OpenAI chat-completion path,
with its configured resize and backend options. Multi-camera timing additionally
measures from server frame receipt through preparation, admission, and the
complete result. Capacity and response speed depend on the configured model
server, camera resolution, sampling interval, and simultaneous segmentation.

Segmentation has its own latest-frame queue and a `target_fps` setting, default
25 and accepted from 1 to 60. The worker batches up to four compatible frames;
completed-result FPS is measured over up to 125 timestamps per camera. The
target is a submission-rate cap, not a measured or guaranteed delivered rate.
See [segmentation configuration and metrics](segmentation.md).

## Connect browser cameras

Create one `RTCPeerConnection` per camera. Add the selected camera's video track,
create an SDP offer, set it as the local description, and wait for ICE gathering
to complete. This API exchanges complete SDP and does not expose a separate
trickle-ICE endpoint.

Send the offer to that camera's endpoint:

```javascript
const root = `/api/multi-camera/sessions/${sessionId}`;
const response = await fetch(`${root}/cameras/${cameraId}/offer`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    type: peer.localDescription.type,
    sdp: peer.localDescription.sdp,
  }),
});
if (!response.ok) throw new Error(await response.text());
const answer = await response.json();
await peer.setRemoteDescription({ type: answer.type, sdp: answer.sdp });
```

These connections use local ICE candidates and are intended for the Jetson LAN;
this backend does not configure STUN/TURN for connections across NAT.

Each browser offer must send exactly one video track, with no extra rejected
video sections. For an RTSP preview, add
one video transceiver with direction `recvonly` and use the same offer endpoint.
The server returns the camera's video on that peer connection. A second active
connection to an already connected camera returns HTTP `409`. Closing an RTSP
preview leaves its ingestion and inference running; the preview can reconnect.

## Read isolated results

Open a WebSocket at:

```text
wss://localhost:8090/api/multi-camera/sessions/loading-bay/ws
```

The first event has `type: "session_status"`. Camera inference events are tagged
with both identifiers:

```json
{
  "type": "vlm_response",
  "session_id": "loading-bay",
  "camera_id": "entrance",
  "text": "A delivery vehicle is approaching the entrance.",
  "metrics": {
    "last_latency_ms": 450,
    "avg_latency_ms": 470,
    "total_inferences": 12,
    "is_processing": false
  }
}
```

The metrics object also exposes completed-request timing:

| Field | Meaning |
| --- | --- |
| `last_e2e_latency_ms` | Server receipt of the sampled frame to the complete VLM result, including conversion, resizing/JPEG preparation, queue waiting, and API work. |
| `last_queue_ms` | Waiting for a shared inference slot. |
| `last_latency_ms` | Work after slot admission, including JPEG preparation and the complete API request. |
| `last_frame_received_unix_ms` / `last_result_unix_ms` | Wall-clock timestamps for that same completed sample and result. |
| `last_request_succeeded` | Whether the completed request produced a successful model result. |
| `last_frame_id` | Source-frame counter of this completed VLM request; matches segmentation `frame_id` in segmented mode. |
| `last_frame_pts` / `last_frame_time_base` | Original source timestamp and its units; match the completed segmentation event. |
| `last_frame_preview_mode` | `segmented` after matched-mask completion, or `raw` for direct raw sampling or segmentation-failure fallback. |
| `observation_age_ms` | Successful sampled frame's age at the time metrics are read, including time since its response completed. |

These values are not first-token timing. They exclude physical capture,
camera-to-server transfer, WebSocket delivery, and browser painting. The first
snapshot has no completed-frame timing yet; a pending request does not relabel
the previous result's timestamps. The browser shows full-result latency as **E2E** on each camera and the
**Full result**, **Queue**, and **Request** breakdown under **Details**. Its local observation-history times indicate when
the browser received descriptions, not when physical activity happened.

A `segmentation_result` event includes `frame_id`, `frame_pts`,
`frame_time_base`, and `frame_received_unix_ms` in its metrics. Correlate those
with the VLM fields using the same session and camera IDs. Segmented processing
can run before a preview is connected; correlation identifies the completed
source frame, not proof that a browser displayed it. If segmentation fails,
that camera falls back to raw preview and subsequent VLM identities are tagged
`raw`; other cameras continue independently.

Use `camera_id` to update the matching camera panel. Results are sent only to
subscribers of that session, and never to single-camera mode's global `/ws` channel.
Fetch the session status to obtain current responses after reconnecting. Closing
a results socket does not delete the session; explicitly send `DELETE` when the
custom client is finished.

```bash
curl --insecure https://localhost:8090/api/multi-camera/sessions/loading-bay
curl --insecure --request DELETE \
  https://localhost:8090/api/multi-camera/sessions/loading-bay
```

Deletion closes WebRTC peers, stops camera sources, cancels frame and inference
tasks, closes the camera-specific VLM clients, and closes session WebSockets.
Server shutdown performs the same cleanup for every active session.

## Error responses

| Status | Meaning |
| --- | --- |
| `400` | Invalid JSON/settings, unsupported camera count or source, duplicate camera IDs, or invalid offer. |
| `404` | Unknown session or camera. |
| `409` | Session ID already exists or the camera already has a WebRTC connection. |
| `502` | RTSP source setup failed. |
| `503` | Required VLM configuration is unavailable, the session limit is reached, or the server is shutting down. |

## Verification

The regression checks cover exact original-frame pixels and identity,
source-based sampling cadence, delayed VLM requests without stalled masked
video, and bounded cancellation. For the final combined browser measurements,
settings, limitations, and underlying reports, see [validation status and
measurement scope](realtime-demo.md#validation-status-and-measurement-scope).
That guide distinguishes the 25 FPS target from measured two- and four-stream
results and full VLM response latency.

Run the API integration tests with:

```bash
python -m pytest tests/integration/test_multi_camera.py -q
```

The tests use synthetic video tracks, real local WebRTC negotiation, and a local
OpenAI-compatible HTTP endpoint. They verify two/four-camera session creation,
configuration validation, independent image requests and responses, session
WebSocket isolation, bounded inference concurrency, setup rollback, and cleanup.
They do not require a physical camera, GPU, or downloaded vision model. Physical
multi-camera throughput and model quality still require deployment testing on
the intended Jetson and camera feeds.
