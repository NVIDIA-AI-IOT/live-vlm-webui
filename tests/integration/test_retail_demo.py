"""Approved recorded clips through the real session, replay and preview APIs."""

import asyncio
from contextlib import asynccontextmanager

import av
import numpy as np
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from live_vlm_webui import multi_camera
from live_vlm_webui.demo_catalog import CLIPS, resolve_demo
from live_vlm_webui.local_video_track import LocalVideoTrack
from tests.integration import test_multi_camera as camera_tests
from tests.integration.test_multi_camera_segmentation import FakeSegmentation, wait_until

client = camera_tests.client
inference_api = camera_tests.inference_api
BASE = camera_tests.BASE


def write_demo(path, brightness, rate=30):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("mpeg4", rate=rate)
        stream.width = 64
        stream.height = 48
        stream.pix_fmt = "yuv420p"
        for _ in range(6):
            image = np.full((48, 64, 3), brightness, dtype=np.uint8)
            frame = av.VideoFrame.from_ndarray(image, format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)


@pytest.fixture
def demo_dir(tmp_path, monkeypatch):
    directory = tmp_path / "retail-demo"
    directory.mkdir()
    monkeypatch.setenv("LIVE_VLM_DEMO_DIR", str(directory))
    return directory


@pytest.fixture
def installed_demos(demo_dir):
    for number, (demo_id, (filename, _)) in enumerate(CLIPS.items()):
        write_demo(
            demo_dir / filename,
            (number % 4 + 1) * 40,
            rate=25 if demo_id.startswith("store-25-") else 30,
        )
    return demo_dir


def demo_body(count=2, *, api_base=None, prefix="retail-"):
    body = camera_tests.session_body(count, source="demo", api_base=api_base)
    for number, camera in enumerate(body["cameras"], 1):
        camera["demo_id"] = f"{prefix}{number}"
    return body


@asynccontextmanager
async def preview(client, camera_id="camera-1"):
    peer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    peer.addTransceiver("video", direction="recvonly")
    first_frame = asyncio.get_running_loop().create_future()
    readers = []

    @peer.on("track")
    def on_track(track):
        async def consume():
            try:
                while True:
                    frame = await track.recv()
                    if not first_frame.done():
                        first_frame.set_result(frame)
            except MediaStreamError:
                if not first_frame.done():
                    first_frame.set_exception(MediaStreamError("Preview closed before first frame"))

        readers.append(asyncio.create_task(consume()))

    try:
        await peer.setLocalDescription(await peer.createOffer())
        response = await client.post(
            f"{BASE}/test-session/cameras/{camera_id}/offer",
            json={"type": "offer", "sdp": peer.localDescription.sdp},
        )
        payload = await response.json()
        assert response.status == 200, payload
        await peer.setRemoteDescription(
            RTCSessionDescription(sdp=payload["sdp"], type=payload["type"])
        )
        yield await asyncio.wait_for(first_frame, timeout=5)
    finally:
        await peer.close()
        for reader in readers:
            reader.cancel()
        await asyncio.gather(*readers, return_exceptions=True)


async def test_catalog_lists_only_approved_videos_with_attribution(client, installed_demos):
    response = await client.get("/api/demo/videos")
    assert response.status == 200
    catalog = await response.json()
    assert catalog["max_fps"] == 25
    assert [video["demo_id"] for video in catalog["videos"]] == list(CLIPS)
    assert len(catalog["videos"]) == 8
    assert [video["demo_id"] for video in catalog["videos"][:4]] == [
        f"store-25-{number}" for number in range(1, 5)
    ]
    for video in catalog["videos"]:
        assert video["available"] is True
        assert video["title"]
        assert video["attribution"]
        assert video["source_url"].startswith("https://")
        if video["demo_id"].startswith("store-25-"):
            assert video["license"] == "Pexels License"
            assert video["license_url"] == "https://www.pexels.com/license/"
            assert video["source_fps"] == 25
            assert video["preferred_for_realtime"] is True
            assert video["attribution"] == "Kampus Production via Pexels"
        else:
            assert video["license"] == "CC BY-SA 4.0"
            assert video["license_url"] == "https://creativecommons.org/licenses/by-sa/4.0/"
            assert video["source_fps"] == 24
            assert video["preferred_for_realtime"] is False
        assert "path" not in video
    assert str(installed_demos) not in str(catalog)


async def test_missing_demo_returns_503_without_reserving_a_session(client, demo_dir):
    catalog = await (await client.get("/api/demo/videos")).json()
    assert not any(video["available"] for video in catalog["videos"])
    response = await client.post(BASE, json=demo_body())
    assert response.status == 503
    assert "not installed" in await response.text()
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions


@pytest.mark.parametrize("bad_id", [None, "retail-5", "../clip1.mp4", "clip1.mp4", 1])
async def test_reject_unknown_or_path_like_demo_identifiers(client, installed_demos, bad_id):
    body = demo_body()
    body["cameras"][0]["demo_id"] = bad_id
    response = await client.post(BASE, json=body)
    assert response.status == 400
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions


