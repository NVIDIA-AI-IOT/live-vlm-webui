"""HTTP/session integration for independent, frame-aligned camera segmentation."""

import asyncio
import base64
import io
import threading
import time
from fractions import Fraction
from itertools import pairwise
from types import SimpleNamespace
from urllib.parse import urlparse

import numpy as np
import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiortc import VideoStreamTrack
from av import VideoFrame
from PIL import Image

from live_vlm_webui import multi_camera, server, video_processor
from live_vlm_webui.segmentation import SegmentationUnavailable, composite_masks
from tests.integration import test_multi_camera as camera_tests
from tests.integration.test_multi_camera import (
    BASE,
    create_session,
    session_body,
)

# Reuse the local OpenAI-compatible HTTP endpoint without importing another test.
inference_api = camera_tests.inference_api


class MovingObjectCamera(VideoStreamTrack):
    """Each camera has a distinct background and an object that moves every frame."""

    def __init__(self, number):
        super().__init__()
        self.number = number
        self.frames_received = 0
        self.last_pts = None

    async def recv(self):
        pts, time_base = await self.next_timestamp()
        self.frames_received += 1
        pixels = np.full((48, 64, 3), self.number * 30, dtype=np.uint8)
        x = (self.frames_received % 4) * 12
        pixels[16:32, x : x + 8] = 255
        frame = VideoFrame.from_ndarray(pixels, format="bgr24")
        frame.pts, frame.time_base = pts, time_base
        self.last_pts = pts
        return frame


class FakeSegmentation:
    """Predict a moving white object's mask while retaining real compositing."""

    def __init__(self):
        self.available = True
        self.closed = False
        self.prepared = []
        self.calls = []
        self.frames = {}
        self.fail_camera = None
        self.hold = asyncio.Event()
        self.hold.set()
        self.cancel_release = asyncio.Event()
        self.cancel_release.set()
        self.cancelled = []

    async def status(self):
        return {
            "available": self.available,
            "model": "fake-yoloe.pt",
            "device": "test",
            "error": None if self.available else "Test model is missing",
        }

    async def prepare(self, config):
        if not self.available:
            raise SegmentationUnavailable("Test model is missing")
        self.prepared.append(config)

    async def render(self, frame, config):
        pixels = frame.to_ndarray(format="bgr24")
        number = round(int(pixels[0, 0, 0]) / 30)
        self.calls.append((number, frame.pts))
        self.frames[number, frame.pts] = pixels.copy()
        try:
            await self.hold.wait()
            await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.append(number)
            await self.cancel_release.wait()
            raise
        if number == self.fail_camera:
            raise SegmentationUnavailable("Synthetic model failure")
        mask = pixels[:, :, 0] > 200
        output = VideoFrame.from_ndarray(
            composite_masks(pixels, mask, config.opacity), format="bgr24"
        )
        output.pts, output.time_base = frame.pts, frame.time_base
        return output, {"inference_ms": 10.0, "mask_count": 1}

    async def close(self):
        self.closed = True


@pytest.fixture
async def rig(monkeypatch):
    monkeypatch.setattr(server, "vlm_service", None)
    tracks = []

    async def create_track(url):
        number = int(urlparse(url).hostname.rsplit("-", 1)[1])
        track = MovingObjectCamera(number)
        tracks.append(track)
        return track

    monkeypatch.setattr(multi_camera.RTSPVideoTrack, "create", staticmethod(create_track))
    app = await server.create_app(test_mode=True)
    manager = app[multi_camera.MULTI_CAMERA_KEY]
    await manager.segmentation.close()
    fake = FakeSegmentation()
    manager.segmentation = fake
    async with TestClient(TestServer(app)) as client:
        try:
            yield SimpleNamespace(client=client, manager=manager, segmentation=fake, tracks=tracks)
        finally:
            fake.hold.set()
            fake.cancel_release.set()


def segmentation_body(count=2, *, vlm_enabled=False, api_base=None):
    body = session_body(count, source="rtsp", api_base=api_base)
    body["segmentation"] = {"enabled": True, "classes": ["person"], "opacity": 0.5}
    body["vlm_enabled"] = vlm_enabled
    if not vlm_enabled:
        del body["vlm"]
    return body


