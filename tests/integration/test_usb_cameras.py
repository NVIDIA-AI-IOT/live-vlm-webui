"""Server-attached USB sources use the existing remote preview and inference flow."""

import asyncio

import pytest

from live_vlm_webui import multi_camera
from tests.integration import test_multi_camera as camera_tests
from tests.integration.test_retail_demo import preview

client = camera_tests.client
inference_api = camera_tests.inference_api
BASE = camera_tests.BASE


@pytest.fixture
def usb_sources(monkeypatch):
    tracks = []
    catalog = [{"device_id": f"video{i * 2}", "label": f"USB camera {i + 1}"} for i in range(4)]
    monkeypatch.setattr(multi_camera, "discover_usb_cameras", lambda: list(catalog))

    async def create(device_id):
        track = camera_tests.SyntheticCamera(brightness=40 + int(device_id[5:]) * 20)
        tracks.append(track)
        return track

    monkeypatch.setattr(multi_camera.USBVideoTrack, "create", staticmethod(create))
    return catalog, tracks


def usb_body(count=2, **kwargs):
    body = camera_tests.session_body(count, source="usb", **kwargs)
    for index, camera in enumerate(body["cameras"]):
        camera["usb_device_id"] = f"video{index * 2}"
    return body


async def test_usb_catalog_is_server_catalog(client, usb_sources):
    response = await client.get("/api/usb-cameras")
    assert response.status == 200
    assert (await response.json())["cameras"] == usb_sources[0]
    assert not usb_sources[1], "Listing devices must not start a camera"


@pytest.mark.parametrize("count", [2, 4])
async def test_usb_sources_infer_independently_and_stream_recvonly(
    client, usb_sources, inference_api, count
):
    body = usb_body(count, api_base=inference_api.api_base)
    created = await camera_tests.create_session(client, body)
    try:
        assert [c["usb_device_id"] for c in created["cameras"]] == [
            f"video{i * 2}" for i in range(count)
        ]
        ws = await client.ws_connect(f"{BASE}/test-session/ws")
        results = await camera_tests.receive_camera_results(ws, count)
        assert len({r["text"] for r in results.values()}) == count
        async with preview(client) as frame:
            assert frame.width == 64 and frame.height == 48
        await ws.close()
    finally:
        assert (await client.delete(f"{BASE}/test-session")).status == 200
    assert len(usb_sources[1]) == count
    assert all(track.readyState == "ended" for track in usb_sources[1])


@pytest.mark.parametrize(
    "device_id", [None, "", "/dev/video0", "../../etc/passwd", "video0\n", 0, "video99"]
)
async def test_usb_rejects_invalid_or_missing_devices_before_open(client, usb_sources, device_id):
    body = usb_body()
    body["cameras"][0]["usb_device_id"] = device_id
    response = await client.post(BASE, json=body)
    assert response.status == 400
    assert not usb_sources[1]
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions


async def test_usb_rejects_duplicate_device_in_one_session(client, usb_sources):
    body = usb_body()
    body["cameras"][1]["usb_device_id"] = "video0"
    response = await client.post(BASE, json=body)
    assert response.status == 400
    assert "different USB camera" in (await response.json())["error"]
    assert not usb_sources[1]


async def test_usb_rejects_device_field_on_browser_source(client, usb_sources):
    body = usb_body()
    body["cameras"][0]["source"] = "webrtc"
    assert (await client.post(BASE, json=body)).status == 400
    assert not usb_sources[1]


async def test_usb_device_ownership_released_after_stop(client, usb_sources):
    await camera_tests.create_session(client, usb_body())
    try:
        conflict = await client.post(BASE, json=usb_body(session_id="second"))
        assert conflict.status == 409
        assert len(usb_sources[1]) == 2
    finally:
        await client.delete(f"{BASE}/test-session")
    await camera_tests.create_session(client, usb_body(session_id="second"))
    assert (await client.delete(f"{BASE}/second")).status == 200
    assert len(usb_sources[1]) == 4
    assert all(track.readyState == "ended" for track in usb_sources[1])


async def test_failed_usb_open_cleans_partial_session_and_releases_devices(
    client, usb_sources, monkeypatch
):
    create = multi_camera.USBVideoTrack.create

    async def fail_second(device_id):
        if device_id == "video2":
            raise RuntimeError("Camera unplugged during open")
        return await create(device_id)

    monkeypatch.setattr(multi_camera.USBVideoTrack, "create", staticmethod(fail_second))
    response = await client.post(BASE, json=usb_body())
    assert response.status == 502
    assert "USB cameras" in await response.text()
    assert all(track.readyState == "ended" for track in usb_sources[1])
    assert not client.app[multi_camera.MULTI_CAMERA_KEY].sessions
    monkeypatch.setattr(multi_camera.USBVideoTrack, "create", staticmethod(create))
    await camera_tests.create_session(client, usb_body())
    await client.delete(f"{BASE}/test-session")


async def test_cancelled_usb_create_releases_opened_device(usb_sources, monkeypatch):
    manager = multi_camera.MultiCameraManager(lambda: None)
    entered = asyncio.Event()
    create = multi_camera.USBVideoTrack.create

    async def block_second(device_id):
        if device_id == "video2":
            entered.set()
            await asyncio.Future()
        return await create(device_id)

    monkeypatch.setattr(multi_camera.USBVideoTrack, "create", staticmethod(block_second))
    pending = asyncio.create_task(manager.create(usb_body()))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not manager.sessions
        assert all(track.readyState == "ended" for track in usb_sources[1])
    finally:
        await manager.close()
