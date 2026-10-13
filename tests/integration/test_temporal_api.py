"""Temporal sessions exercise HTTP payloads and lifecycle without a model or GPU."""

import asyncio
import base64
import io
from fractions import Fraction
from types import SimpleNamespace
from urllib.parse import urlparse

import av
import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiortc import VideoStreamTrack
from PIL import Image

from live_vlm_webui import multi_camera, server, video_processor
from live_vlm_webui.temporal_buffer import TemporalConfig
from live_vlm_webui.vlm_service import VLMService

BASE = "/api/multi-camera/sessions"


async def wait_until(predicate, timeout=3):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


async def event_of_type(ws, kind):
    async with asyncio.timeout(3):
        while True:
            event = await ws.receive_json()
            if event["type"] == kind:
                return event


class QueuedSource(VideoStreamTrack):
    def __init__(self, number):
        super().__init__()
        self.number = number
        self.queue = asyncio.Queue()
        self._stopped = False

    def stop(self):
        self._stopped = True
        super().stop()

    async def recv(self):
        return await self.queue.get()

    def get_stats(self):
        return {"connected": True, "codec": "synthetic", "width": 32, "height": 24, "fps": 2}

    def put(self, number):
        pixels = np.full((24, 32, 3), self.number * 40 + number, dtype=np.uint8)
        frame = av.VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts, frame.time_base = number, Fraction(1, 2)
        self.queue.put_nowait(frame)
        return frame


@pytest.fixture
async def rig(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        video_processor,
        "time",
        SimpleNamespace(perf_counter=lambda: clock[0], time=lambda: clock[0] + 100000),
    )
    tracks = []

    async def create_source(url):
        number = int(urlparse(url).hostname.rsplit("-", 1)[1])
        source = QueuedSource(number)
        tracks.append(source)
        return source

    monkeypatch.setattr(multi_camera.RTSPVideoTrack, "create", staticmethod(create_source))
    monkeypatch.setattr(server, "vlm_service", None)
    app = await server.create_app(test_mode=True)
    manager = app[multi_camera.MULTI_CAMERA_KEY]
    manager.semaphore = asyncio.Semaphore(4)
    async with TestClient(TestServer(app)) as client:
        yield SimpleNamespace(client=client, app=app, manager=manager, tracks=tracks, clock=clock)