async def wait_until(predicate, timeout=5):
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("Expected camera state did not arrive")
        await asyncio.sleep(0.01)


async def read_events_until(ws, predicate, timeout=5):
    events = []
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate(events):
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Expected camera events did not arrive")
        events.append(await ws.receive_json(timeout=remaining))
    return events


@pytest.mark.parametrize("count", [2, 4])
async def test_segmentation_only_previews_match_each_camera_and_original_frame(rig, count):
    created = await create_session(rig.client, segmentation_body(count))
    assert created["vlm_enabled"] is False
    assert created["segmentation_enabled"] is True
    assert created["preview_mode"] == "segmented"
    cameras = rig.manager.sessions["test-session"].cameras
    assert all(camera.service is None for camera in cameras.values())
    for camera in cameras.values():
        camera.preview = multi_camera.CameraPreviewTrack()
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        frames = await asyncio.wait_for(
            asyncio.gather(*[camera.preview.recv() for camera in cameras.values()]), timeout=5
        )
        for number, frame in enumerate(frames, 1):
            original = rig.segmentation.frames[number, frame.pts]
            output = frame.to_ndarray(format="bgr24")
            mask = original[:, :, 0] > 200
            changed_pixels = np.any(output != original, axis=2)
            assert np.array_equal(changed_pixels, mask)
            assert np.array_equal(output[~mask], original[~mask])
            assert np.all(output[mask, 1] == 255)
            assert np.all(output[mask, 0] == 128)
            assert frame.time_base is not None
        events = await read_events_until(
            ws,
            lambda events: len(
                {event["camera_id"] for event in events if event["type"] == "segmentation_result"}
            )
            == count,
        )
        results = [event for event in events if event["type"] == "segmentation_result"]
        assert all(event["session_id"] == "test-session" for event in results)
        assert all(event["state"] == "running" for event in results)
        assert all(event["metrics"]["mask_count"] == 1 for event in results)
        assert all("preview" not in event for event in results)
        assert not any(event["type"] == "vlm_response" for event in events)
    status = await (await rig.client.get(f"{BASE}/test-session")).json()
    assert all(camera["segmentation"]["state"] == "running" for camera in status["cameras"])


async def test_slow_segmentation_keeps_one_newest_frame_per_camera(rig):
    rig.segmentation.hold.clear()
    await create_session(rig.client, segmentation_body(4))
    cameras = list(rig.manager.sessions["test-session"].cameras.values())
    await wait_until(lambda: len(rig.segmentation.calls) == 4)
    await wait_until(lambda: all(camera.segmentation_dropped >= 4 for camera in cameras))
    newest_before_release = {}
    for number, camera in enumerate(cameras, 1):
        assert camera.segmentation_queue.maxsize == 1
        assert camera.segmentation_queue.qsize() == 1
        queued = camera.segmentation_queue._queue[0].frame
        assert queued.pts == rig.tracks[number - 1].last_pts
        newest_before_release[number] = queued.pts
    assert len(rig.segmentation.calls) == 4
    rig.segmentation.hold.set()
    await wait_until(lambda: len(rig.segmentation.calls) >= 8)
    for number in range(1, 5):
        processed_pts = [pts for camera, pts in rig.segmentation.calls if camera == number]
        assert processed_pts[1] >= newest_before_release[number]
        assert processed_pts[1] - processed_pts[0] >= 12000


async def test_vlm_and_segmentation_results_remain_independent(rig, inference_api):
    await create_session(
        rig.client, segmentation_body(vlm_enabled=True, api_base=inference_api.api_base)
    )
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        events = await read_events_until(
            ws,
            lambda events: all(
                any(event["type"] == kind and event.get("camera_id") == camera for event in events)
                for kind in ("vlm_response", "segmentation_result")
                for camera in ("camera-1", "camera-2")
            ),
        )
    for event in events:
        if event["type"] == "vlm_response":
            assert event["text"].startswith(event["camera_id"] + ":brightness-")
            assert event["metrics"]["total_inferences"] >= 1
    assert len(inference_api.requests) >= 2
    # VLM inputs must remain original camera frames, without rendered green masks.
    import base64
    import io

    from PIL import Image

    for payload in inference_api.requests:
        content = payload["messages"][0]["content"]
        image_url = next(
            item["image_url"]["url"] for item in content if item["type"] == "image_url"
        )
        image = np.asarray(Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1]))))
        assert np.max(np.ptp(image.astype(int), axis=2)) <= 2


