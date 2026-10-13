"""Camera-local visual windows, admission, ownership and reset without model inference."""

import asyncio
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from PIL import Image

from live_vlm_webui import video_processor
from live_vlm_webui.temporal_buffer import TemporalBuffer, TemporalConfig
from live_vlm_webui.video_processor import VideoProcessorTrack


class WindowService:
    def __init__(self, blocked=False):
        self.prompt = "Describe the action"
        self.model = "synthetic"
        self.api_base = "http://unused.invalid/v1"
        self.backend = "openai"
        self.max_tokens = 32
        self.temperature = 0
        self.image_max_edge = 512
        self.windows = []
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.active = 0
        self.maximum_active = 0

    async def process_identified_video(self, images, timestamps, received_at, unix_ms, **identity):
        self.windows.append((images, timestamps, received_at, unix_ms, identity))
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1

    def get_current_response(self):
        return "result", bool(self.active)

    def get_metrics(self):
        return {"total_inferences": len(self.windows)}


def frame(number, *, brightness=None, pts=None, size=(16, 12)):
    pixels = np.full((size[1], size[0], 3), brightness or number, dtype=np.uint8)
    value = av.VideoFrame.from_ndarray(pixels, format="rgb24")
    value.pts = number if pts is None else pts
    value.time_base = Fraction(1, 2)
    return value


def offer(processor, number, *, timestamp=None, brightness=None, pts=None):
    return processor.submit_frame(
        frame(number, brightness=brightness, pts=pts),
        received_at=100 + (number - 1) * 0.5 if timestamp is None else timestamp,
        received_unix_ms=100000 + number * 500,
        frame_id=number,
        preview_mode="segmented",
    )


def processor(service=None, **kwargs):
    return VideoProcessorTrack(
        VideoStreamTrack(),
        service or WindowService(),
        process_every_n_frames=1,
        temporal={"enabled": True, "window_seconds": 2},
        **kwargs,
    )


@pytest.mark.parametrize("window", [2, 4, 8])
def test_temporal_config_capacity_and_explicit_defaults(window):
    assert TemporalConfig.from_dict().as_dict() == {
        "enabled": False,
        "window_seconds": 4,
        "sample_fps": 2,
    }
    assert TemporalConfig.from_dict({"window_seconds": window}).max_frames == window * 2


@pytest.mark.parametrize(
    "value",
    [
        [],
        True,
        {"enabled": 1},
        {"window_seconds": True},
        {"window_seconds": 3},
        {"window_seconds": 4.0},
        {"window_seconds": 16},
        {"sample_fps": 1},
        {"sample_fps": 2.0},
        {"sample_fps": True},
        {"unknown": 1},
    ],
)
def test_temporal_config_rejects_unbounded_or_ambiguous_values(value):
    with pytest.raises(ValueError):
        TemporalConfig.from_dict(value)


def test_buffer_samples_by_time_owns_bounded_rgb_copies_and_stable_snapshots():
    buffer = TemporalBuffer({"enabled": True, "window_seconds": 2})
    source = Image.new("RGB", (1280, 720), (10, 20, 30))
    for number in range(4):
        timestamp = number * 0.5
        assert buffer.due(timestamp, timestamp)
        assert buffer.offer(source, timestamp, number, timestamp * 1000)
        assert not buffer.due(timestamp + 0.25, timestamp + 0.25)
    assert buffer.ready
    snapshot = buffer.snapshot()
    assert isinstance(snapshot, tuple)
    source.paste((255, 0, 0), (0, 0, *source.size))
    assert all(sample.image.getpixel((0, 0)) == (10, 20, 30) for sample in snapshot)
    assert all(sample.image.size == (640, 360) for sample in snapshot)
    for number in range(4, 16):
        assert buffer.due(number * 0.5, number * 0.5)
        assert buffer.offer(source, number * 0.5, number, number * 500)
    assert len(buffer.samples) == 4
    assert [sample.frame_id for sample in buffer.snapshot()] == [12, 13, 14, 15]
    assert [sample.frame_id for sample in snapshot] == [0, 1, 2, 3]
    assert all(sample.image.getpixel((0, 0)) == (10, 20, 30) for sample in snapshot)
    assert source.size == (1280, 720)


@pytest.mark.parametrize("timestamp,source_time", [(0.5, 2), (2, 0), (5, 5)])
def test_receipt_regression_pts_regression_and_long_gap_reset_window(timestamp, source_time):
    buffer = TemporalBuffer({"enabled": True, "window_seconds": 2})
    image = Image.new("RGB", (8, 8))
    for index, observed in enumerate([0, 0.5, 1]):
        assert buffer.due(observed, observed)
        buffer.offer(image, observed, index, observed * 1000)
    assert buffer.due(timestamp, source_time)
    assert not buffer.samples
    buffer.offer(image, timestamp, 10, timestamp * 1000)
    assert [sample.frame_id for sample in buffer.snapshot()] == [10]
    assert not buffer.ready


