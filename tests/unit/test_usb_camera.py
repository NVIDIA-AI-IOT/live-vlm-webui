"""USB discovery, bounded delivery and capture cleanup without camera hardware."""

import asyncio
import ctypes
import errno
import queue
import struct
import threading
from fractions import Fraction
from types import SimpleNamespace

import av
import pytest
from aiortc.mediastreams import MediaStreamError

from live_vlm_webui import usb_camera as usb


def mode(pixel_format="MJPG", width=640, height=480, fps=30):
    return usb._CaptureMode(usb._fourcc(pixel_format), width, height, Fraction(1, fps))


def packet(values, size=64, offset=0):
    data = bytearray(size)
    struct.pack_into("=" + "I" * len(values), data, offset, *values)
    return data


@pytest.mark.parametrize(
    "value", [None, 2, "", "/dev/video0", "../video0", "video0/../video2", "video0\n", "video-1"]
)
def test_resolver_rejects_paths_and_malformed_ids_before_discovery(monkeypatch, value):
    monkeypatch.setattr(usb, "discover_usb_cameras", lambda: pytest.fail("must validate first"))
    with pytest.raises(ValueError, match="device ID"):
        usb.resolve_usb_camera(value)


def test_resolver_only_allows_currently_discovered_devices(monkeypatch):
    monkeypatch.setattr(
        usb, "discover_usb_cameras", lambda: [{"device_id": "video2", "label": "C920"}]
    )
    assert usb.resolve_usb_camera("video2") == usb._DEVICE_ROOT / "video2"
    with pytest.raises(ValueError, match="unavailable"):
        usb.resolve_usb_camera("video0")


def test_discovery_requires_usb_capture_capability_readable_node_and_supported_mode(
    tmp_path, monkeypatch
):
    sysfs = tmp_path / "video4linux"
    parent = tmp_path / "usb-device"
    parent.mkdir()
    (parent / "serial").write_text("CAMERA-SERIAL\n")
    for name in ("video0", "video1", "video2", "video3", "video4", "video5", "videoBAD"):
        (sysfs / name).mkdir(parents=True)
    closed = []
    monkeypatch.setattr(usb, "_SYSFS_ROOT", sysfs)
    monkeypatch.setattr(usb, "_usb_parent", lambda node: None if node.name == "video4" else parent)

    def open_device(path):
        number = int(path.name.removeprefix("video"))
        if number == 2:
            raise PermissionError("permission denied")
        return number

    def capabilities(fd):
        return (
            (usb._VIDEO_CAPTURE if fd != 1 else 0x00800000) | usb._CAP_STREAMING,
            "Test USB camera",
        )

    def select_mode(fd):
        if fd == 3:
            raise ValueError("unsupported")
        return mode()

    monkeypatch.setattr(usb, "_device_fd", open_device)
    monkeypatch.setattr(usb, "_capabilities", capabilities)
    monkeypatch.setattr(usb, "_select_mode", select_mode)
    monkeypatch.setattr(usb.os, "close", closed.append)
    assert usb.discover_usb_cameras() == [
        {"device_id": "video0", "label": "Test USB camera (CAMERA-SERIAL)"},
        {"device_id": "video5", "label": "Test USB camera (CAMERA-SERIAL)"},
    ]
    assert closed == [0, 1, 3, 5]


def test_usb_parent_requires_real_usb_ancestry(tmp_path):
    parent = tmp_path / "camera"
    parent.mkdir()
    (parent / "idVendor").write_text("046d")
    (parent / "idProduct").write_text("082d")
    subsystem = tmp_path / "usb"
    subsystem.mkdir()
    (parent / "subsystem").symlink_to(subsystem)
    interface = parent / "interface"
    interface.mkdir()
    node = tmp_path / "video0"
    node.mkdir()
    (node / "device").symlink_to(interface)
    assert usb._usb_parent(node) == parent
    (parent / "subsystem").unlink()
    (parent / "subsystem").symlink_to(tmp_path / "pci")
    assert usb._usb_parent(node) is None