async def test_missing_segmentation_model_returns_503_without_leaking_session(rig):
    rig.segmentation.available = False
    status_response = await rig.client.get("/api/segmentation/status")
    assert status_response.status == 200
    assert (await status_response.json())["available"] is False
    response = await rig.client.post(BASE, json=segmentation_body())
    assert response.status == 503
    assert "missing" in (await response.json())["error"]
    assert not rig.manager.sessions
    assert not rig.tracks
    rig.segmentation.available = True
    await create_session(rig.client, segmentation_body())


async def test_segmentation_failure_is_tagged_and_preserves_vlm_and_other_cameras(
    rig, inference_api
):
    await create_session(
        rig.client, segmentation_body(vlm_enabled=True, api_base=inference_api.api_base)
    )
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        rig.segmentation.fail_camera = 1
        events = await read_events_until(
            ws,
            lambda events: (
                any(
                    event["type"] == "segmentation_result"
                    and event.get("camera_id") == "camera-1"
                    and event.get("state") == "failed"
                    for event in events
                )
                and any(
                    event["type"] == "segmentation_result"
                    and event.get("camera_id") == "camera-2"
                    and event.get("state") == "running"
                    for event in events
                )
                and all(
                    any(
                        event["type"] == "vlm_response" and event.get("camera_id") == camera
                        for event in events
                    )
                    for camera in ("camera-1", "camera-2")
                )
            ),
        )
    failed = next(event for event in events if event.get("state") == "failed")
    assert failed["session_id"] == "test-session"
    assert failed["camera_id"] == "camera-1"
    assert failed["error"]
    snapshot = await (await rig.client.get(f"{BASE}/test-session")).json()
    first, second = snapshot["cameras"]
    assert first["state"] == second["state"] == "streaming"
    assert first["segmentation"]["state"] == "failed"
    assert second["segmentation"]["state"] == "running"
    camera = rig.manager.sessions["test-session"].cameras["camera-1"]
    camera.preview = multi_camera.CameraPreviewTrack()
    fallback = await asyncio.wait_for(camera.preview.recv(), timeout=3)
    pixels = fallback.to_ndarray(format="bgr24")
    assert np.all(pixels[:, :, 0] == pixels[:, :, 1])


async def test_delete_waits_for_inflight_segmentation_cancellation(rig):
    rig.segmentation.hold.clear()
    rig.segmentation.cancel_release.clear()
    await create_session(rig.client, segmentation_body())
    await wait_until(lambda: len(rig.segmentation.calls) == 2)
    cameras = list(rig.manager.sessions["test-session"].cameras.values())
    request_task = asyncio.create_task(rig.client.delete(f"{BASE}/test-session"))
    try:
        await wait_until(lambda: len(rig.segmentation.cancelled) == 2)
        assert not request_task.done()
        assert "test-session" in rig.manager.sessions
        assert all(not camera.segmentation_task.done() for camera in cameras)
        rig.segmentation.cancel_release.set()
        response = await asyncio.wait_for(request_task, timeout=3)
        assert response.status == 200
        assert not rig.manager.sessions
        assert all(camera.segmentation_task.done() for camera in cameras)
        assert all(camera.segmentation_queue.empty() for camera in cameras)
        assert all(track.readyState == "ended" for track in rig.tracks)
    finally:
        rig.segmentation.cancel_release.set()
        await asyncio.gather(request_task, return_exceptions=True)


async def test_shutdown_closes_shared_segmentation_runtime(rig):
    await create_session(rig.client, segmentation_body())
    await rig.client.close()
    assert rig.segmentation.closed
    assert not rig.manager.sessions
    assert all(track.readyState == "ended" for track in rig.tracks)


