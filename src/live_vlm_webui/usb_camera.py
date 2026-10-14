# SPDX-License-Identifier: Apache-2.0
"""USB cameras attached to the server, using bounded V4L2 capture and CPU decoding.

Only discoverable USB video-capture nodes are accepted. MJPEG at 640x480 and
30 FPS is preferred to avoid exhausting a shared USB bus; enumerated smaller
modes are supported, with raw YUYV limited to 15 FPS. A capture worker owns all
V4L2 mappings and decoding. Viewers receive the latest complete original frame,
without accumulating old frames or scheduling one event-loop callback per frame.
"""

import asyncio
import ctypes
import errno
import mmap
import os
import re
import select
import stat
import struct
import sys
import threading
import time
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError

try:
    import fcntl
except ImportError:  # Browser and RTSP sources remain usable on non-Linux hosts.
    fcntl = None

_SUPPORTED = sys.platform.startswith("linux") and fcntl is not None


def _configured_device_root():
    """Use the host-managed device view when running inside the Thor container."""
    value = os.environ.get("LIVE_VLM_USB_DEVICE_ROOT")
    if value is None:
        return Path("/dev")
    if not value or "\0" in value or not Path(value).is_absolute():
        raise ValueError("LIVE_VLM_USB_DEVICE_ROOT must be an absolute directory path")
    return Path(value)


_SYSFS_ROOT = Path("/sys/class/video4linux")
_DEVICE_ROOT = _configured_device_root()
_DEVICE_ID = re.compile(r"video[0-9]+\Z")
_VIDEO_CAPTURE = 1
_MEMORY_MMAP = 1
_CAP_STREAMING = 0x04000000
_CAP_DEVICE_CAPS = 0x80000000
_TIME_BASE = Fraction(1, 90000)


# Linux UAPI layouts from videodev2.h. ctypes preserves native pointer/timeval
# alignment on both aarch64 and x86_64 instead of assuming ioctl buffer offsets.
class _Timeval(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_long), ("microseconds", ctypes.c_long)]


class _BufferMemory(ctypes.Union):
    _fields_ = [
        ("offset", ctypes.c_uint32),
        ("userptr", ctypes.c_ulong),
        ("planes", ctypes.c_void_p),
    ]


class _Buffer(ctypes.Structure):
    _fields_ = [
        ("index", ctypes.c_uint32),
        ("type", ctypes.c_uint32),
        ("bytesused", ctypes.c_uint32),
        ("flags", ctypes.c_uint32),
        ("field", ctypes.c_uint32),
        ("timestamp", _Timeval),
        ("timecode", ctypes.c_uint8 * 16),
        ("sequence", ctypes.c_uint32),
        ("memory", ctypes.c_uint32),
        ("m", _BufferMemory),
        ("length", ctypes.c_uint32),
        ("reserved2", ctypes.c_uint32),
        ("request_fd", ctypes.c_int32),
    ]


class _FormatData(ctypes.Union):
    _fields_ = [("data", ctypes.c_uint8 * 200), ("alignment", ctypes.c_void_p)]


class _Format(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint32), ("fmt", _FormatData)]


def _ioctl(fd, number, buffer, *, read=True):
    """Issue a Linux V4L2 request with the native structure's exact size."""
    size = len(buffer) if isinstance(buffer, bytearray) else ctypes.sizeof(buffer)
    command = ((3 if read else 1) << 30) | (size << 16) | (ord("V") << 8) | number
    return fcntl.ioctl(fd, command, buffer, True)


def _fourcc(name):
    return int.from_bytes(name.encode("ascii"), "little")


def _capabilities(fd):
    data = bytearray(104)
    # QUERYCAP is read-only, unlike most video ioctl structures.
    fcntl.ioctl(fd, (2 << 30) | (len(data) << 16) | (ord("V") << 8), data, True)
    capabilities, device_caps = struct.unpack_from("=II", data, 84)
    capabilities = device_caps if capabilities & _CAP_DEVICE_CAPS else capabilities
    card = bytes(data[16:48]).split(b"\0", 1)[0].decode("utf-8", errors="replace")
    return capabilities, card