async def test_busy_request_keeps_sampling_and_admits_only_latest_complete_window():
    service = WindowService(blocked=True)
    track = processor(service)
    try:
        for number in range(1, 5):
            assert offer(track, number) is (number == 4)
        await asyncio.wait_for(service.started.wait(), timeout=1)
        running = track._inference_task
        original_window = service.windows[0][0]
        for number in range(5, 21):
            assert not offer(track, number)
            await asyncio.sleep(0)
        assert track._inference_task is running
        assert len(service.windows) == 1
        assert len(track.temporal_buffer.samples) == 4
        assert [sample.frame_id for sample in track.temporal_buffer.snapshot()] == [17, 18, 19, 20]
        assert [image.getpixel((0, 0))[0] for image in original_window] == [1, 2, 3, 4]
        service.release.set()
        await running
        assert offer(track, 21)
        await track._inference_task
        newest = service.windows[1]
        assert [image.getpixel((0, 0))[0] for image in newest[0]] == [18, 19, 20, 21]
        assert newest[1] == [0, 0.5, 1, 1.5]
        assert newest[2:4] == (110, 110500)
        assert newest[4] == {
            "frame_id": 21,
            "frame_pts": 21,
            "frame_time_base": "1/2",
            "preview_mode": "segmented",
        }
        assert service.maximum_active == 1
    finally:
        await track.aclose()


async def test_four_concurrent_cameras_keep_windows_and_stop_independent():
    services = [WindowService(blocked=True) for _ in range(4)]
    tracks = [processor(service) for service in services]
    try:
        for number in range(1, 5):
            for camera, track in enumerate(tracks, 1):
                offer(track, number, brightness=40 * camera + number)
        await asyncio.gather(*(asyncio.wait_for(service.started.wait(), 1) for service in services))
        await tracks[0].aclose()
        assert not tracks[0].temporal_buffer.samples
        assert services[0].cancelled.is_set()
        assert all(service.active == 1 for service in services[1:])
        for camera, service in enumerate(services, 1):
            assert [image.getpixel((0, 0))[0] for image in service.windows[0][0]] == [
                40 * camera + number for number in range(1, 5)
            ]
        assert len({id(track.temporal_buffer) for track in tracks}) == 4
    finally:
        await asyncio.gather(*(track.aclose() for track in tracks))
    assert all(service.active == 0 for service in services)
    assert all(not track.temporal_buffer.samples for track in tracks)


@pytest.mark.parametrize(
    "setting,new_value",
    [
        ("prompt", "A different question"),
        ("model", "replacement"),
        ("api_base", "http://replacement.invalid/v1"),
        ("backend", "edgellm"),
        ("max_tokens", 96),
        ("temperature", 0.5),
        ("image_max_edge", 640),
    ],
)
async def test_changed_settings_invalidate_old_window_and_cancel_inflight(setting, new_value):
    service = WindowService(blocked=True)
    track = processor(service)
    try:
        for number in range(1, 5):
            offer(track, number)
        await asyncio.wait_for(service.started.wait(), 1)
        setattr(service, setting, new_value)
        assert not offer(track, 5)
        await asyncio.sleep(0)
        assert len(track.temporal_buffer.samples) == 1
        assert track.temporal_buffer.snapshot()[0].frame_id == 5
        assert service.cancelled.is_set()
        assert len(service.windows) == 1
    finally:
        await track.aclose()


async def test_temporal_recv_returns_original_frame_and_stop_clears_state(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        video_processor,
        "time",
        SimpleNamespace(perf_counter=lambda: clock[0], time=lambda: clock[0] + 100000),
    )
    source_frames = [frame(number) for number in range(1, 5)]

    class Source(VideoStreamTrack):
        async def recv(self):
            return source_frames.pop(0)

    source = Source()
    service = WindowService(blocked=True)
    statuses = []
    track = VideoProcessorTrack(
        source,
        service,
        process_every_n_frames=1,
        temporal={"enabled": True, "window_seconds": 2},
        temporal_callback=statuses.append,
    )
    try:
        for number in range(4):
            clock[0] = 100 + number * 0.5
            original = source_frames[0]
            original_pixels = original.to_ndarray(format="rgb24").copy()
            assert await track.recv() is original
            np.testing.assert_array_equal(original.to_ndarray(format="rgb24"), original_pixels)
        await asyncio.wait_for(service.started.wait(), 1)
        assert [status["temporal_buffer_frames"] for status in statuses] == [1, 2, 3, 4]
        assert statuses[-1]["temporal_buffer_ready"]
        await track.aclose()
        assert source.readyState == "live"
        assert not track.temporal_buffer.samples
        assert service.cancelled.is_set()
        with pytest.raises(MediaStreamError):
            await track.recv()
        assert not offer(track, 5)
    finally:
        await track.aclose()