@pytest.mark.parametrize(
    "setting",
    [
        {"enabled": "true"},
        {"confidence": 1.1},
        {"opacity": -0.1},
        {"classes": []},
        {"classes": ["person", "person"]},
        {"imgsz": 641},
        {"enabled": False},
    ],
)
async def test_reject_invalid_segmentation_configuration(rig, setting):
    body = segmentation_body()
    body["segmentation"].update(setting)
    response = await rig.client.post(BASE, json=body)
    assert response.status == 400
    assert not rig.manager.sessions
    assert not rig.tracks


async def test_segmentation_only_honors_each_camera_source_latency(rig, monkeypatch):
    wall_clock = [0.0]
    monkeypatch.setattr(
        video_processor,
        "time",
        SimpleNamespace(time=lambda: wall_clock[0], perf_counter=time.perf_counter),
    )
    release_stale = asyncio.Event()
    release_fresh = asyncio.Event()
    finished_frames = asyncio.Event()

    class TimestampedCamera(VideoStreamTrack):
        def __init__(self, number):
            super().__init__()
            self.number = number
            self.index = 0

        async def recv(self):
            if self.index == 1:
                await release_stale.wait()
            elif self.index == 2:
                await release_fresh.wait()
            elif self.index >= 3:
                await finished_frames.wait()
            pts = [0, 3000, 180000][self.index]
            self.index += 1
            pixels = np.full((48, 64, 3), self.number * 30, np.uint8)
            frame = VideoFrame.from_ndarray(pixels, format="bgr24")
            frame.pts, frame.time_base = pts, Fraction(1, 90000)
            return frame

    async def create_track(url):
        number = int(urlparse(url).hostname.rsplit("-", 1)[1])
        source = TimestampedCamera(number)
        rig.tracks.append(source)
        return source

    monkeypatch.setattr(multi_camera.RTSPVideoTrack, "create", staticmethod(create_track))
    body = segmentation_body()
    body["cameras"][0]["max_latency"] = 0.1
    body["cameras"][1]["max_latency"] = 0
    await create_session(rig.client, body)
    await wait_until(lambda: len(rig.segmentation.calls) == 2)
    cameras = list(rig.manager.sessions["test-session"].cameras.values())
    wall_clock[0] = 2.0
    release_stale.set()
    await wait_until(lambda: (2, 3000) in rig.segmentation.calls)
    assert (1, 3000) not in rig.segmentation.calls
    release_fresh.set()
    await wait_until(lambda: (1, 180000) in rig.segmentation.calls)
    status = await (await rig.client.get(f"{BASE}/test-session")).json()
    limited, unlimited = status["cameras"]
    assert limited["dropped_frames"] == 1
    assert unlimited["dropped_frames"] == 0
    assert limited["frames_received"] == 2
    assert all(camera.service is None for camera in cameras)
    assert all(camera.processor.last_frame is None for camera in cameras)
    assert all(camera.processor._inference_task is None for camera in cameras)


async def test_delete_cancels_vlm_before_waiting_for_segmentation_worker(rig, monkeypatch):
    inference_started = asyncio.Event()
    inference_cancelled = asyncio.Event()

    async def blocked_analysis(_service, _image, _prompt=None):
        inference_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            inference_cancelled.set()
            raise

    monkeypatch.setattr(multi_camera.VLMService, "analyze_image", blocked_analysis)
    rig.segmentation.hold.clear()
    rig.segmentation.cancel_release.clear()
    # Smooth mode admits raw VLM frames while segmentation is still blocked.
    body = segmentation_body(vlm_enabled=True)
    body["preview_mode"] = "smooth"
    await create_session(rig.client, body)
    await asyncio.wait_for(inference_started.wait(), timeout=3)
    await wait_until(lambda: len(rig.segmentation.calls) == 2)
    deleting = asyncio.create_task(rig.client.delete(f"{BASE}/test-session"))
    try:
        await wait_until(lambda: len(rig.segmentation.cancelled) == 2)
        assert not deleting.done()
        await asyncio.wait_for(inference_cancelled.wait(), timeout=1)
        rig.segmentation.cancel_release.set()
        assert (await asyncio.wait_for(deleting, timeout=3)).status == 200
    finally:
        rig.segmentation.cancel_release.set()
        await asyncio.gather(deleting, return_exceptions=True)


