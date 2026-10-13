"""Exercise multi-camera HTTP, WebRTC, inference isolation, and resource ownership."""

import asyncio
import base64
import io
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from urllib.parse import urlparse

import numpy as np
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame
from PIL import Image

from live_vlm_webui import multi_camera, server

BASE = "/api/multi-camera/sessions"


class SyntheticCamera(VideoStreamTrack):
    """A real video track with recognizable images, without a physical camera."""

    def __init__(self, brightness=48, fail_after=None):
        super().__init__()
        self.brightness = brightness
        self.fail_after = fail_after
        self.frames_received = 0
        self._stopped = False

    async def recv(self):
        if self.fail_after is not None and self.frames_received >= self.fail_after:
            raise RuntimeError("synthetic camera disconnected")
        pts, time_base = await self.next_timestamp()
        self.frames_received += 1
        pixels = np.full((48, 64, 3), self.brightness, dtype=np.uint8)
        frame = VideoFrame.from_ndarray(pixels, format="rgb24")
        frame.pts = pts
        frame.time_base = time_base
        return frame

    def get_stats(self):
        return {"connected": not self._stopped, "frames_received": self.frames_received}

    def stop(self):
        self._stopped = True
        super().stop()


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(server, "vlm_service", None)
    app = await server.create_app(test_mode=True)
    async with TestClient(TestServer(app)) as test_client:
        yield test_client


