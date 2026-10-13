"""RTSP lifecycle tests without network access or physical cameras."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from aiortc.contrib.media import MediaRelay
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

from live_vlm_webui.rtsp_track import RTSPVideoTrack


class CameraContainer:
    def __init__(self, packets=(), *, video=True):
        self.streams = SimpleNamespace(
            video=(
                [
                    SimpleNamespace(
                        codec_context=SimpleNamespace(name="h264"),
                        width=320,
                        height=240,
                        average_rate=30,
                    )
                ]
                if video
                else []
            )
        )
        self.packets = packets
        self.demux_calls = 0
        self.close = Mock()

    def demux(self, stream):
        self.demux_calls += 1
        return iter(self.packets)


def packet(*frames):
    return SimpleNamespace(decode=lambda: frames)


async def wait_for_worker(event):
    assert await asyncio.to_thread(event.wait, 2), "RTSP worker did not start"


@pytest.mark.asyncio
async def test_factory_opens_off_loop_and_passes_io_timeouts(monkeypatch):
    container = CameraContainer()
    loop_thread = threading.get_ident()
    threads = []

    def open_camera(*args, **kwargs):
        threads.append(threading.get_ident())
        assert kwargs["timeout"] == (1.5, 2.5)
        return container

    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", open_camera)
    track = await RTSPVideoTrack.create("rtsp://camera/stream", open_timeout=1.5, read_timeout=2.5)
    try:
        assert track.is_connected
        assert len(threads) == 1
        assert threads[0] != loop_thread
    finally:
        await track.aclose()
    container.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_factory_closes_camera_that_finishes_opening(monkeypatch):
    opened = threading.Event()
    release = threading.Event()
    container = CameraContainer()

    def open_camera(*args, **kwargs):
        opened.set()
        assert release.wait(2)
        return container

    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", open_camera)
    task = asyncio.create_task(RTSPVideoTrack.create("rtsp://camera/stream"))
    try:
        await wait_for_worker(opened)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done(), "Factory must retain its opening camera until cleanup"
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 2)
    container.close.assert_called_once()


@pytest.mark.asyncio
async def test_failed_stream_validation_closes_container(monkeypatch):
    container = CameraContainer(video=False)
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", lambda *a, **kw: container)
    with pytest.raises(ValueError, match="No video stream"):
        await RTSPVideoTrack.create("rtsp://camera/audio-only")
    container.close.assert_called_once()


@pytest.mark.asyncio
async def test_each_decoded_frame_is_delivered_without_restarting_demux(monkeypatch):
    frames = [VideoFrame(2, 2, "rgb24") for _ in range(3)]
    container = CameraContainer([packet(*frames[:2]), packet(frames[2])])
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", lambda *a, **kw: container)
    track = await RTSPVideoTrack.create("rtsp://camera/stream")
    try:
        received = [await track.recv() for _ in frames]
        assert all(actual is expected for actual, expected in zip(received, frames))
        assert container.demux_calls == 1
        assert track.get_stats()["frames_received"] == 3
    finally:
        await track.aclose()


@pytest.mark.asyncio
async def test_close_waits_for_cancelled_read_without_racing_container(monkeypatch):
    reading = threading.Event()
    release = threading.Event()
    frame = VideoFrame(2, 2, "rgb24")

    def decode():
        reading.set()
        try:
            assert release.wait(2)
            return [frame]
        finally:
            reading.clear()

    container = CameraContainer([SimpleNamespace(decode=decode)])
    close_during_read = []
    container.close.side_effect = lambda: close_during_read.append(reading.is_set())
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", lambda *a, **kw: container)
    track = await RTSPVideoTrack.create("rtsp://camera/stream")
    consumer = asyncio.create_task(track.recv())
    closing = None
    try:
        await wait_for_worker(reading)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer
        closing = asyncio.create_task(track.aclose())
        await asyncio.sleep(0.01)
        assert not closing.done()
        container.close.assert_not_called()
    finally:
        release.set()
        if closing is not None:
            await asyncio.wait_for(closing, 2)
        else:
            await track.aclose()
    assert close_during_read == [False]
    assert not track.is_connected
    with pytest.raises(MediaStreamError):
        await track.recv()
    await track.aclose()
    container.close.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_open_and_close_run_off_event_loop(monkeypatch):
    frame = VideoFrame(2, 2, "rgb24")
    first = CameraContainer()
    replacement = CameraContainer([packet(frame)])
    containers = iter([first, replacement])
    operations = []
    loop_thread = threading.get_ident()

    def open_camera(*args, **kwargs):
        operations.append(threading.get_ident())
        return next(containers)

    first.close.side_effect = lambda: operations.append(threading.get_ident())
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", open_camera)
    track = await RTSPVideoTrack.create("rtsp://camera/stream")
    try:
        assert await track.recv() is frame
        first.close.assert_called_once()
        assert len(operations) == 3
        assert all(thread != loop_thread for thread in operations)
    finally:
        await track.aclose()
    replacement.close.assert_called_once()


@pytest.mark.asyncio
async def test_cancelled_close_still_finishes_cleanup(monkeypatch):
    closing = threading.Event()
    release = threading.Event()
    container = CameraContainer()

    def close_camera():
        closing.set()
        assert release.wait(2)

    container.close.side_effect = close_camera
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", lambda *a, **kw: container)
    track = await RTSPVideoTrack.create("rtsp://camera/stream")
    task = asyncio.create_task(track.aclose())
    try:
        await wait_for_worker(closing)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        release.set()
        await asyncio.wait_for(track.aclose(), 2)
    container.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("reconnect_fails", [False, True])
async def test_terminal_source_wakes_relay_and_releases_its_resources(monkeypatch, reconnect_fails):
    first = CameraContainer()
    replacement = CameraContainer()
    sources = iter([first, replacement])

    def open_camera(*args, **kwargs):
        container = next(sources)
        if container is replacement and reconnect_fails:
            raise OSError("Camera unavailable")
        return container

    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", open_camera)
    track = await RTSPVideoTrack.create("rtsp://camera/stream", reconnect_attempts=1)
    relay = MediaRelay()
    proxy = relay.subscribe(track, buffered=False)
    try:
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(proxy.recv(), 2)
        assert track.readyState == "ended"
        assert proxy.readyState == "ended"
        # MediaRelay has no public shutdown API: terminal source errors must let
        # its own reader remove both ownership dictionaries.
        assert not relay._MediaRelay__tasks
        assert not relay._MediaRelay__proxies
        first.close.assert_called_once()
        if not reconnect_fails:
            replacement.close.assert_called_once()
    finally:
        proxy.stop()
        await track.aclose()


@pytest.mark.asyncio
async def test_stopping_during_reconnect_backoff_wakes_relay_promptly(monkeypatch):
    first = CameraContainer()
    retry_started = threading.Event()
    call_count = 0

    def open_camera(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first
        retry_started.set()
        raise OSError("Camera unavailable")

    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", open_camera)
    track = await RTSPVideoTrack.create(
        "rtsp://camera/stream", reconnect_attempts=5, reconnect_delay=30
    )
    relay = MediaRelay()
    proxy = relay.subscribe(track, buffered=False)
    consumer = asyncio.create_task(proxy.recv())
    try:
        await wait_for_worker(retry_started)
        # Give the relay reader an opportunity to enter its long retry delay.
        await asyncio.sleep(0.01)
        await track.aclose()
        with pytest.raises(MediaStreamError):
            await asyncio.wait_for(consumer, 1)
        assert call_count == 2
        assert not relay._MediaRelay__tasks
        assert not relay._MediaRelay__proxies
    finally:
        proxy.stop()
        await track.aclose()
        consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)


async def test_source_generation_changes_only_on_successful_connections(monkeypatch):
    first_frame = VideoFrame(2, 2, "rgb24")
    second_frame = VideoFrame(2, 2, "rgb24")
    first_frame.pts, second_frame.pts = 100, 101
    containers = iter(
        [
            CameraContainer([packet(first_frame)]),
            CameraContainer(video=False),
            CameraContainer([packet(second_frame)]),
        ]
    )
    monkeypatch.setattr("live_vlm_webui.rtsp_track.av.open", lambda *a, **kw: next(containers))
    track = RTSPVideoTrack("rtsp://camera/stream", _connect_on_init=False)
    try:
        assert track.source_generation == 0
        await asyncio.to_thread(track._connect)
        assert track.source_generation == 1
        assert await track.recv() is first_frame
        await asyncio.to_thread(track._close_container)
        with pytest.raises(ValueError, match="No video stream"):
            await asyncio.to_thread(track._connect)
        assert track.source_generation == 1
        # recv reconnects internally; the new frame retains increasing PTS.
        assert await track.recv() is second_frame
        assert second_frame.pts > first_frame.pts
        assert track.source_generation == 2
    finally:
        await track.aclose()