@pytest.mark.parametrize("mode", [None, True, 1, "", "live", "Smooth", {}, []])
async def test_preview_mode_rejects_invalid_values_before_opening_sources(rig, mode):
    body = segmentation_body()
    body["preview_mode"] = mode
    response = await rig.client.post(BASE, json=body)
    assert response.status == 400
    assert "preview_mode" in (await response.json())["error"]
    assert not rig.manager.sessions
    assert not rig.tracks


async def test_smooth_preview_continues_while_segmentation_waits_and_never_receives_old_masks(rig):
    rig.segmentation.hold.clear()
    body = segmentation_body()
    body["preview_mode"] = "smooth"
    created = await create_session(rig.client, body)
    assert created["preview_mode"] == "smooth"
    cameras = list(rig.manager.sessions["test-session"].cameras.values())
    received = {camera.camera_id: [] for camera in cameras}

    class RecordingPreview(multi_camera.CameraPreviewTrack):
        def __init__(self, camera_id):
            super().__init__()
            self.camera_id = camera_id

        def publish(self, frame):
            received[self.camera_id].append(frame)
            super().publish(frame)

    for camera in cameras:
        camera.preview = RecordingPreview(camera.camera_id)
    await wait_until(lambda: len(rig.segmentation.calls) == 2)
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        await wait_until(lambda: all(len(frames) >= 4 for frames in received.values()))
        assert not any(camera.segmentation_metrics for camera in cameras)
        rig.segmentation.hold.set()
        await read_events_until(
            ws,
            lambda events: all(
                any(
                    event.get("camera_id") == camera.camera_id and "preview" in event
                    for event in events
                )
                for camera in cameras
            ),
        )
        await wait_until(lambda: all(len(frames) >= 8 for frames in received.values()))
    for frames in received.values():
        assert all(second.pts > first.pts for first, second in pairwise(frames))
        for frame in frames:
            pixels = frame.to_ndarray(format="bgr24")
            # Original fixtures are grayscale; even one masked publication has
            # green pixels and fails this check, including transient overlays.
            assert np.array_equal(pixels[:, :, 0], pixels[:, :, 1])
            assert np.array_equal(pixels[:, :, 1], pixels[:, :, 2])
            assert frame.time_base == Fraction(1, 90000)
    snapshot = await (await rig.client.get(f"{BASE}/test-session")).json()
    assert snapshot["preview_mode"] == "smooth"


async def test_smooth_snapshot_is_the_matched_frame_with_bounded_size_and_real_age(
    rig, monkeypatch
):
    async def larger_camera(url):
        number = int(urlparse(url).hostname.rsplit("-", 1)[1])
        camera = MovingObjectCamera(number)
        original_recv = camera.recv

        async def recv():
            frame = await original_recv()
            return frame.reformat(width=960, height=720)

        camera.recv = recv
        rig.tracks.append(camera)
        return camera

    monkeypatch.setattr(multi_camera.RTSPVideoTrack, "create", staticmethod(larger_camera))
    encode_threads = []
    original_encode = multi_camera._encode_segmentation_preview

    def encode(frame):
        encode_threads.append(threading.get_ident())
        return original_encode(frame)

    monkeypatch.setattr(multi_camera, "_encode_segmentation_preview", encode)
    rig.segmentation.hold.clear()
    body = segmentation_body()
    body["preview_mode"] = "smooth"
    await create_session(rig.client, body)
    await wait_until(lambda: len(rig.segmentation.calls) == 2)
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        await asyncio.sleep(0.12)
        rig.segmentation.hold.set()
        events = await read_events_until(
            ws, lambda events: any("preview" in event for event in events)
        )
    event = next(event for event in events if "preview" in event)
    preview = event["preview"]
    assert preview["image"].startswith("data:image/jpeg;base64,")
    image = Image.open(io.BytesIO(base64.b64decode(preview["image"].split(",", 1)[1])))
    assert image.format == "JPEG"
    assert image.size == (320, 240) == (preview["width"], preview["height"])
    number = int(event["camera_id"].split("-")[-1])
    assert (number, preview["frame_pts"]) in rig.segmentation.frames
    assert preview["frame_time_base"] == "1/90000"
    assert preview["frame_age_ms"] >= 100
    assert preview["generated_unix_ms"] - preview["frame_received_unix_ms"] >= 100
    assert "_received_at" not in preview
    pixels = np.asarray(image).astype(int)
    assert np.any(
        (pixels[:, :, 1] > pixels[:, :, 0] + 40) & (pixels[:, :, 1] > pixels[:, :, 2] + 40)
    )
    assert encode_threads and all(thread != threading.get_ident() for thread in encode_threads)


