"""Real-file replay cadence/looping and decoder cancellation without cameras."""

import asyncio
import threading
import time
from fractions import Fraction
from types import SimpleNamespace

import av
import numpy as np
import pytest
from aiortc.mediastreams import MediaStreamError

from live_vlm_webui.local_video_track import VIDEO_TIME_BASE, LocalVideoTrack


def write_video(path, *, fps=30, frames=6, width=64, height=48):
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("ffv1", rate=fps)
        stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
        for index in range(frames):
            pixels = np.full((height, width, 3), 20 + index * 10, np.uint8)
            frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    return path


@pytest.mark.asyncio
async def test_real_video_loops_with_continuous_paced_90khz_timestamps(tmp_path):
    path = write_video(tmp_path / "demo.mkv")
    track = await LocalVideoTrack.create(path, max_fps=15)
    try:
        started = time.monotonic()
        frames = [await track.recv() for _ in range(8)]
        elapsed = time.monotonic() - started
        assert [frame.pts for frame in frames] == [index * 6000 for index in range(8)]
        assert all(frame.time_base == VIDEO_TIME_BASE for frame in frames)
        assert elapsed >= 0.43
        assert elapsed < 2.0
        assert track.loop_count >= 2
        assert track.frames_received == 8
        brightness = [int(frame.to_ndarray(format="bgr24").mean()) for frame in frames]
        assert brightness[1] > brightness[0]
        assert brightness[2] > brightness[1]
        assert abs(brightness[3] - brightness[0]) <= 1
        decoded = track._decoded_in_loop
        await asyncio.sleep(0.02)
        assert (
            track._decoded_in_loop == decoded
        ), "Replay must decode only when a frame is requested"
    finally:
        await track.aclose()
    assert track.readyState == "ended"
    assert track._container is None


@pytest.mark.asyncio
async def test_preserves_lower_native_frame_rate_and_does_not_upscale(tmp_path):
    track = await LocalVideoTrack.create(write_video(tmp_path / "slow.mkv", fps=10, frames=2))
    try:
        frames = [await track.recv() for _ in range(3)]
        assert [frame.pts for frame in frames] == [0, 9000, 18000]
        assert all((frame.width, frame.height) == (64, 48) for frame in frames)
    finally:
        await track.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "width,height,expected", [(1280, 720, (960, 540)), (720, 1280, (302, 540))]
)
async def test_video_is_fit_inside_demo_dimensions(tmp_path, width, height, expected):
    track = await LocalVideoTrack.create(
        write_video(tmp_path / "large.mkv", width=width, height=height, frames=1)
    )
    try:
        frame = await track.recv()
        assert (frame.width, frame.height) == expected
        assert abs(frame.width / frame.height - width / height) < 0.005
    finally:
        await track.aclose()


@pytest.mark.asyncio
async def test_stop_interrupts_frame_pacing_immediately(tmp_path):
    track = await LocalVideoTrack.create(write_video(tmp_path / "paced.mkv", fps=1, frames=2))
    await track.recv()
    pending = asyncio.create_task(track.recv())
    try:
        await asyncio.sleep(0.03)
        assert not pending.done()
        track.stop()
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(pending, timeout=0.3)
        await track.aclose()
        await track.aclose()
        with pytest.raises(MediaStreamError):
            await track.recv()
    finally:
        await track.aclose()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("contents", [None, b"", b"this is not a video"])
async def test_missing_empty_and_corrupt_files_are_terminal(tmp_path, contents):
    path = tmp_path / "bad.mkv"
    if contents is not None:
        path.write_bytes(contents)
    with pytest.raises(MediaStreamError):
        await LocalVideoTrack.create(path)


@pytest.mark.asyncio
async def test_audio_only_file_is_rejected(tmp_path):
    path = tmp_path / "audio.wav"
    with av.open(str(path), mode="w") as container:
        stream = container.add_stream("pcm_s16le", rate=8000)
        frame = av.AudioFrame.from_ndarray(
            np.zeros((1, 80), dtype=np.int16), format="s16", layout="mono"
        )
        frame.sample_rate = 8000
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    with pytest.raises(MediaStreamError, match="video stream"):
        await LocalVideoTrack.create(path)


class GatedContainer:
    def __init__(self, *, block_decode=False):
        self.started = threading.Event()
        self.release = threading.Event()
        self.decoding = False
        self.closed = False
        self.block_decode = block_decode
        self.stream = SimpleNamespace(
            average_rate=Fraction(30), base_rate=None, codec_context=SimpleNamespace(thread_count=0)
        )
        self.streams = SimpleNamespace(video=[self.stream])

    def decode(self, stream):
        assert stream is self.stream  # Audio streams must never be decoded.
        self.decoding = True
        try:
            self.started.set()
            if self.block_decode:
                assert self.release.wait(timeout=3)
            frame = av.VideoFrame(8, 8, "yuv420p")
            frame.pts, frame.time_base = 0, Fraction(1, 30)
            yield frame
        finally:
            self.decoding = False

    def close(self):
        # The generator may be suspended at yield, but the gated decoder operation
        # itself must have finished before its container can be closed.
        assert not self.block_decode or self.release.is_set()
        self.closed = True


@pytest.mark.asyncio
async def test_cancel_during_decode_waits_for_worker_before_closing(tmp_path, monkeypatch):
    path = tmp_path / "fixture.mkv"
    path.touch()
    container = GatedContainer(block_decode=True)
    monkeypatch.setattr(av, "open", lambda *_args, **_kwargs: container)
    track = await LocalVideoTrack.create(path)
    reading = asyncio.create_task(track.recv())
    try:
        assert await asyncio.to_thread(container.started.wait, 1)
        reading.cancel()
        await asyncio.sleep(0.02)
        assert not reading.done()
        assert not container.closed
        container.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(reading, timeout=1)
        assert container.closed
        assert track.readyState == "ended"
    finally:
        container.release.set()
        await asyncio.gather(reading, return_exceptions=True)
        await track.aclose()


@pytest.mark.asyncio
async def test_cancel_during_open_releases_late_container(tmp_path, monkeypatch):
    path = tmp_path / "fixture.mkv"
    path.touch()
    container = GatedContainer()
    opening_started = threading.Event()
    release_open = threading.Event()

    def open_container(*_args, **_kwargs):
        opening_started.set()
        assert release_open.wait(timeout=3)
        return container

    monkeypatch.setattr(av, "open", open_container)
    opening = asyncio.create_task(LocalVideoTrack.create(path))
    try:
        assert await asyncio.to_thread(opening_started.wait, 1)
        opening.cancel()
        await asyncio.sleep(0.02)
        assert not opening.done()
        release_open.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(opening, timeout=1)
        assert container.closed
    finally:
        release_open.set()
        await asyncio.gather(opening, return_exceptions=True)


@pytest.mark.parametrize(
    "options", [{"max_fps": 0}, {"max_fps": float("nan")}, {"max_fps": True}, {"max_width": 1}]
)
def test_rejects_invalid_output_limits(tmp_path, options):
    with pytest.raises(ValueError):
        LocalVideoTrack(tmp_path / "clip.mkv", **options)


@pytest.mark.asyncio
async def test_default_replay_targets_25_fps_without_duplicating_frames(tmp_path):
    track = await LocalVideoTrack.create(write_video(tmp_path / "25fps.mkv", frames=12))
    try:
        frames = [await track.recv() for _ in range(7)]
        assert [frame.pts for frame in frames] == [i * 3600 for i in range(7)]
        assert track._decoded_in_loop >= len(frames)
        assert track.max_fps == 25
    finally:
        await track.aclose()
