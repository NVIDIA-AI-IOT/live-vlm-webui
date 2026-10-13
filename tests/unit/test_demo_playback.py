"""Global demo resize limits do not change footage identity or replay cadence."""

import hashlib
import json
from types import SimpleNamespace

import av
import pytest

from live_vlm_webui import multi_camera
from live_vlm_webui.demo_catalog import demo_catalog, demo_playback_bounds
from live_vlm_webui.local_video_track import VIDEO_TIME_BASE, LocalVideoTrack
from tests.unit.test_local_video_track import write_video


def test_default_demo_bounds_and_catalog(monkeypatch):
    monkeypatch.delenv("LIVE_VLM_DEMO_MAX_SIZE", raising=False)
    assert demo_playback_bounds() == (960, 540)
    catalog = demo_catalog()
    assert (catalog["playback_max_width"], catalog["playback_max_height"]) == (960, 540)
    assert catalog["max_fps"] == 25


@pytest.mark.parametrize(
    "value,expected",
    [("320x180", (320, 180)), ("640x360", (640, 360)), ("1920x1080", (1920, 1080))],
)
def test_valid_global_demo_bounds(monkeypatch, value, expected):
    monkeypatch.setenv("LIVE_VLM_DEMO_MAX_SIZE", value)
    assert demo_playback_bounds() == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "640",
        "640X360",
        "640 x 360",
        " 640x360",
        "640x360\n",
        "640.0x360",
        "0640x360",
        "-640x360",
        "0x0",
        "318x180",
        "320x178",
        "1922x1080",
        "1920x1082",
        "641x360",
        "640x361",
        "640x360x25",
    ],
)
def test_invalid_global_demo_bounds_fail_before_startup(monkeypatch, value):
    monkeypatch.setenv("LIVE_VLM_DEMO_MAX_SIZE", value)
    with pytest.raises(ValueError, match="LIVE_VLM_DEMO_MAX_SIZE"):
        multi_camera.MultiCameraManager(lambda: None)


async def test_manager_snapshots_bounds_for_source_and_catalog(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_VLM_DEMO_MAX_SIZE", "640x360")
    manager = multi_camera.MultiCameraManager(lambda: None)
    monkeypatch.setenv("LIVE_VLM_DEMO_MAX_SIZE", "1920x1080")
    source = tmp_path / "original.mp4"
    track = object()
    calls = []

    async def create(path, **kwargs):
        calls.append((path, kwargs))
        return track

    monkeypatch.setattr(multi_camera, "resolve_demo", lambda demo_id: source)
    monkeypatch.setattr(multi_camera.LocalVideoTrack, "create", create)
    monkeypatch.setattr(manager, "_attach", lambda *args: None)
    camera = SimpleNamespace(demo_id="store-25-1", track=None)
    try:
        await manager._open_demo(None, camera)
        assert camera.track is track
        assert calls == [(source, {"max_width": 640, "max_height": 360})]
        request = SimpleNamespace(app={multi_camera.MULTI_CAMERA_KEY: manager})
        response = await multi_camera.demo_videos(request)
        catalog = json.loads(response.body)
        assert (catalog["playback_max_width"], catalog["playback_max_height"]) == (640, 360)
        assert catalog["max_fps"] == 25
        assert [clip["demo_id"] for clip in catalog["videos"][:4]] == [
            f"store-25-{i}" for i in range(1, 5)
        ]
        assert all(clip["source_fps"] == 25 for clip in catalog["videos"][:4])
    finally:
        await manager.close()


async def test_smaller_playback_preserves_frame_order_timing_and_original_file(tmp_path):
    path = write_video(tmp_path / "native25.mkv", fps=25, frames=6, width=1280, height=720)
    original_digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with av.open(str(path)) as container:
        source_trace = [
            int(frame.to_ndarray(format="bgr24").mean()) for frame in container.decode(video=0)
        ]
    assert len(set(source_trace)) == 6
    traces = []
    for width, height in [(960, 540), (640, 360)]:
        track = await LocalVideoTrack.create(path, max_width=width, max_height=height)
        try:
            frames = [await track.recv() for _ in range(10)]
            assert all((frame.width, frame.height) == (width, height) for frame in frames)
            assert all(frame.time_base == VIDEO_TIME_BASE for frame in frames)
            assert [frame.pts for frame in frames] == [index * 3600 for index in range(10)]
            assert track.loop_count == 1
            trace = [int(frame.to_ndarray(format="bgr24").mean()) for frame in frames]
            assert all(
                abs(value - source_trace[index % 6]) <= 1 for index, value in enumerate(trace)
            )
            traces.append(trace)
        finally:
            await track.aclose()
    assert traces[0] == traces[1]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == original_digest


@pytest.mark.parametrize(
    "size,expected",
    [((1280, 720), (640, 360)), ((720, 1280), (202, 360)), ((320, 180), (320, 180))],
)
async def test_smaller_bound_preserves_aspect_and_does_not_upscale(tmp_path, size, expected):
    track = await LocalVideoTrack.create(
        write_video(tmp_path / "aspect.mkv", fps=25, frames=1, width=size[0], height=size[1]),
        max_width=640,
        max_height=360,
    )
    try:
        frame = await track.recv()
        assert (frame.width, frame.height) == expected
        assert abs(frame.width / frame.height - size[0] / size[1]) < 0.005
        assert frame.pts == 0
    finally:
        await track.aclose()