async def test_smooth_snapshots_are_throttled_independently_for_each_camera(rig):
    body = segmentation_body()
    body["preview_mode"] = "smooth"
    await create_session(rig.client, body)
    async with rig.client.ws_connect(f"{BASE}/test-session/ws") as ws:
        events = await read_events_until(
            ws,
            lambda events: all(
                sum(event.get("camera_id") == camera_id and "preview" in event for event in events)
                >= 3
                for camera_id in ("camera-1", "camera-2")
            ),
        )
    for camera_id in ("camera-1", "camera-2"):
        snapshots = [
            event["preview"]
            for event in events
            if event.get("camera_id") == camera_id and "preview" in event
        ]
        assert all(
            second["generated_unix_ms"] - first["generated_unix_ms"] >= 199
            for first, second in pairwise(snapshots)
        )
        assert all(
            second["frame_pts"] > first["frame_pts"] for first, second in pairwise(snapshots)
        )
    assert any(
        event["type"] == "segmentation_result" and "preview" not in event for event in events
    )


async def test_smooth_mode_does_not_encode_without_subscribers(rig, monkeypatch):
    encoded = []

    def forbidden(frame):
        encoded.append(frame)
        raise AssertionError("No viewer should mean no analysis JPEG work")

    monkeypatch.setattr(multi_camera, "_encode_segmentation_preview", forbidden)
    body = segmentation_body()
    body["preview_mode"] = "smooth"
    await create_session(rig.client, body)
    await wait_until(lambda: len(rig.segmentation.calls) >= 6)
    assert not encoded
    assert all(
        camera.segmentation_state == "running"
        for camera in rig.manager.sessions["test-session"].cameras.values()
    )


async def test_delete_waits_for_snapshot_encoding_and_publishes_nothing_after_stop(
    rig, monkeypatch
):
    started = asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()
    original_encode = multi_camera._encode_segmentation_preview

    def blocked_encode(frame):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(timeout=5)
        return original_encode(frame)

    monkeypatch.setattr(multi_camera, "_encode_segmentation_preview", blocked_encode)
    body = segmentation_body()
    body["preview_mode"] = "smooth"
    await create_session(rig.client, body)
    session = rig.manager.sessions["test-session"]
    publications_after_stop = []
    original_publish = session.publish

    def record_publish(event):
        if session.state == "stopped":
            publications_after_stop.append(event)
        original_publish(event)

    session.publish = record_publish
    deleting = None
    try:
        async with rig.client.ws_connect(f"{BASE}/test-session/ws"):
            await asyncio.wait_for(started.wait(), timeout=2)
            deleting = asyncio.create_task(rig.client.delete(f"{BASE}/test-session"))
            await wait_until(lambda: session.state == "stopped")
            await asyncio.sleep(0.02)
            assert not deleting.done()
            assert any(not camera.segmentation_task.done() for camera in session.cameras.values())
            release.set()
            assert (await asyncio.wait_for(deleting, timeout=2)).status == 200
        assert not publications_after_stop
        assert all(camera.segmentation_task.done() for camera in session.cameras.values())
        assert all(camera.segmentation_queue.empty() for camera in session.cameras.values())
    finally:
        release.set()
        if deleting:
            await asyncio.gather(deleting, return_exceptions=True)