async def test_existing_absolute_path_and_file_url_are_not_demo_identifiers(
    client, installed_demos
):
    path = installed_demos / CLIPS["retail-1"][0]
    assert path.is_file()
    for value in (str(path), path.as_uri(), "../retail-demo/clip1.mp4"):
        body = demo_body()
        body["cameras"][0]["demo_id"] = value
        response = await client.post(BASE, json=body)
        assert response.status == 400
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions


async def test_catalog_rejects_an_approved_filename_symlink_outside_demo_directory(
    client, demo_dir, tmp_path
):
    outside = tmp_path / "outside.mp4"
    write_demo(outside, 40)
    (demo_dir / CLIPS["retail-1"][0]).symlink_to(outside)
    with pytest.raises(FileNotFoundError):
        resolve_demo("retail-1")
    catalog = await (await client.get("/api/demo/videos")).json()
    first = next(video for video in catalog["videos"] if video["demo_id"] == "retail-1")
    assert first["available"] is False
    response = await client.post(BASE, json=demo_body())
    assert response.status == 503
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions


@pytest.mark.parametrize("count", [2, 4])
@pytest.mark.parametrize("prefix", ["retail-", "store-25-"])
async def test_recorded_sessions_loop_and_infer_independently(
    client, installed_demos, inference_api, count, prefix
):
    created = await camera_tests.create_session(
        client, demo_body(count, api_base=inference_api.api_base, prefix=prefix)
    )
    assert created["camera_count"] == count
    cameras = client.app[multi_camera.MULTI_CAMERA_KEY].sessions["test-session"].cameras
    for number, camera in enumerate(cameras.values(), 1):
        assert isinstance(camera.track, LocalVideoTrack)
        assert camera.track.path == resolve_demo(f"{prefix}{number}")
        if prefix == "store-25-":
            assert float(camera.track._source_period) == 1 / 25
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
        results = await camera_tests.receive_camera_results(ws, count)
        for number in range(1, count + 1):
            event = results[f"camera-{number}"]
            assert event["text"].startswith(f"camera-{number}:brightness-")
            brightness = int(event["text"].rsplit("-", 1)[1])
            assert abs(brightness - number * 40) <= 3
        await wait_until(lambda: all(camera.track.loop_count >= 1 for camera in cameras.values()))
    response = await client.delete(f"{BASE}/test-session")
    assert response.status == 200
    assert all(camera.track.readyState == "ended" for camera in cameras.values())
    assert all(camera.track._container is None for camera in cameras.values())


async def test_mixed_browser_and_demo_preview_disconnect_preserves_replay(
    client, installed_demos, inference_api
):
    body = demo_body(api_base=inference_api.api_base)
    body["cameras"][1]["source"] = "webrtc"
    del body["cameras"][1]["demo_id"]
    await camera_tests.create_session(client, body)
    manager = client.app[multi_camera.MULTI_CAMERA_KEY]
    demo_camera = manager.sessions["test-session"].cameras["camera-1"]
    async with (
        client.ws_connect(f"{BASE}/test-session/ws") as ws,
        camera_tests.browser_camera(client, "camera-2", 80),
    ):
        async with preview(client) as frame:
            assert (frame.width, frame.height) == (64, 48)
            results = await camera_tests.receive_camera_results(ws, 2)
            assert set(results) == {"camera-1", "camera-2"}
        await wait_until(lambda: demo_camera.pc is None)
        previous_count = demo_camera.service.total_inferences
        await wait_until(lambda: demo_camera.service.total_inferences > previous_count)
        assert demo_camera.track.readyState == "live"
        assert demo_camera.state == "streaming"
        # Optional preview can reconnect without recreating the looping source.
        previous_track = demo_camera.track
        async with preview(client):
            assert demo_camera.track is previous_track
    assert demo_camera.track.readyState == "live"


async def test_recorded_session_supports_segmentation_without_vlm(client, installed_demos):
    manager = client.app[multi_camera.MULTI_CAMERA_KEY]
    await manager.segmentation.close()
    manager.segmentation = FakeSegmentation()
    body = demo_body()
    body.pop("vlm")
    body["vlm_enabled"] = False
    body["segmentation"] = {"enabled": True, "classes": ["person", "shopping cart"]}
    await camera_tests.create_session(client, body)
    cameras = manager.sessions["test-session"].cameras
    assert all(camera.service is None for camera in cameras.values())
    async with client.ws_connect(f"{BASE}/test-session/ws") as ws:
        seen = set()
        deadline = asyncio.get_running_loop().time() + 5
        while len(seen) < 2:
            event = await ws.receive_json(
                timeout=max(0.01, deadline - asyncio.get_running_loop().time())
            )
            if event["type"] == "segmentation_result":
                assert event["state"] == "running"
                seen.add(event["camera_id"])
        assert seen == {"camera-1", "camera-2"}
    assert all(isinstance(camera.track, LocalVideoTrack) for camera in cameras.values())