@pytest.fixture
async def model_api():
    state = SimpleNamespace(requests=[], active=0, maximum=0, release=asyncio.Event())
    state.release.set()

    async def complete(request):
        body = await request.json()
        content = body["messages"][0]["content"]
        images = [item["image_url"]["url"] for item in content if item["type"] == "image_url"]
        decoded = [Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) for url in images]
        colors = [image.getpixel((0, 0)) for image in decoded]
        prompt = content[0]["text"].rsplit("\n", 1)[-1]
        state.requests.append({"body": body, "colors": colors, "prompt": prompt})
        state.active += 1
        state.maximum = max(state.maximum, state.active)
        try:
            await state.release.wait()
            return web.json_response(
                {
                    "id": "temporal-test",
                    "object": "chat.completion",
                    "created": 0,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": prompt},
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
        finally:
            state.active -= 1

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    async with TestServer(app) as api:
        state.api_base = str(api.make_url("/v1"))
        try:
            yield state
        finally:
            state.release.set()


def body(count=2, *, api_base="http://unused.invalid/v1", source="webrtc"):
    return {
        "session_id": "temporal-test",
        "process_every": 1,
        "vlm": {"model": "synthetic", "api_base": api_base},
        "temporal": {"enabled": True, "window_seconds": 2},
        "cameras": [
            {
                "camera_id": f"camera-{index}",
                "source": source,
                "vlm": {"prompt": f"camera-{index} action"},
                **({"rtsp_url": f"rtsp://camera-{index}/stream"} if source == "rtsp" else {}),
            }
            for index in range(1, count + 1)
        ],
    }


async def create(rig, config):
    response = await rig.client.post(BASE, json=config)
    payload = await response.json()
    assert response.status == 201, payload
    return payload


async def feed(rig, cameras, number, *, segmented=False):
    rig.clock[0] = 100 + (number - 1) * 0.5
    for camera in cameras:
        camera.track.put(number)
    await wait_until(lambda: all(camera.processor.frame_count >= number for camera in cameras))
    if segmented:
        await wait_until(
            lambda: all(
                camera.segmentation_metrics.get("frame_id", 0) >= number for camera in cameras
            )
        )


async def test_temporal_defaults_and_camera_overrides_are_snapshot_independent(rig):
    config = body()
    config["temporal"] = {"enabled": True, "window_seconds": 4}
    config["cameras"][1]["temporal"] = {"window_seconds": 8}
    created = await create(rig, config)
    assert [item["temporal"]["window_seconds"] for item in created["cameras"]] == [4, 8]
    assert all(item["temporal_enabled"] for item in created["cameras"])
    assert all(item["temporal_buffer_frames"] == 0 for item in created["cameras"])
    assert not any(item["temporal_buffer_ready"] for item in created["cameras"])
    config["temporal"]["window_seconds"] = 2
    response = await rig.client.get(f"{BASE}/temporal-test")
    assert [item["temporal"]["window_seconds"] for item in (await response.json())["cameras"]] == [
        4,
        8,
    ]


@pytest.mark.parametrize(
    "invalid",
    [
        [],
        True,
        {"enabled": "true"},
        {"window_seconds": 3},
        {"window_seconds": 16},
        {"sample_fps": 4},
        {"unexpected": 1},
    ],
)
@pytest.mark.parametrize("per_camera", [False, True])
async def test_invalid_temporal_settings_fail_before_resources_open(rig, invalid, per_camera):
    config = body(source="rtsp")
    target = config["cameras"][0] if per_camera else config
    target["temporal"] = invalid
    response = await rig.client.post(BASE, json=config)
    assert response.status == 400
    assert not rig.tracks
    assert not rig.manager.sessions


@pytest.mark.parametrize("count", [2, 4])
async def test_busy_temporal_sessions_keep_camera_identity_and_delete_clears_buffers(
    rig, model_api, count
):
    model_api.release.clear()
    await create(rig, body(count, source="rtsp", api_base=model_api.api_base))
    session = rig.manager.sessions["temporal-test"]
    cameras = list(session.cameras.values())
    async with rig.client.ws_connect(f"{BASE}/temporal-test/ws") as ws:
        for number in range(1, 5):
            await feed(rig, cameras, number)
        await wait_until(lambda: len(model_api.requests) == count)
        for number in range(5, 13):
            await feed(rig, cameras, number)
        assert len(model_api.requests) == count
        assert model_api.maximum == count
        for index, camera in enumerate(cameras, 1):
            samples = camera.processor.temporal_buffer.snapshot()
            assert [sample.frame_id for sample in samples] == [9, 10, 11, 12]
            assert [sample.image.getpixel((0, 0))[0] for sample in samples] == [
                index * 40 + number for number in range(9, 13)
            ]
        for request in model_api.requests:
            index = int(request["prompt"].split()[0].rsplit("-", 1)[1])
            assert len(request["colors"]) == 4
            for number, color in enumerate(request["colors"], 1):
                assert color == pytest.approx((index * 40 + number,) * 3, abs=2)
        model_api.release.set()
        results = {}
        while len(results) < count:
            event = await event_of_type(ws, "vlm_response")
            results[event["camera_id"]] = event
        assert set(results) == set(session.cameras)
        for camera_id, event in results.items():
            assert event["text"] == f"{camera_id} action"
            assert event["metrics"]["last_frame_id"] == 4
        old_processors = [camera.processor for camera in cameras]
        response = await rig.client.delete(f"{BASE}/temporal-test")
        assert response.status == 200
    assert all(not track.temporal_buffer.samples for track in old_processors)
    assert all(track.readyState == "ended" for track in old_processors)
    assert all(camera.service.client.is_closed() for camera in cameras)
    assert all(source.readyState == "ended" for source in rig.tracks)
    restarted = await create(rig, body(count, source="rtsp", api_base=model_api.api_base))
    assert all(item["temporal_buffer_frames"] == 0 for item in restarted["cameras"])
    assert all(item["metrics"]["total_inferences"] == 0 for item in restarted["cameras"])


async def test_segmented_temporal_windows_use_exact_originals_not_green_overlays(rig, model_api):
    class Segmenter:
        async def prepare(self, _config):
            pass

        async def close(self):
            pass

        async def render(self, source, _config):
            output = av.VideoFrame.from_ndarray(
                np.full((24, 32, 3), (0, 255, 0), np.uint8), format="rgb24"
            )
            output.pts, output.time_base = source.pts, source.time_base
            return output, {"mask_count": 1, "inference_ms": 1}

    await rig.manager.segmentation.close()
    rig.manager.segmentation = Segmenter()
    config = body(source="rtsp", api_base=model_api.api_base)
    config["segmentation"] = {"enabled": True, "classes": ["person"]}
    config["preview_mode"] = "segmented"
    await create(rig, config)
    cameras = list(rig.manager.sessions["temporal-test"].cameras.values())
    for camera in cameras:
        assert camera.processor.process_on_recv is False
        camera.preview = multi_camera.CameraPreviewTrack()
    for number in range(1, 5):
        await feed(rig, cameras, number, segmented=True)
    await wait_until(lambda: len(model_api.requests) == 2)
    for request in model_api.requests:
        assert len(request["colors"]) == 4
        assert all(abs(color[0] - color[1]) <= 2 for color in request["colors"])
        assert all(color[1] < 200 for color in request["colors"])
    for camera in cameras:
        preview = await camera.preview.recv()
        assert tuple(preview.to_ndarray(format="rgb24")[0, 0]) == (0, 255, 0)
        samples = camera.processor.temporal_buffer.snapshot()
        assert samples[-1].frame_pts == preview.pts == 4


async def test_single_stream_clones_service_scopes_results_and_cleans_up(rig, monkeypatch):
    defaults = VLMService("synthetic", api_base="http://unused.invalid/v1", prompt="original")
    monkeypatch.setattr(server, "vlm_service", defaults)
    request = SimpleNamespace(app=rig.app)
    first = server._single_processor(
        request, QueuedSource(1), TemporalConfig(enabled=True), "first"
    )
    second = server._single_processor(
        request, QueuedSource(2), TemporalConfig(enabled=True), "second"
    )
    try:
        assert first.vlm_service is not second.vlm_service
        assert first.vlm_service is not defaults
        assert first.vlm_service._semaphore is second.vlm_service._semaphore
        async with (
            rig.client.ws_connect("/ws?stream_id=first") as one,
            rig.client.ws_connect("/ws?stream_id=second") as two,
        ):
            await event_of_type(one, "temporal_config")
            await event_of_type(two, "temporal_config")
            first.text_callback("first-only", {"total_inferences": 1})
            second.text_callback("second-only", {"total_inferences": 1})
            assert (await event_of_type(one, "vlm_response"))["text"] == "first-only"
            assert (await event_of_type(two, "vlm_response"))["text"] == "second-only"
            await one.send_json({"type": "update_prompt", "prompt": "first question"})
            assert (await event_of_type(one, "prompt_updated"))["prompt"] == "first question"
            assert first.vlm_service.prompt == "first question"
            assert second.vlm_service.prompt == "original"
            await one.send_json({"type": "update_temporal", "temporal": {"enabled": True}})
            assert "Stop" in (await event_of_type(one, "error"))["error"]
            await server._close_single_processor(rig.app, first)
            assert first.vlm_service.client.is_closed()
            assert not second.vlm_service.client.is_closed()
            assert not defaults.client.is_closed()
            await one.send_json(
                {"type": "update_temporal", "temporal": {"enabled": True, "window_seconds": 8}}
            )
            assert (await event_of_type(one, "temporal_updated"))["temporal"]["window_seconds"] == 8
            assert second.temporal.window_seconds == 4
    finally:
        await server._close_single_processor(rig.app, first)
        await server._close_single_processor(rig.app, second)
        await defaults.client.close()
    assert not rig.app[server.SINGLE_PROCESSORS_KEY]


async def test_single_rtsp_stop_and_restart_discard_previous_visual_context(rig, monkeypatch):
    defaults = VLMService("synthetic", api_base="http://unused.invalid/v1")
    monkeypatch.setattr(server, "vlm_service", defaults)
    monkeypatch.setattr(server, "RTSPVideoTrack", lambda _url: QueuedSource(1))
    config = {
        "rtsp_url": "rtsp://camera-1/stream",
        "session_id": "single-temporal",
        "stream_id": "single-owner",
        "temporal": {"enabled": True},
    }
    try:
        response = await rig.client.post("/api/rtsp/start", json=config)
        assert response.status == 200, await response.text()
        source, original, _ = server.rtsp_tracks["single-temporal"]
        source.put(1)
        await wait_until(lambda: len(original.temporal_buffer.samples) == 1)
        response = await rig.client.post("/api/rtsp/start", json=config)
        assert response.status == 200, await response.text()
        _, replacement, _ = server.rtsp_tracks["single-temporal"]
        assert replacement is not original
        assert not original.temporal_buffer.samples
        assert original.readyState == "ended"
        assert original.vlm_service.client.is_closed()
        assert not replacement.temporal_buffer.samples
        response = await rig.client.post("/api/rtsp/stop", json={"session_id": "single-temporal"})
        assert response.status == 200
        assert replacement.vlm_service.client.is_closed()
        assert not rig.app[server.SINGLE_PROCESSORS_KEY]
    finally:
        await server._stop_rtsp_session("single-temporal")
        await defaults.client.close()


async def test_single_prompt_change_invalidates_busy_window_without_waiting_for_next_frame(
    rig, model_api, monkeypatch
):
    defaults = VLMService("synthetic", api_base=model_api.api_base)
    monkeypatch.setattr(server, "vlm_service", defaults)
    track = server._single_processor(
        SimpleNamespace(app=rig.app),
        QueuedSource(1),
        TemporalConfig(enabled=True, window_seconds=2),
        "settings-owner",
    )
    track.process_every_n_frames = 1
    model_api.release.clear()
    try:
        for number in range(1, 5):
            original = track.track.put(number)
            track.submit_frame(
                original,
                received_at=100 + number * 0.5,
                received_unix_ms=100000 + number * 500,
                frame_id=number,
            )
        await wait_until(lambda: len(model_api.requests) == 1)
        running = track._inference_task
        async with rig.client.ws_connect("/ws?stream_id=settings-owner") as ws:
            await event_of_type(ws, "temporal_config")
            await ws.send_json({"type": "update_prompt", "prompt": "new question"})
            await event_of_type(ws, "prompt_updated")
            await asyncio.sleep(0)
            assert not track.temporal_buffer.samples
            await wait_until(running.done)
            assert running.cancelled()
            assert track.vlm_service.prompt == "new question"
            assert track.vlm_service.total_inferences == 0
    finally:
        model_api.release.set()
        await server._close_single_processor(rig.app, track)
        await defaults.client.close()


async def test_legacy_broadcast_scope_cannot_overwrite_named_streams(rig):
    async with (
        rig.client.ws_connect("/ws?stream_id=first") as first,
        rig.client.ws_connect("/ws?stream_id=second") as second,
        rig.client.ws_connect("/ws") as legacy,
    ):
        for ws in (first, second, legacy):
            await event_of_type(ws, "temporal_config")
        # Send legacy first so an incorrectly broad broadcast visibly arrives
        # before either named viewer's own result or buffer status.
        for owner, text in (
            (None, "legacy-only"),
            ("first", "first-only"),
            ("second", "second-only"),
        ):
            server.broadcast_text_update(text, {"total_inferences": 1}, stream_id=owner)
            server._broadcast_stream_message({"type": "temporal_status", "owner": text}, owner)
        for ws, expected in (
            (first, "first-only"),
            (second, "second-only"),
            (legacy, "legacy-only"),
        ):
            assert (await event_of_type(ws, "vlm_response"))["text"] == expected
            assert (await event_of_type(ws, "temporal_status"))["owner"] == expected


@pytest.mark.parametrize("phase", ["rtsp_setup", "negotiation"])
async def test_single_offer_cancellation_owns_cleanup_through_repeated_cancellation(
    rig, monkeypatch, phase
):
    entered, cleanup_entered, cleanup_release = (asyncio.Event() for _ in range(3))
    defaults = VLMService("synthetic", api_base="http://unused.invalid/v1")
    monkeypatch.setattr(server, "vlm_service", defaults)
    source = QueuedSource(1)
    source.source_generation = 1
    peers = []

    async def close_source():
        cleanup_entered.set()
        await cleanup_release.wait()
        source.stop()

    source.aclose = close_source

    def open_source(_url):
        entered.set()
        return source

    class Peer:
        def __init__(self, **_kwargs):
            self.handlers = {}
            self.tracks = []
            self.connectionState = "new"
            self.close_calls = 0
            peers.append(self)

        def on(self, name):
            def register(callback):
                self.handlers[name] = callback
                return callback

            return register

        def addTrack(self, track):
            self.tracks.append(track)

        async def setRemoteDescription(self, _offer):
            if phase == "negotiation":
                self.handlers["track"](source)
                entered.set()
            await asyncio.Event().wait()

        async def close(self):
            self.close_calls += 1
            if phase == "negotiation":
                # Hold teardown after the processor/API client has closed.
                cleanup_entered.set()
                await cleanup_release.wait()
            self.connectionState = "closed"
            source.stop()
            # A synchronous callback emulation is stricter than aiortc's emitter;
            # cleanup must not re-enter itself even here.
            await self.handlers["connectionstatechange"]()

    monkeypatch.setattr(server, "RTCPeerConnection", Peer)
    monkeypatch.setattr(server, "RTSPVideoTrack", open_source)
    payload = {
        "sdp": "v=0",
        "type": "offer",
        "stream_id": "cancel-owner",
        "temporal": {"enabled": True},
    }
    if phase == "rtsp_setup":
        payload["rtsp_url"] = "rtsp://camera-1/stream"

    async def request_json():
        return payload

    request = SimpleNamespace(app=rig.app, json=request_json)
    task = asyncio.create_task(server.offer(request))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        processors = list(rig.app[server.SINGLE_PROCESSORS_KEY])
        if phase == "negotiation":
            assert len(processors) == 1
            assert not processors[0].vlm_service.client.is_closed()
        else:
            assert not processors  # Cancel before the initial setup sleep ends.
        task.cancel()
        await asyncio.wait_for(cleanup_entered.wait(), 1)
        if phase == "negotiation":
            late_source = QueuedSource(2)
            peers[0].handlers["track"](late_source)
            assert late_source.readyState == "ended"
            assert not rig.app[server.SINGLE_PROCESSORS_KEY]
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 1)
        assert peers[0].close_calls == 1
        assert peers[0] not in server.pcs
        assert not rig.app[server.SINGLE_PROCESSORS_KEY]
        assert source.readyState == "ended"
        assert all(processor.vlm_service.client.is_closed() for processor in processors)
    finally:
        cleanup_release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        for peer in peers:
            server.pcs.discard(peer)
        await defaults.client.close()