async def test_segmented_vlm_samples_only_completed_originals_and_never_stalls_video(
    rig, monkeypatch
):
    """A held model cannot admit raw frames or hold up the matched masked preview."""
    release_vlm = asyncio.Event()
    samples = []

    async def blocked_analysis(service, image, _prompt=None):
        samples.append(
            {
                "camera": service.prompt,
                "image": np.array(image),
                "identity": dict(service._frame_identity),
                "received_unix_ms": service._frame_received_unix_ms,
            }
        )
        await release_vlm.wait()
        service.total_inferences += 1
        return "Description of sampled source frame"

    class RecordedPreview(multi_camera.CameraPreviewTrack):
        def __init__(self):
            super().__init__()
            self.published = {}

        def publish(self, frame):
            self.published[frame.pts] = frame
            super().publish(frame)

    monkeypatch.setattr(multi_camera.VLMService, "analyze_image", blocked_analysis)
    rig.segmentation.hold.clear()
    await create_session(rig.client, segmentation_body(vlm_enabled=True))
    session = rig.manager.sessions["test-session"]
    events = asyncio.Queue(maxsize=128)
    session.subscribers.add(events)
    for camera in session.cameras.values():
        camera.preview = RecordedPreview()
    try:
        await wait_until(lambda: all(c.segmentation_dropped >= 3 for c in session.cameras.values()))
        assert len(rig.segmentation.calls) == 2
        assert samples == []
        assert all(not c.preview.published for c in session.cameras.values())
        assert all(c.processor._inference_task is None for c in session.cameras.values())

        rig.segmentation.hold.set()
        await wait_until(lambda: len(samples) == 2)
        await wait_until(
            lambda: all(len(c.preview.published) >= 4 for c in session.cameras.values())
        )
        # Both descriptions are still waiting, but each masked video has advanced.
        assert len(samples) == 2
        for sample in samples:
            number = int(sample["camera"].rsplit("-", 1)[1])
            camera = session.cameras[sample["camera"]]
            identity = sample["identity"]
            pts = identity["last_frame_pts"]
            original = rig.segmentation.frames[number, pts]
            masked = camera.preview.published[pts].to_ndarray("bgr24")
            np.testing.assert_array_equal(sample["image"], original[:, :, ::-1])
            np.testing.assert_array_equal(
                masked, composite_masks(original, original[:, :, 0] > 200, 0.5)
            )
            assert identity["last_frame_preview_mode"] == "segmented"
            assert identity["last_frame_time_base"] == str(camera.preview.published[pts].time_base)
            assert camera.frames_received > identity["last_frame_id"]
            assert max(camera.preview.published) > pts

        release_vlm.set()
        await wait_until(
            lambda: all(c.service.total_inferences >= 1 for c in session.cameras.values())
        )
        messages = []
        while not events.empty():
            messages.append(events.get_nowait())
        for sample in samples[:2]:
            identity = sample["identity"]
            result = next(
                event
                for event in messages
                if event["type"] == "vlm_response"
                and event["camera_id"] == sample["camera"]
                and event["metrics"]["last_frame_id"] == identity["last_frame_id"]
            )
            segmentation = next(
                event
                for event in messages
                if event["type"] == "segmentation_result"
                and event["camera_id"] == sample["camera"]
                and event["metrics"]["frame_id"] == identity["last_frame_id"]
            )
            metrics = result["metrics"]
            assert metrics["last_request_succeeded"]
            assert metrics["last_frame_pts"] == segmentation["metrics"]["frame_pts"]
            assert metrics["last_frame_time_base"] == segmentation["metrics"]["frame_time_base"]
            assert metrics["last_frame_received_unix_ms"] == sample["received_unix_ms"]
            assert (
                metrics["last_frame_received_unix_ms"]
                == segmentation["metrics"]["frame_received_unix_ms"]
            )
            assert metrics["last_e2e_latency_ms"] >= segmentation["metrics"]["frame_age_ms"]
    finally:
        release_vlm.set()
        rig.segmentation.hold.set()
        session.subscribers.discard(events)