def _enumerate(fd, number, size, fields):
    for index in range(256):
        data = bytearray(size)
        struct.pack_into("=" + "I" * (len(fields) + 1), data, 0, index, *fields)
        try:
            _ioctl(fd, number, data)
        except OSError as exc:
            if exc.errno == errno.EINVAL:
                return
            raise
        yield data


def _frame_sizes(fd, pixel_format):
    for data in _enumerate(fd, 74, 44, (pixel_format,)):
        kind = struct.unpack_from("=I", data, 8)[0]
        if kind == 1:
            width, height = struct.unpack_from("=II", data, 12)
            if 2 <= width <= 640 and 2 <= height <= 480 and not (width % 2 or height % 2):
                yield width, height
        elif kind in (2, 3):
            low_w, high_w, step_w, low_h, high_h, step_h = struct.unpack_from("=6I", data, 12)
            if low_w <= 640 and low_h <= 480:
                step_w, step_h = max(1, step_w), max(1, step_h)
                width = low_w + (min(640, high_w) - low_w) // step_w * step_w
                height = low_h + (min(480, high_h) - low_h) // step_h * step_h
                if width >= 2 and height >= 2 and not (width % 2 or height % 2):
                    yield width, height
            return


def _frame_intervals(fd, pixel_format, width, height, max_fps):
    minimum = Fraction(1, max_fps)
    for data in _enumerate(fd, 75, 52, (pixel_format, width, height)):
        kind = struct.unpack_from("=I", data, 16)[0]
        values = struct.unpack_from("=6I", data, 20)
        if kind == 1 and values[0] and values[1]:
            interval = Fraction(values[0], values[1])
            if minimum <= interval <= 1:
                yield interval
        elif kind in (2, 3) and all(values[index] for index in (0, 1, 2, 3)):
            low, high = Fraction(values[0], values[1]), Fraction(values[2], values[3])
            interval = max(minimum, low)
            if kind == 3 and values[4] and values[5]:
                step = Fraction(values[4], values[5])
                steps = -(-(interval - low) // step)
                interval = low + steps * step
            if interval <= min(high, Fraction(1)):
                yield interval
            return


@dataclass(frozen=True)
class _CaptureMode:
    pixel_format: int
    width: int
    height: int
    interval: Fraction


def _select_mode(fd):
    formats = {
        struct.unpack_from("=I", data, 44)[0] for data in _enumerate(fd, 2, 64, (_VIDEO_CAPTURE,))
    }
    # Compressed MJPEG is strongly preferred, even over a larger raw mode.
    for name, max_fps in (("MJPG", 30), ("YUYV", 15)):
        pixel_format = _fourcc(name)
        if pixel_format not in formats:
            continue
        sizes = sorted(
            set(_frame_sizes(fd, pixel_format)), key=lambda size: size[0] * size[1], reverse=True
        )
        for width, height in sizes:
            intervals = list(_frame_intervals(fd, pixel_format, width, height, max_fps))
            if intervals:
                return _CaptureMode(pixel_format, width, height, min(intervals))
    raise ValueError("USB camera has no supported MJPEG/YUYV mode up to 640x480")


def _usb_parent(node):
    try:
        location = (node / "device").resolve(strict=True)
        for parent in (location, *location.parents):
            if (
                (parent / "idVendor").is_file()
                and (parent / "idProduct").is_file()
                and (parent / "subsystem").resolve().name == "usb"
            ):
                return parent
    except OSError:
        pass
    return None


def _device_fd(path):
    node = _SYSFS_ROOT / path.name
    parent = _usb_parent(node)
    if not _DEVICE_ID.fullmatch(path.name) or parent is None:
        raise ValueError("USB camera must have a current USB video device entry")
    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        identity = os.fstat(fd)
        if not stat.S_ISCHR(identity.st_mode):
            raise ValueError("USB camera must be a video device")
        # A hotplug view can briefly contain an old node while udev reconciles it.
        # Compare the opened descriptor with live sysfs before issuing any ioctl.
        number = (node / "dev").read_text().strip()
        if not re.fullmatch(r"[0-9]+:[0-9]+", number):
            raise ValueError("USB camera has an invalid sysfs device number")
        major, minor = map(int, number.split(":"))
        if (os.major(identity.st_rdev), os.minor(identity.st_rdev)) != (major, minor):
            raise ValueError("USB camera device does not match its current sysfs entry")
        if _usb_parent(node) != parent:
            raise ValueError("USB camera changed while opening its device")
    except BaseException:
        os.close(fd)
        raise
    return fd


def discover_usb_cameras():
    """List accessible USB capture nodes, excluding metadata and unsupported modes.

    Discovery only queries capabilities/formats; it never starts camera streaming.
    Device IDs are server-local names, not paths supplied by an HTTP client.
    """
    if not _SUPPORTED:
        return []
    cameras = []
    for node in sorted(_SYSFS_ROOT.glob("video*"), key=lambda item: item.name):
        if not _DEVICE_ID.fullmatch(node.name):
            continue
        parent = _usb_parent(node)
        if parent is None:
            continue
        fd = None
        try:
            fd = _device_fd(_DEVICE_ROOT / node.name)
            caps, card = _capabilities(fd)
            if not (caps & _VIDEO_CAPTURE and caps & _CAP_STREAMING):
                continue
            _select_mode(fd)
            try:
                serial = (parent / "serial").read_text().strip()[:128]
            except OSError:
                serial = ""
            label = card or node.name
            if serial:
                label += f" ({serial})"
            cameras.append({"device_id": node.name, "label": label})
        except (OSError, ValueError):
            # Metadata, busy/unreadable devices and unsupported formats are not options.
            continue
        finally:
            if fd is not None:
                os.close(fd)
    return cameras


def resolve_usb_camera(device_id):
    """Resolve only a currently discovered USB capture ID; never accept paths."""
    if not _SUPPORTED:
        raise ValueError("Server USB camera capture requires Linux with V4L2")
    if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
        raise ValueError("USB camera must be a discovered video device ID")
    if not any(item["device_id"] == device_id for item in discover_usb_cameras()):
        raise ValueError("USB camera is unavailable; refresh the camera list")
    return _DEVICE_ROOT / device_id


class _V4L2Capture:
    """One worker exclusively owns its nonblocking fd, MMAP buffers and decoder."""

    def __init__(self, device_id):
        self.fd = None
        self.maps = []
        self.streaming = False
        self.decoder = None
        self.mode = None
        self.bytes_per_line = 0
        try:
            self.fd = _device_fd(resolve_usb_camera(device_id))
            caps, _ = _capabilities(self.fd)
            if not (caps & _VIDEO_CAPTURE and caps & _CAP_STREAMING):
                raise ValueError("USB camera no longer supports video capture")
            self.mode = _select_mode(self.fd)
            fmt = _Format()
            fmt.type = _VIDEO_CAPTURE
            struct.pack_into(
                "=IIII",
                fmt.fmt.data,
                0,
                self.mode.width,
                self.mode.height,
                self.mode.pixel_format,
                0,
            )
            _ioctl(self.fd, 5, fmt)
            width, height, pixel_format, _, stride = struct.unpack_from("=5I", fmt.fmt.data)
            if (width, height, pixel_format) != (
                self.mode.width,
                self.mode.height,
                self.mode.pixel_format,
            ):
                raise ValueError("USB camera rejected its enumerated capture format")
            self.bytes_per_line = stride
            parameters = bytearray(204)
            struct.pack_into("=I", parameters, 0, _VIDEO_CAPTURE)
            struct.pack_into(
                "=II", parameters, 12, self.mode.interval.numerator, self.mode.interval.denominator
            )
            _ioctl(self.fd, 22, parameters)
            numerator, denominator = struct.unpack_from("=II", parameters, 12)
            if (
                not numerator
                or not denominator
                or Fraction(numerator, denominator) < self.mode.interval
            ):
                raise ValueError("USB camera rejected the bounded capture frame rate")
            request = bytearray(20)
            struct.pack_into("=III", request, 0, 3, _VIDEO_CAPTURE, _MEMORY_MMAP)
            _ioctl(self.fd, 8, request)
            count = struct.unpack_from("=I", request)[0]
            if not 1 <= count <= 8:
                raise ValueError("USB camera returned an invalid capture buffer count")
            for index in range(count):
                buffer = _Buffer(index=index, type=_VIDEO_CAPTURE, memory=_MEMORY_MMAP)
                _ioctl(self.fd, 9, buffer)
                if not 0 < buffer.length <= 16 * 1024 * 1024:
                    raise ValueError("USB camera returned an invalid capture buffer size")
                self.maps.append(
                    mmap.mmap(
                        self.fd,
                        buffer.length,
                        flags=mmap.MAP_SHARED,
                        prot=mmap.PROT_READ | mmap.PROT_WRITE,
                        offset=buffer.m.offset,
                    )
                )
                _ioctl(self.fd, 15, buffer)
            if self.mode.pixel_format == _fourcc("MJPG"):
                self.decoder = av.CodecContext.create("mjpeg", "r")
                self.decoder.thread_count = 1
            _ioctl(self.fd, 18, bytearray(struct.pack("=I", _VIDEO_CAPTURE)), read=False)
            self.streaming = True
        except BaseException:
            self.close()
            raise

    def read(self, timeout):
        readable, _, _ = select.select([self.fd], [], [], timeout)
        if not readable:
            return None
        # Drain the finite kernel queue, retaining only its newest complete packet.
        packet = None
        for _ in range(len(self.maps)):
            buffer = _Buffer(type=_VIDEO_CAPTURE, memory=_MEMORY_MMAP)
            try:
                _ioctl(self.fd, 17, buffer)
            except BlockingIOError:
                break
            try:
                if buffer.index >= len(self.maps) or buffer.bytesused > len(
                    self.maps[buffer.index]
                ):
                    raise ValueError("USB camera returned an invalid capture buffer")
                if buffer.bytesused and not (buffer.flags & 0x40):  # V4L2_BUF_FLAG_ERROR
                    packet = self.maps[buffer.index][: buffer.bytesused]
            finally:
                _ioctl(self.fd, 15, buffer)
        if packet is None:
            return None
        if self.decoder is not None:
            frames = self.decoder.decode(av.Packet(packet))
            return frames[-1] if frames else None
        row_bytes = self.mode.width * 2
        stride = max(row_bytes, self.bytes_per_line)
        if len(packet) < stride * self.mode.height:
            raise ValueError("USB camera returned an incomplete YUYV frame")
        pixels = np.frombuffer(packet, dtype=np.uint8, count=stride * self.mode.height)
        pixels = pixels.reshape(self.mode.height, stride)[:, :row_bytes]
        pixels = np.ascontiguousarray(pixels).reshape(self.mode.height, self.mode.width, 2)
        return av.VideoFrame.from_ndarray(pixels, format="yuyv422")

    def close(self):
        if self.fd is not None and self.streaming:
            try:
                _ioctl(self.fd, 19, bytearray(struct.pack("=I", _VIDEO_CAPTURE)), read=False)
            except OSError:
                pass  # Unplugged cameras still release their mappings and descriptor.
            self.streaming = False
        try:
            for mapping in self.maps:
                mapping.close()
        finally:
            self.maps.clear()
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            self.decoder = None


class USBVideoTrack(VideoStreamTrack):
    """Latest-frame track for a USB camera attached to the server, not the browser."""

    def __init__(self, device_id):
        super().__init__()
        if not isinstance(device_id, str) or not _DEVICE_ID.fullmatch(device_id):
            raise ValueError("USB camera must be a discovered video device ID")
        self.device_id = device_id
        self.source_generation = 1
        self._loop = asyncio.get_running_loop()
        self._stop_requested = threading.Event()
        self._state_lock = threading.Lock()
        self._pending_frame = None
        self._notification_pending = False
        self._finished = False
        self._failure = None
        self._frame = None
        self._available = asyncio.Event()
        self._opened = asyncio.Event()
        self._recv_lock = asyncio.Lock()
        self._worker = None

    @classmethod
    async def create(cls, device_id):
        if not _SUPPORTED:
            raise MediaStreamError("Server USB camera capture requires Linux with V4L2")
        track = cls(device_id)
        track._worker = asyncio.create_task(asyncio.to_thread(track._capture))
        track._worker.add_done_callback(cls._observe_worker)
        try:
            await asyncio.wait_for(track._opened.wait(), timeout=10)
            if track._finished or track._stop_requested.is_set():
                raise MediaStreamError(track._failure or "USB camera could not be opened")
            return track
        except BaseException:
            await track.aclose()
            raise

    @staticmethod
    def _observe_worker(task):
        if not task.cancelled():
            task.exception()

    def _schedule_delivery(self, frame=None, *, finished=False, failure=None):
        with self._state_lock:
            if frame is not None and not self._stop_requested.is_set():
                self._pending_frame = frame
            if finished:
                self._finished = True
                self._failure = failure
            if self._notification_pending:
                return
            self._notification_pending = True
        self._loop.call_soon_threadsafe(self._deliver)

    def _deliver(self):
        with self._state_lock:
            frame, self._pending_frame = self._pending_frame, None
            self._notification_pending = False
            terminal = self._finished or self._stop_requested.is_set()
        if terminal:
            self._frame = None
            self._opened.set()
            self._available.set()
        elif frame is not None:
            self._frame = frame
            self._opened.set()
            self._available.set()

    def _capture(self):
        source = None
        failure = None
        try:
            source = _V4L2Capture(self.device_id)
            started = None
            last_pts = -1
            last_frame_at = time.monotonic()
            while not self._stop_requested.is_set():
                frame = source.read(0.1)
                now = time.monotonic()
                if frame is None:
                    if now - last_frame_at > 3:
                        raise MediaStreamError("USB camera stopped delivering frames")
                    continue
                last_frame_at = now
                if started is None:
                    started = now
                frame.pts = max(last_pts + 1, round((now - started) / _TIME_BASE))
                frame.time_base = _TIME_BASE
                last_pts = frame.pts
                self._schedule_delivery(frame)
        except Exception as exc:  # noqa: BLE001 - report driver/decoder errors to recv's owner
            failure = f"USB camera {self.device_id} could not be read: {exc}"
        finally:
            try:
                if source is not None:
                    source.close()
            finally:
                self._schedule_delivery(finished=True, failure=failure)

    async def recv(self):
        async with self._recv_lock:
            while True:
                if self._stop_requested.is_set() or self._finished:
                    raise MediaStreamError(self._failure or "USB camera is stopped")
                await self._available.wait()
                self._available.clear()
                if self._stop_requested.is_set() or self._finished:
                    raise MediaStreamError(self._failure or "USB camera is stopped")
                frame, self._frame = self._frame, None
                if frame is not None:
                    return frame

    def stop(self):
        self._stop_requested.set()
        super().stop()
        self._schedule_delivery()

    async def aclose(self):
        """Own worker completion even when the caller is repeatedly canceled."""
        self.stop()
        if self._worker is not None:
            try:
                await asyncio.shield(self._worker)
            except asyncio.CancelledError:
                while not self._worker.done():
                    try:
                        await asyncio.shield(self._worker)
                    except asyncio.CancelledError:
                        continue
                self._worker.result()
                raise