def test_mode_prefers_compressed_capture_and_its_fastest_bounded_interval(monkeypatch):
    monkeypatch.setattr(
        usb,
        "_enumerate",
        lambda *_: [
            packet([usb._fourcc("YUYV")], offset=44),
            packet([usb._fourcc("MJPG")], offset=44),
        ],
    )
    monkeypatch.setattr(usb, "_frame_sizes", lambda *_: [(320, 240), (640, 480)])
    monkeypatch.setattr(usb, "_frame_intervals", lambda *args: [Fraction(1, 15), Fraction(1, 30)])
    assert usb._select_mode(9) == mode()


def test_raw_fallback_limits_usb_bandwidth_and_is_explicitly_enumerated(monkeypatch):
    monkeypatch.setattr(usb, "_enumerate", lambda *_: [packet([usb._fourcc("YUYV")], offset=44)])
    monkeypatch.setattr(usb, "_frame_sizes", lambda *_: [(640, 480)])
    limits = []

    def intervals(*args):
        limits.append(args[-1])
        return [Fraction(1, args[-1])]

    monkeypatch.setattr(usb, "_frame_intervals", intervals)
    assert usb._select_mode(9) == mode("YUYV", fps=15)
    assert limits == [15]


def test_only_enumerated_formats_are_used(monkeypatch):
    monkeypatch.setattr(usb, "_enumerate", lambda *_: [packet([usb._fourcc("H264")], offset=44)])
    with pytest.raises(ValueError, match="no supported"):
        usb._select_mode(9)


def test_discrete_sizes_reject_large_and_odd_geometry(monkeypatch):
    monkeypatch.setattr(
        usb,
        "_enumerate",
        lambda *_: [
            packet([1, *size], size=44, offset=8) for size in [(1920, 1080), (640, 480), (319, 240)]
        ],
    )
    assert list(usb._frame_sizes(9, usb._fourcc("MJPG"))) == [(640, 480)]


def test_stepwise_sizes_use_supported_alignment(monkeypatch):
    monkeypatch.setattr(
        usb, "_enumerate", lambda *_: [packet([3, 160, 1920, 16, 120, 1080, 16], size=44, offset=8)]
    )
    assert list(usb._frame_sizes(9, usb._fourcc("MJPG"))) == [(640, 472)]


def test_discrete_intervals_exclude_excessive_fps(monkeypatch):
    monkeypatch.setattr(
        usb,
        "_enumerate",
        lambda *_: [packet([1, 1, fps], size=52, offset=16) for fps in (60, 30, 15)],
    )
    assert list(usb._frame_intervals(9, 0, 640, 480, 30)) == [Fraction(1, 30), Fraction(1, 15)]


def test_stepwise_intervals_round_to_supported_slower_rate(monkeypatch):
    monkeypatch.setattr(
        usb, "_enumerate", lambda *_: [packet([3, 1, 60, 1, 5, 1, 60], size=52, offset=16)]
    )
    assert list(usb._frame_intervals(9, 0, 640, 480, 25)) == [Fraction(1, 20)]


class FakeCapture:
    def __init__(self):
        self.frames = queue.Queue()
        self.opened = threading.Event()
        self.closed = threading.Event()
        self.read_entered = threading.Event()
        self.release_close = threading.Event()
        self.release_close.set()
        self.worker_thread = None

    def factory(self, _device_id):
        self.worker_thread = threading.get_ident()
        self.opened.set()
        return self

    def read(self, timeout):
        self.read_entered.set()
        try:
            value = self.frames.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(value, Exception):
            raise value
        return value

    def close(self):
        assert threading.get_ident() == self.worker_thread
        self.release_close.wait(timeout=3)
        self.closed.set()

    def put(self):
        frame = av.VideoFrame(width=8, height=8, format="yuv420p")
        self.frames.put(frame)
        return frame