@pytest.fixture
async def inference_api():
    """Run the production OpenAI client against a deterministic HTTP model server."""
    stats = SimpleNamespace(
        active=0,
        maximum=0,
        requests=[],
        arrivals=asyncio.Queue(),
        release=asyncio.Event(),
    )
    stats.release.set()

    async def complete(request):
        payload = await request.json()
        stats.requests.append(payload)
        stats.active += 1
        stats.maximum = max(stats.maximum, stats.active)
        stats.arrivals.put_nowait(payload)
        try:
            await stats.release.wait()
            await asyncio.sleep(0.08)
            content = payload["messages"][0]["content"]
            prompt = next(item["text"] for item in content if item["type"] == "text")
            image_url = next(
                item["image_url"]["url"] for item in content if item["type"] == "image_url"
            )
            pixels = Image.open(io.BytesIO(base64.b64decode(image_url.split(",", 1)[1])))
            brightness = round(float(np.asarray(pixels).mean()))
            return web.json_response(
                {
                    "id": "test-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": payload["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"{prompt}:brightness-{brightness}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            )
        finally:
            stats.active -= 1

    app = web.Application()
    app.router.add_post("/v1/chat/completions", complete)
    async with TestServer(app) as endpoint:
        stats.api_base = str(endpoint.make_url("/v1"))
        yield stats


@pytest.fixture
def camera_sources(monkeypatch):
    tracks = []

    async def make_track(url, **kwargs):
        host = urlparse(url).hostname
        if host == "unavailable":
            raise RuntimeError("synthetic connection failure")
        number = int(host.rsplit("-", 1)[1])
        track = SyntheticCamera(brightness=number * 40)
        tracks.append(track)
        return track

    monkeypatch.setattr(multi_camera.RTSPVideoTrack, "create", staticmethod(make_track))
    return tracks


def session_body(count=2, *, session_id="test-session", source="webrtc", api_base=None):
    body = {
        "session_id": session_id,
        "vlm": {"model": "test-model"},
        "process_every": 1,
        "cameras": [],
    }
    if api_base:
        body["vlm"]["api_base"] = api_base
    for number in range(1, count + 1):
        camera = {
            "camera_id": f"camera-{number}",
            "source": source,
            "vlm": {"prompt": f"camera-{number}"},
        }
        if source == "rtsp":
            camera["rtsp_url"] = f"rtsp://camera-{number}/stream"
        body["cameras"].append(camera)
    return body


async def create_session(client, body):
    response = await client.post(BASE, json=body)
    payload = await response.json()
    assert response.status == 201, payload
    return payload


async def receive_camera_results(ws, count):
    results = {}
    deadline = asyncio.get_running_loop().time() + 12
    while len(results) < count:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("Timed out waiting for every camera's inference result")
        event = await ws.receive_json(timeout=remaining)
        if event["type"] == "vlm_response" and ":brightness-" in event.get("text", ""):
            results[event["camera_id"]] = event
    return results


@pytest.mark.parametrize("count", [2, 4])
async def test_create_status_delete_webrtc_session(client, count):
    created = await create_session(client, session_body(count))
    assert created["session_id"] == "test-session"
    assert created["camera_count"] == count
    assert len(created["cameras"]) == count
    assert all(camera["state"] == "waiting" for camera in created["cameras"])

    response = await client.get(f"{BASE}/test-session")
    assert response.status == 200
    assert (await response.json())["camera_count"] == count
    response = await client.delete(f"{BASE}/test-session")
    assert response.status == 200
    response = await client.get(f"{BASE}/test-session")
    assert response.status == 404


@pytest.mark.parametrize("count", [0, 1, 3, 5])
async def test_reject_unsupported_camera_count(client, count):
    response = await client.post(BASE, json=session_body(count))
    assert response.status == 400


@pytest.mark.parametrize(
    "field,value",
    [
        ("camera_id", "../camera"),
        ("camera_id", ""),
        ("source", "file"),
        ("process_every", 0),
        ("process_every", 3601),
        ("process_every", True),
        ("max_latency", -1),
        ("max_latency", 11),
    ],
)
async def test_reject_invalid_camera_settings(client, field, value):
    body = session_body()
    body["cameras"][0][field] = value
    response = await client.post(BASE, json=body)
    assert response.status == 400


async def test_reject_duplicate_camera_ids_and_sessions(client):
    body = session_body()
    body["cameras"][1]["camera_id"] = body["cameras"][0]["camera_id"]
    response = await client.post(BASE, json=body)
    assert response.status == 400
    await create_session(client, session_body())
    response = await client.post(BASE, json=session_body())
    assert response.status == 409


@pytest.mark.parametrize("url", [None, "", "file:///tmp/camera", "http://camera/stream"])
async def test_rtsp_requires_rtsp_url(client, url):
    body = session_body(source="rtsp")
    body["cameras"][0]["rtsp_url"] = url
    response = await client.post(BASE, json=body)
    assert response.status == 400


async def test_missing_defaults_requires_explicit_model(client):
    body = session_body()
    del body["vlm"]
    response = await client.post(BASE, json=body)
    assert response.status == 503


async def test_snapshot_defaults_and_camera_overrides(client, monkeypatch):
    defaults = SimpleNamespace(
        model="default-model",
        api_base="http://localhost:8000/v1",
        api_key="private-key-that-must-not-be-returned",
        prompt="default prompt",
        max_tokens=128,
    )
    monkeypatch.setattr(server, "vlm_service", defaults)
    body = session_body()
    del body["vlm"]
    body["cameras"][1]["vlm"]["model"] = "second-model"
    created = await create_session(client, body)
    assert defaults.api_key not in str(created)
    manager = client.app[multi_camera.MULTI_CAMERA_KEY]
    cameras = manager.sessions[created["session_id"]].cameras
    first = cameras["camera-1"].service
    second = cameras["camera-2"].service
    assert first is not second
    assert first.model == "default-model"
    assert second.model == "second-model"
    assert first.prompt == "camera-1"
    assert second.prompt == "camera-2"
    defaults.model = "new-global-model"
    defaults.prompt = "new-global-prompt"
    assert first.model == "default-model"
    assert first.prompt == "camera-1"


@pytest.mark.parametrize("count", [2, 4])
async def test_rtsp_inference_and_results_are_per_camera(
    client, inference_api, camera_sources, count
):
    await create_session(
        client, session_body(count, source="rtsp", api_base=inference_api.api_base)
    )
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
        initial = await ws.receive_json(timeout=5)
        assert initial["type"] == "session_status"
        results = await receive_camera_results(ws, count)
        for number in range(1, count + 1):
            event = results[f"camera-{number}"]
            assert event["session_id"] == "test-session"
            assert event["text"] == f"camera-{number}:brightness-{number * 40}"
            assert event["metrics"]["total_inferences"] >= 1
        assert 1 <= inference_api.maximum <= 2
        status = await (await client.get(f"{BASE}/test-session")).json()
        for camera in status["cameras"]:
            assert camera["response"].startswith(camera["camera_id"] + ":brightness-")
    await client.delete(f"{BASE}/test-session")
    assert all(track.readyState == "ended" for track in camera_sources)


async def test_websockets_are_scoped_to_their_session(client, inference_api, camera_sources):
    for name in ("first", "second"):
        body = session_body(session_id=name, source="rtsp", api_base=inference_api.api_base)
        for camera in body["cameras"]:
            camera["vlm"]["prompt"] = f"{name}-{camera['camera_id']}"
        await create_session(client, body)
    async with (
        client.ws_connect(f"{BASE}/first/ws") as first,
        client.ws_connect(f"{BASE}/second/ws") as second,
    ):
        first_results, second_results = await asyncio.gather(
            receive_camera_results(first, 2), receive_camera_results(second, 2)
        )
        assert all(event["session_id"] == "first" for event in first_results.values())
        assert all(event["text"].startswith("first-") for event in first_results.values())
        assert all(event["session_id"] == "second" for event in second_results.values())
        assert all(event["text"].startswith("second-") for event in second_results.values())
        assert inference_api.maximum <= 2


@pytest.mark.parametrize("camera_counts", [(4,), (2, 4)], ids=["one-session", "shared-budget"])
async def test_edge_cameras_share_one_upstream_with_four_inflight_requests(
    monkeypatch, inference_api, camera_sources, camera_counts
):
    """Hold real HTTP requests to distinguish concurrency from sequential throughput."""
    monkeypatch.setattr(
        server,
        "vlm_service",
        SimpleNamespace(
            backend="edgellm",
            model="test-model",
            api_base=inference_api.api_base,
        ),
    )
    app = await server.create_app(test_mode=True)
    inference_api.release.clear()
    async with TestClient(TestServer(app)) as edge_client:
        try:
            async with AsyncExitStack() as stack:
                sockets = []
                for index, count in enumerate(camera_counts):
                    name = f"edge-{index}"
                    body = session_body(count, session_id=name, source="rtsp")
                    # All camera requests inherit the same Edge endpoint and model.
                    del body["vlm"]
                    for camera in body["cameras"]:
                        camera["vlm"]["prompt"] = f"{name}-{camera['camera_id']}"
                    await create_session(edge_client, body)
                    sockets.append(
                        await stack.enter_async_context(edge_client.ws_connect(f"{BASE}/{name}/ws"))
                    )

                manager = edge_client.app[multi_camera.MULTI_CAMERA_KEY]
                services = [
                    camera.service
                    for session in manager.sessions.values()
                    for camera in session.cameras.values()
                ]

                # Every camera must have offered work, including cameras awaiting
                # admission. Otherwise an upper-bound check could pass by accident.
                async def wait_for_all_cameras():
                    while not all(service.is_processing for service in services):
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(wait_for_all_cameras(), timeout=5)
                initial_requests = [
                    await asyncio.wait_for(inference_api.arrivals.get(), timeout=5)
                    for _ in range(4)
                ]
                assert inference_api.active == 4
                assert inference_api.maximum == 4
                assert (
                    len(
                        {
                            payload["messages"][0]["content"][0]["text"]
                            for payload in initial_requests
                        }
                    )
                    == 4
                )
                # Six cameras in two sessions still share four admissions. A
                # per-session semaphore would incorrectly send requests five/six.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(inference_api.arrivals.get(), timeout=0.15)

                inference_api.release.set()
                results_by_session = await asyncio.gather(
                    *(
                        receive_camera_results(ws, count)
                        for ws, count in zip(sockets, camera_counts)
                    )
                )
                for index, results in enumerate(results_by_session):
                    for number in range(1, camera_counts[index] + 1):
                        event = results[f"camera-{number}"]
                        assert event["session_id"] == f"edge-{index}"
                        assert event["text"] == (
                            f"edge-{index}-camera-{number}:brightness-{number * 40}"
                        )
                assert inference_api.maximum == 4
                assert all(service.api_base == inference_api.api_base for service in services)
                assert all(payload["model"] == "test-model" for payload in inference_api.requests)
                assert all(
                    payload["reuse_context"] is False and payload["cache_generated_tokens"] is False
                    for payload in inference_api.requests
                )
        finally:
            inference_api.release.set()

    assert not manager.sessions
    assert all(service.client.is_closed() for service in services)
    assert all(track.readyState == "ended" for track in camera_sources)


async def test_rtsp_creation_failure_rolls_back_all_resources(client, camera_sources):
    body = session_body(source="rtsp")
    body["cameras"][1]["rtsp_url"] = "rtsp://unavailable/stream"
    response = await client.post(BASE, json=body)
    assert response.status == 502
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions
    assert camera_sources
    assert all(track.readyState == "ended" for track in camera_sources)
    # The failed reservation must not block retrying the same session ID.
    await create_session(client, session_body())


async def test_application_shutdown_releases_rtsp_and_clients(
    client, inference_api, camera_sources
):
    await create_session(client, session_body(source="rtsp", api_base=inference_api.api_base))
    manager = client.app[multi_camera.MULTI_CAMERA_KEY]
    services = [camera.service for camera in manager.sessions["test-session"].cameras.values()]
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
        await receive_camera_results(ws, 2)
    await client.close()
    assert not manager.sessions
    assert all(track.readyState == "ended" for track in camera_sources)
    assert all(service.client.is_closed() for service in services)


async def test_unknown_session_and_camera_return_404(client):
    assert (await client.get(f"{BASE}/missing")).status == 404
    assert (await client.delete(f"{BASE}/missing")).status == 404
    await create_session(client, session_body())
    response = await client.post(
        f"{BASE}/test-session/cameras/missing/offer", json={"type": "offer", "sdp": ""}
    )
    assert response.status == 404


@asynccontextmanager
async def browser_camera(client, camera_id, brightness):
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    track = SyntheticCamera(brightness)
    peer.addTrack(track)
    try:
        await peer.setLocalDescription(await peer.createOffer())
        payload = {"sdp": peer.localDescription.sdp, "type": peer.localDescription.type}
        response = await client.post(f"{BASE}/test-session/cameras/{camera_id}/offer", json=payload)
        answer = await response.json()
        assert response.status == 200, answer
        assert answer["type"] == "answer"
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        yield payload
    finally:
        await peer.close()
        track.stop()


@pytest.mark.parametrize("count", [2, 4])
async def test_real_webrtc_cameras_infer_separately_and_reject_duplicate_offer(
    client, inference_api, count
):
    await create_session(client, session_body(count, api_base=inference_api.api_base))
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws, AsyncExitStack() as stack:
        offers = []
        for number in range(1, count + 1):
            offers.append(
                await stack.enter_async_context(
                    browser_camera(client, f"camera-{number}", number * 40)
                )
            )
        results = await receive_camera_results(ws, count)
        for number in range(1, count + 1):
            event = results[f"camera-{number}"]
            assert event["text"].startswith(f"camera-{number}:brightness-")
            brightness = int(event["text"].rsplit("-", 1)[1])
            assert abs(brightness - number * 40) <= 5
        response = await client.post(f"{BASE}/test-session/cameras/camera-1/offer", json=offers[0])
        assert response.status == 409


async def test_delete_cancels_inflight_inferences(client, camera_sources, monkeypatch):
    started = []
    cancelled = []
    two_started = asyncio.Event()
    hold_inference = asyncio.Event()

    async def blocked_analysis(service, image, prompt=None):
        started.append(service)
        if len(started) == 2:
            two_started.set()
        try:
            await hold_inference.wait()
        except asyncio.CancelledError:
            cancelled.append(service)
            raise
        return "unreachable until released"

    monkeypatch.setattr(multi_camera.VLMService, "analyze_image", blocked_analysis)
    await create_session(client, session_body(4, source="rtsp"))
    await asyncio.wait_for(two_started.wait(), timeout=5)
    await asyncio.sleep(0.1)
    assert len(started) == 2
    manager = client.app[multi_camera.MULTI_CAMERA_KEY]
    services = [camera.service for camera in manager.sessions["test-session"].cameras.values()]
    response = await client.delete(f"{BASE}/test-session")
    assert response.status == 200
    assert len(cancelled) == 2
    assert not manager.sessions
    assert all(track.readyState == "ended" for track in camera_sources)
    assert all(service.client.is_closed() for service in services)


async def test_one_camera_failure_leaves_other_camera_running(
    client, inference_api, camera_sources
):
    await create_session(client, session_body(source="rtsp", api_base=inference_api.api_base))
    camera_sources[0].fail_after = 6
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
        await receive_camera_results(ws, 2)
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            status = await (await client.get(f"{BASE}/test-session")).json()
            cameras = {camera["camera_id"]: camera for camera in status["cameras"]}
            if cameras["camera-1"]["state"] == "failed":
                break
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.03)
        previous_count = cameras["camera-2"]["metrics"]["total_inferences"]
        while True:
            event = await ws.receive_json(timeout=5)
            if (
                event["type"] == "vlm_response"
                and event["camera_id"] == "camera-2"
                and event["metrics"]["total_inferences"] > previous_count
            ):
                assert event["text"] == "camera-2:brightness-80"
                break


@pytest.mark.parametrize("body", [[], None, {"cameras": "two"}])
async def test_reject_non_object_or_malformed_session_body(client, body):
    response = await client.post(BASE, json=body)
    assert response.status == 400


async def test_invalid_offer_leaves_camera_available_for_valid_offer(client, inference_api):
    await create_session(client, session_body(api_base=inference_api.api_base))
    response = await client.post(
        f"{BASE}/test-session/cameras/camera-1/offer",
        json={"type": "offer", "sdp": "not valid SDP"},
    )
    assert response.status == 400
    async with (
        browser_camera(client, "camera-1", 40),
        client.ws_connect(f"{BASE}/test-session/ws") as ws,
    ):
        results = await receive_camera_results(ws, 1)
        assert results["camera-1"]["text"].startswith("camera-1:brightness-")


async def test_rtsp_inference_continues_after_preview_disconnect(
    client, inference_api, camera_sources
):
    await create_session(client, session_body(source="rtsp", api_base=inference_api.api_base))
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTransceiver("video", direction="recvonly")
    preview_frame = asyncio.get_running_loop().create_future()
    reader_tasks = []

    @peer.on("track")
    def on_track(track):
        async def read_preview():
            try:
                frame = await track.recv()
                if not preview_frame.done():
                    preview_frame.set_result(frame)
            except (MediaStreamError, RuntimeError) as exc:
                if not preview_frame.done():
                    preview_frame.set_exception(exc)

        reader_tasks.append(asyncio.create_task(read_preview()))

    try:
        await peer.setLocalDescription(await peer.createOffer())
        response = await client.post(
            f"{BASE}/test-session/cameras/camera-1/offer",
            json={"sdp": peer.localDescription.sdp, "type": "offer"},
        )
        answer = await response.json()
        assert response.status == 200, answer
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=answer["sdp"], type=answer["type"])
        )
        frame = await asyncio.wait_for(preview_frame, timeout=10)
        assert frame.width == 64
        assert frame.height == 48
        await peer.close()
        async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
            results = await receive_camera_results(ws, 2)
            assert results["camera-1"]["text"] == "camera-1:brightness-40"
            assert results["camera-2"]["text"] == "camera-2:brightness-80"
        status = await (await client.get(f"{BASE}/test-session")).json()
        assert all(camera["state"] == "streaming" for camera in status["cameras"])
        assert all(track.readyState == "live" for track in camera_sources)
    finally:
        await peer.close()
        for task in reader_tasks:
            task.cancel()
        await asyncio.gather(*reader_tasks, return_exceptions=True)
