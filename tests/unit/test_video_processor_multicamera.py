"""Per-camera sampling and cancellation without camera hardware or a model server."""

import asyncio
from fractions import Fraction
from unittest.mock import Mock

import av
import numpy as np
import pytest
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError

from live_vlm_webui.video_processor import VideoProcessorTrack


class SyntheticSource(VideoStreamTrack):
    def __init__(self, timestamps=None):
        super().__init__()
        self.frames = []
        self.timestamps = iter(timestamps) if timestamps is not None else None
        self.release = None
        self.waiting = asyncio.Event()

    async def recv(self):
        self.waiting.set()
        if self.release is not None:
            await self.release.wait()
        number = len(self.frames) + 1
        frame = av.VideoFrame.from_ndarray(np.full((8, 8, 3), number, np.uint8), format="bgr24")
        frame.pts = next(self.timestamps) if self.timestamps is not None else number - 1
        frame.time_base = Fraction(1, 30)
        self.frames.append(frame)
        return frame


class FakeVLM:
    def __init__(self, blocked=False, fail=False):
        self.release = asyncio.Event()
        if not blocked:
            self.release.set()
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.samples = []
        self.active = 0
        self.maximum_active = 0
        self.fail = fail

    async def process_frame(self, image):
        self.samples.append(image.getpixel((0, 0))[0])
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.started.set()
        try:
            await self.release.wait()
            if self.fail:
                raise RuntimeError("model unavailable")
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        finally:
            self.active -= 1

    def get_current_response(self):
        return "camera result", self.active > 0

    def get_metrics(self):
        return {"total_inferences": len(self.samples)}


@pytest.mark.asyncio
async def test_camera_intervals_are_independent_and_frames_pass_through():
    sources = [SyntheticSource(), SyntheticSource()]
    services = [FakeVLM(), FakeVLM()]
    callbacks = [Mock(), Mock()]
    processors = [
        VideoProcessorTrack(source, service, callback, process_every_n_frames=interval)
        for source, service, callback, interval in zip(sources, services, callbacks, [2, 4])
    ]
    try:
        for _ in range(8):
            for source, processor in zip(sources, processors):
                assert await processor.recv() is source.frames[-1]
            await asyncio.sleep(0)
        assert services[0].samples == [2, 4, 6, 8]
        assert services[1].samples == [4, 8]
        assert [callback.call_count for callback in callbacks] == [8, 8]
    finally:
        await asyncio.gather(*(processor.aclose() for processor in processors))


@pytest.mark.asyncio
async def test_legacy_tracks_follow_live_class_defaults(monkeypatch):
    monkeypatch.setattr(VideoProcessorTrack, "process_every_n_frames", 2)
    legacy_service, camera_service = FakeVLM(), FakeVLM()
    legacy = VideoProcessorTrack(SyntheticSource(), legacy_service)
    camera = VideoProcessorTrack(
        SyntheticSource(), camera_service, process_every_n_frames=4, max_frame_latency=0
    )
    try:
        for _ in range(2):
            await legacy.recv()
            await camera.recv()
            await asyncio.sleep(0)
        monkeypatch.setattr(VideoProcessorTrack, "process_every_n_frames", 1)
        for _ in range(2):
            await legacy.recv()
            await camera.recv()
            await asyncio.sleep(0)
        assert legacy_service.samples == [2, 3, 4]
        assert camera_service.samples == [4]
        monkeypatch.setattr(VideoProcessorTrack, "max_frame_latency", 0.2)
        assert legacy.max_frame_latency == 0.2
        assert camera.max_frame_latency == 0
    finally:
        await legacy.aclose()
        await camera.aclose()


@pytest.mark.asyncio
async def test_busy_camera_drops_samples_and_never_queues_inference():
    service = FakeVLM(blocked=True)
    processor = VideoProcessorTrack(SyntheticSource(), service, process_every_n_frames=1)
    try:
        await processor.recv()
        await asyncio.wait_for(service.started.wait(), timeout=1)
        first_task = processor._inference_task
        for _ in range(30):
            await processor.recv()
            await asyncio.sleep(0)
        assert processor.frame_count == 31
        assert service.samples == [1]
        assert processor._inference_task is first_task
        assert service.maximum_active == 1
        service.release.set()
        await first_task
        await processor.recv()
        await asyncio.sleep(0)
        assert service.samples == [1, 32]
        assert service.maximum_active == 1
    finally:
        await processor.aclose()


@pytest.mark.asyncio
async def test_aclose_cancels_inference_without_stopping_borrowed_source():
    source, service, callback = SyntheticSource(), FakeVLM(blocked=True), Mock()
    processor = VideoProcessorTrack(source, service, callback, process_every_n_frames=1)
    await processor.recv()
    await asyncio.wait_for(service.started.wait(), timeout=1)
    await processor.aclose()
    assert service.cancelled.is_set()
    assert service.active == 0
    assert processor.readyState == "ended"
    assert source.readyState == "live"
    with pytest.raises(MediaStreamError):
        await processor.recv()
    assert len(source.frames) == 1
    assert callback.call_count == 1
    await processor.aclose()


@pytest.mark.asyncio
async def test_stop_while_waiting_for_a_frame_prevents_callback_and_inference():
    source, service, callback = SyntheticSource(), FakeVLM(), Mock()
    source.release = asyncio.Event()
    processor = VideoProcessorTrack(source, service, callback, process_every_n_frames=1)
    receive_task = asyncio.create_task(processor.recv())
    try:
        await asyncio.wait_for(source.waiting.wait(), timeout=1)
        processor.stop()
        source.release.set()
        with pytest.raises(MediaStreamError):
            await receive_task
        callback.assert_not_called()
        assert service.samples == []
        assert source.readyState == "live"
    finally:
        receive_task.cancel()
        await asyncio.gather(receive_task, return_exceptions=True)
        await processor.aclose()