async def wait_until(predicate):
    async with asyncio.timeout(3):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_thread_and_latest_frame_preserve_originals_with_monotonic_timestamps(monkeypatch):
    source = FakeCapture()
    monkeypatch.setattr(usb, "_V4L2Capture", source.factory)
    first = source.put()
    track = await usb.USBVideoTrack.create("video0")
    try:
        assert source.worker_thread != threading.get_ident()
        assert await track.recv() is first
        previous_pts = first.pts
        for _ in range(100):
            latest = source.put()
        await wait_until(lambda: source.frames.empty() and track._frame is latest)
        assert await track.recv() is latest
        assert latest.pts > previous_pts
        assert latest.time_base == Fraction(1, 90000)
        waiting = asyncio.create_task(track.recv())
        await asyncio.sleep(0)
        assert not waiting.done()
        track.stop()
        with pytest.raises(MediaStreamError):
            await waiting
    finally:
        await track.aclose()
    assert source.closed.is_set()
    assert track._worker.done()


@pytest.mark.asyncio
async def test_producer_coalesces_callbacks_when_event_loop_cannot_keep_up(monkeypatch):
    track = usb.USBVideoTrack("video0")
    callbacks = []
    track._loop = SimpleNamespace(call_soon_threadsafe=callbacks.append)
    for _ in range(1000):
        frame = av.VideoFrame(width=8, height=8, format="yuv420p")
        track._schedule_delivery(frame)
    assert len(callbacks) == 1
    assert track._pending_frame is frame
    callbacks.pop()()
    assert await track.recv() is frame
    assert track._pending_frame is None
    track.stop()
    callbacks.pop()()


@pytest.mark.asyncio
async def test_unplug_wakes_receivers_and_releases_capture(monkeypatch):
    source = FakeCapture()
    monkeypatch.setattr(usb, "_V4L2Capture", source.factory)
    source.put()
    track = await usb.USBVideoTrack.create("video0")
    await track.recv()
    waiting = asyncio.create_task(track.recv())
    source.frames.put(OSError(errno.ENODEV, "camera unplugged"))
    with pytest.raises(MediaStreamError, match="unplugged"):
        await asyncio.wait_for(waiting, 2)
    await track.aclose()
    assert source.closed.is_set()


@pytest.mark.asyncio
async def test_open_failure_is_reported_without_waiting_for_first_frame(monkeypatch):
    def fail(_device_id):
        raise PermissionError("camera unavailable")

    monkeypatch.setattr(usb, "_V4L2Capture", fail)
    with pytest.raises(MediaStreamError, match="unavailable"):
        await asyncio.wait_for(usb.USBVideoTrack.create("video0"), 2)


@pytest.mark.asyncio
async def test_canceling_start_still_closes_capture(monkeypatch):
    source = FakeCapture()
    monkeypatch.setattr(usb, "_V4L2Capture", source.factory)
    opening = asyncio.create_task(usb.USBVideoTrack.create("video0"))
    await wait_until(source.read_entered.is_set)
    opening.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(opening, 2)
    assert source.closed.is_set()


@pytest.mark.asyncio
async def test_repeated_close_cancellation_does_not_abandon_worker(monkeypatch):
    source = FakeCapture()
    monkeypatch.setattr(usb, "_V4L2Capture", source.factory)
    source.put()
    track = await usb.USBVideoTrack.create("video0")
    source.release_close.clear()
    closing = asyncio.create_task(track.aclose())
    try:
        await asyncio.sleep(0.02)
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        assert not closing.done()
        assert not source.closed.is_set()
        source.release_close.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, 2)
        assert source.closed.is_set()
        assert track._worker.done()
    finally:
        source.release_close.set()
        await track.aclose()


def test_partial_v4l2_setup_closes_fd(monkeypatch):
    closed = []
    monkeypatch.setattr(usb, "resolve_usb_camera", lambda _id: usb._DEVICE_ROOT / "video0")
    monkeypatch.setattr(usb, "_device_fd", lambda _: 19)
    monkeypatch.setattr(
        usb, "_capabilities", lambda _: (usb._VIDEO_CAPTURE | usb._CAP_STREAMING, "camera")
    )
    monkeypatch.setattr(usb, "_select_mode", lambda _: mode())
    monkeypatch.setattr(usb.os, "close", closed.append)

    def failed_ioctl(*_args):
        raise OSError(errno.EBUSY, "camera in use")

    monkeypatch.setattr(usb, "_ioctl", failed_ioctl)
    with pytest.raises(OSError, match="in use"):
        usb._V4L2Capture("video0")
    assert closed == [19]