@pytest.mark.parametrize("timestamp,pts", [(102, 0), (120, 5)])
async def test_source_restart_or_gap_cancels_inflight_and_rewarms(timestamp, pts):
    service = WindowService(blocked=True)
    track = processor(service)
    try:
        for number in range(1, 5):
            offer(track, number)
        await asyncio.wait_for(service.started.wait(), 1)
        assert not offer(track, 5, timestamp=timestamp, pts=pts)
        await asyncio.sleep(0)
        assert service.cancelled.is_set()
        assert [sample.frame_id for sample in track.temporal_buffer.snapshot()] == [5]
        assert not track.temporal_buffer.ready
    finally:
        await track.aclose()


@pytest.mark.parametrize("generation_attribute", ["source_generation", "loop_count", "relayed"])
async def test_quick_reconnect_or_loop_resets_despite_continuous_pts(
    monkeypatch, generation_attribute
):
    clock = [100.0]
    monkeypatch.setattr(
        video_processor,
        "time",
        SimpleNamespace(perf_counter=lambda: clock[0], time=lambda: clock[0] + 100000),
    )
    original = SimpleNamespace(generation=0)

    class Source(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self.number = 0
            if generation_attribute != "relayed":
                setattr(self, generation_attribute, 0)

        async def recv(self):
            self.number += 1
            if self.number == 5:
                if generation_attribute == "relayed":
                    original.generation += 1
                else:
                    setattr(self, generation_attribute, 1)
            return frame(self.number)

    source = Source()
    service = WindowService(blocked=True)
    kwargs = (
        {"source_generation_provider": lambda: original.generation}
        if generation_attribute == "relayed"
        else {}
    )
    track = VideoProcessorTrack(
        source,
        service,
        process_every_n_frames=1,
        temporal={"enabled": True, "window_seconds": 2},
        **kwargs,
    )
    try:
        for number in range(1, 5):
            clock[0] = 100 + (number - 1) * 0.5
            await track.recv()
        await asyncio.wait_for(service.started.wait(), 1)
        assert track.temporal_buffer.ready
        clock[0] = 102
        received = await track.recv()
        assert received.pts == 5  # No PTS regression or long gap caused this reset.
        await asyncio.sleep(0)
        assert service.cancelled.is_set()
        assert [sample.frame_id for sample in track.temporal_buffer.snapshot()] == [5]
        assert not track.temporal_buffer.ready
    finally:
        await track.aclose()


async def test_delayed_segmented_original_cannot_cross_source_generation_boundary(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(
        video_processor,
        "time",
        SimpleNamespace(perf_counter=lambda: clock[0], time=lambda: clock[0] + 100000),
    )

    class Source(VideoStreamTrack):
        def __init__(self):
            super().__init__()
            self.number = 0
            self.loop_count = 0

        async def recv(self):
            self.number += 1
            if self.number == 5:
                self.loop_count = 1
            return frame(self.number)

    service = WindowService(blocked=True)
    track = VideoProcessorTrack(
        Source(),
        service,
        process_every_n_frames=1,
        process_on_recv=False,
        temporal={"enabled": True, "window_seconds": 2},
    )
    try:
        pending = []
        for number in range(1, 5):
            clock[0] = 100 + (number - 1) * 0.5
            received = await track.recv()
            pending.append((received, clock[0], track.frame_count))
            track.submit_frame(
                received,
                received_at=clock[0],
                received_unix_ms=100000,
                frame_id=track.frame_count,
                preview_mode="segmented",
            )
        await asyncio.wait_for(service.started.wait(), 1)
        clock[0] = 102
        new_original = await track.recv()
        await asyncio.sleep(0)
        assert service.cancelled.is_set()
        assert not track.temporal_buffer.samples
        # An old GPU completion may arrive after the new loop has been consumed.
        old, old_time, old_id = pending[-1]
        assert not track.submit_frame(
            old,
            received_at=old_time,
            received_unix_ms=100000,
            frame_id=old_id,
            preview_mode="segmented",
        )
        assert not track.temporal_buffer.samples
        assert not track.submit_frame(
            new_original,
            received_at=102,
            received_unix_ms=102000,
            frame_id=5,
            preview_mode="segmented",
        )
        assert [sample.frame_id for sample in track.temporal_buffer.snapshot()] == [5]
        # A second old completion must not clear or contaminate the new sample.
        assert not track.submit_frame(
            old,
            received_at=old_time,
            received_unix_ms=100000,
            frame_id=old_id,
            preview_mode="segmented",
        )
        assert [sample.frame_id for sample in track.temporal_buffer.snapshot()] == [5]
    finally:
        await track.aclose()