async def run_mocked_segmentation_clock(monkeypatch, *, frames=26, stalled_render=None):
    """A native25 source continues replacing its sole slot during waits/work."""
    clock = [100.0]
    origin, period = clock[0], 0.04
    starts, frame_ids, sleeps, events = [], [], [], []
    camera = SimpleNamespace(
        camera_id="clock-camera",
        state="streaming",
        preview=None,
        processor=SimpleNamespace(submit_frame=lambda *_args, **_kwargs: None),
        segmentation_queue=asyncio.Queue(maxsize=1),
        segmentation_dropped=0,
    )
    session = SimpleNamespace(
        state="active",
        preview_mode="segmented",
        subscribers=set(),
        publish=events.append,
        segmentation=SimpleNamespace(target_fps=25),
    )
    produced = 0

    def produce_latest():
        nonlocal produced
        due = int((clock[0] - origin + 1e-9) / period) + 1
        while produced < due:
            produced += 1
            frame = VideoFrame.from_ndarray(np.zeros((4, 4, 3), np.uint8), format="bgr24")
            frame.pts, frame.time_base = produced * 3600, Fraction(1, 90000)
            received_at = origin + (produced - 1) * period
            if camera.segmentation_queue.full():
                camera.segmentation_queue.get_nowait()
                camera.segmentation_dropped += 1
            camera.segmentation_queue.put_nowait(
                multi_camera._SegmentationFrame(frame, received_at, received_at * 1000, produced)
            )

    async def late_sleep(delay):
        assert delay > 0
        sleeps.append(delay)
        clock[0] += delay + 0.002  # Consistent scheduler wake-up overshoot.
        produce_latest()

    async def render(frame, _config):
        starts.append(clock[0])
        frame_ids.append(frame.pts // 3600)
        if len(starts) == frames:
            raise asyncio.CancelledError
        clock[0] += 0.205 if len(starts) == stalled_render else 0.005
        produce_latest()
        return frame, {"mask_count": 1}

    produce_latest()
    monkeypatch.setattr(
        multi_camera,
        "time",
        SimpleNamespace(
            monotonic=lambda: clock[0], perf_counter=lambda: clock[0], time=lambda: clock[0]
        ),
    )
    monkeypatch.setattr(
        multi_camera,
        "asyncio",
        SimpleNamespace(sleep=late_sleep, CancelledError=asyncio.CancelledError),
    )
    manager = SimpleNamespace(segmentation=SimpleNamespace(render=render))
    with pytest.raises(asyncio.CancelledError):
        await multi_camera.MultiCameraManager._segment(manager, session, camera)
    return SimpleNamespace(
        starts=starts, frame_ids=frame_ids, sleeps=sleeps, events=events, camera=camera
    )


async def test_segmentation_deadlines_retain_25hz_across_repeated_late_wakeups(monkeypatch):
    result = await run_mocked_segmentation_clock(monkeypatch)
    # Only the first late wakeup shifts the phase by2ms. Later wakeups do not
    # compound that delay into a42ms period (23.8FPS).
    assert result.starts[-1] - result.starts[0] == pytest.approx(1.002)
    assert np.diff(result.starts[1:]) == pytest.approx([0.04] * 24)
    assert result.frame_ids == list(range(1, 27))
    assert result.camera.segmentation_dropped == 0
    assert len(result.events) == 25


async def test_segmentation_stall_resets_deadline_without_replay_or_catchup_burst(monkeypatch):
    result = await run_mocked_segmentation_clock(monkeypatch, frames=10, stalled_render=3)
    assert result.starts[3] - result.starts[2] == pytest.approx(0.205)
    # Inference resumes with the latest frame, skipping the old queued frames.
    assert result.frame_ids[3] - result.frame_ids[2] == 5
    assert result.camera.segmentation_dropped == 4
    assert len(set(result.frame_ids)) == len(result.frame_ids)
    # The next submission waits a full new period plus wake-up jitter, rather
    # than racing through old deadlines after the stall.
    assert result.starts[4] - result.starts[3] == pytest.approx(0.042)
    assert np.diff(result.starts[4:]) == pytest.approx([0.04] * 5)
    assert result.camera.segmentation_queue.maxsize == 1