@pytest.mark.asyncio
async def test_model_failure_is_retrieved_and_next_sample_can_run(caplog):
    service = FakeVLM(fail=True)
    processor = VideoProcessorTrack(SyntheticSource(), service, process_every_n_frames=1)
    try:
        await processor.recv()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert "VLM frame processing failed" in caplog.text
        assert "model unavailable" in caplog.text
        service.fail = False
        await processor.recv()
        await asyncio.sleep(0)
        assert service.samples == [1, 2]
    finally:
        await processor.aclose()


@pytest.mark.asyncio
async def test_camera_latency_limit_does_not_change_other_camera(monkeypatch):
    current_time = [0.0]
    monkeypatch.setattr("live_vlm_webui.video_processor.time.time", lambda: current_time[0])
    sources = [SyntheticSource([0, 1, 60]), SyntheticSource([0, 1, 60])]
    limited = VideoProcessorTrack(sources[0], FakeVLM(), max_frame_latency=0.1)
    unlimited = VideoProcessorTrack(sources[1], FakeVLM(), max_frame_latency=0)
    try:
        await limited.recv()
        await unlimited.recv()
        current_time[0] = 2.0
        assert (await limited.recv()).pts == 60
        assert (await unlimited.recv()).pts == 1
        assert limited.dropped_frames == 1
        assert unlimited.dropped_frames == 0
    finally:
        await limited.aclose()
        await unlimited.aclose()


@pytest.mark.parametrize("interval", [0, -1, 1.5, True])
def test_rejects_invalid_per_camera_interval(interval):
    with pytest.raises(ValueError, match="positive integer"):
        VideoProcessorTrack(SyntheticSource(), FakeVLM(), process_every_n_frames=interval)


@pytest.mark.parametrize("latency", [-1, float("nan"), float("inf"), True])
def test_rejects_invalid_per_camera_latency(latency):
    with pytest.raises(ValueError, match="finite non-negative"):
        VideoProcessorTrack(SyntheticSource(), FakeVLM(), max_frame_latency=latency)


@pytest.mark.asyncio
async def test_repeated_stop_does_not_interrupt_inference_cleanup():
    cleanup_started = asyncio.Event()
    cleanup_complete = asyncio.Event()

    class CleaningVLM(FakeVLM):
        async def process_frame(self, image):
            try:
                await super().process_frame(image)
            finally:
                cleanup_started.set()
                await asyncio.sleep(0)
                cleanup_complete.set()

    service = CleaningVLM(blocked=True)
    processor = VideoProcessorTrack(SyntheticSource(), service, process_every_n_frames=1)
    await processor.recv()
    await asyncio.wait_for(service.started.wait(), timeout=1)
    processor.stop()
    await asyncio.wait_for(cleanup_started.wait(), timeout=1)
    await processor.aclose()
    assert cleanup_complete.is_set()


@pytest.mark.asyncio
async def test_busy_inference_resumes_on_next_fresh_frame_after_missed_boundary():
    service = FakeVLM(blocked=True)
    processor = VideoProcessorTrack(SyntheticSource(), service, process_every_n_frames=3)
    try:
        for _ in range(3):
            await processor.recv()
            await asyncio.sleep(0)
        await asyncio.wait_for(service.started.wait(), timeout=1)
        running = processor._inference_task
        for _ in range(4):
            await processor.recv()
            await asyncio.sleep(0)
        assert service.samples == [3]
        service.release.set()
        await running
        frame = await processor.recv()
        await asyncio.sleep(0)
        # The old modulo sampler waited until frame9. Frame8 is already due
        # and fresh, so inference should resume immediately without backlog.
        assert service.samples == [3, 8]
        assert service.maximum_active == 1
        assert processor.last_frame_received_at is not None
        assert processor.last_frame_received_unix_ms > 0
        assert frame.pts == 7
    finally:
        await processor.aclose()


@pytest.mark.asyncio
async def test_external_sampling_uses_source_cadence_and_exact_completed_frame_identity():
    class IdentifiedVLM(FakeVLM):
        def __init__(self):
            super().__init__(blocked=True)
            self.identities = []

        async def process_identified_frame(self, image, received_at, received_unix_ms, **identity):
            self.identities.append((received_at, received_unix_ms, identity))
            await super().process_frame(image)

    source, service = SyntheticSource(), IdentifiedVLM()
    processor = VideoProcessorTrack(
        source, service, process_every_n_frames=3, process_on_recv=False
    )
    try:
        for _ in range(12):
            await processor.recv()
        assert processor.frame_count == 12
        assert service.samples == []
        assert processor.last_frame is None

        def offer(number):
            return processor.submit_frame(
                source.frames[number - 1],
                received_at=number / 10,
                received_unix_ms=number * 1000,
                frame_id=number,
                preview_mode="segmented",
            )

        assert not offer(2)
        assert offer(3)
        await service.started.wait()
        assert not offer(6)  # Busy inference never queues another frame.
        running = processor._inference_task
        service.release.set()
        await running
        assert offer(8)  # Due by source cadence despite only two completed masks.
        await processor._inference_task
        assert not offer(9)
        assert service.samples == [3, 8]
        assert service.maximum_active == 1
        assert service.identities == [
            (
                number / 10,
                number * 1000,
                {
                    "frame_id": number,
                    "frame_pts": number - 1,
                    "frame_time_base": "1/30",
                    "preview_mode": "segmented",
                },
            )
            for number in (3, 8)
        ]
        np.testing.assert_array_equal(processor.last_frame, source.frames[7].to_ndarray("bgr24"))
        processor.stop()
        assert not offer(12)
        assert service.samples == [3, 8]
    finally:
        await processor.aclose()