def test_linux_native_structures_have_expected_64_bit_layout():
    if ctypes.sizeof(ctypes.c_void_p) != 8:
        pytest.skip("64-bit target layout")
    assert ctypes.sizeof(usb._Buffer) == 88
    assert ctypes.sizeof(usb._Format) == 208
    assert usb._Buffer.m.offset == 64
    assert usb._Format.fmt.offset == 8


@pytest.mark.asyncio
async def test_unsupported_platform_has_empty_catalog_and_clear_capture_error(monkeypatch):
    monkeypatch.setattr(usb, "_SUPPORTED", False)
    assert usb.discover_usb_cameras() == []
    with pytest.raises(ValueError, match="requires Linux"):
        usb.resolve_usb_camera("video0")
    with pytest.raises(MediaStreamError, match="requires Linux"):
        await usb.USBVideoTrack.create("video0")


@pytest.mark.asyncio
async def test_stalled_camera_fails_and_releases_capture(monkeypatch):
    source = FakeCapture()
    monkeypatch.setattr(usb, "_V4L2Capture", source.factory)
    clock = iter([1.0, 2.0, 3.0, 4.0, 5.0])
    monkeypatch.setattr(usb, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    with pytest.raises(MediaStreamError, match="stopped delivering"):
        await asyncio.wait_for(usb.USBVideoTrack.create("video0"), 2)
    assert source.closed.is_set()


def test_capture_close_releases_mappings_even_after_unplug(monkeypatch):
    capture = object.__new__(usb._V4L2Capture)
    capture.fd = 19
    capture.streaming = True
    capture.decoder = object()
    closed = []
    capture.maps = [SimpleNamespace(close=lambda: closed.append("mapping"))]

    def unplugged(*_args, **_kwargs):
        raise OSError(errno.ENODEV, "camera unplugged")

    monkeypatch.setattr(usb, "_ioctl", unplugged)
    monkeypatch.setattr(usb.os, "close", lambda fd: closed.append(fd))
    capture.close()
    capture.close()
    assert closed == ["mapping", 19]
    assert capture.fd is None
    assert capture.decoder is None


def test_kernel_queue_is_drained_and_only_latest_complete_packet_is_decoded(monkeypatch):
    capture = object.__new__(usb._V4L2Capture)
    capture.fd = 19
    capture.maps = [b"first", b"second", b"third"]
    capture.mode = mode()
    sequence = iter(range(3))
    queued = []
    decoded = []
    expected = av.VideoFrame(width=8, height=8, format="yuv420p")

    def ioctl(_fd, number, buffer):
        if number == 17:
            buffer.index = next(sequence)
            buffer.bytesused = len(capture.maps[buffer.index])
        else:
            assert number == 15
            queued.append(buffer.index)

    def decode(packet):
        decoded.append(bytes(packet))
        return [expected]

    capture.decoder = SimpleNamespace(decode=decode)
    monkeypatch.setattr(usb.select, "select", lambda *_: ([19], [], []))
    monkeypatch.setattr(usb, "_ioctl", ioctl)
    assert capture.read(0.1) is expected
    assert queued == [0, 1, 2]
    assert decoded == [b"third"]


def test_error_buffer_is_requeued_without_decoding_stale_data(monkeypatch):
    capture = object.__new__(usb._V4L2Capture)
    capture.fd = 19
    capture.maps = [b"bad"]
    queued = []

    def ioctl(_fd, number, buffer):
        if number == 17:
            buffer.index = 0
            buffer.bytesused = 3
            buffer.flags = 0x40
        else:
            queued.append(buffer.index)

    monkeypatch.setattr(usb.select, "select", lambda *_: ([19], [], []))
    monkeypatch.setattr(usb, "_ioctl", ioctl)
    assert capture.read(0.1) is None
    assert queued == [0]
