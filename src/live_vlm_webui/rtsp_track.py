"""
RTSP Video Track for IP Camera Support

SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
"""

import asyncio
import logging
import re
import threading

import av
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from av import VideoFrame

# Packet loss can cause recoverable decoder errors on IP camera streams.
av.logging.set_level(av.logging.FATAL)
logger = logging.getLogger(__name__)


class RTSPVideoTrack(VideoStreamTrack):
    """Read an RTSP camera through the same frame pipeline as webcam input.

    Async callers should use ``await RTSPVideoTrack.create(url)`` and
    ``await track.aclose()`` so opening and closing do not block other cameras.
    The synchronous constructor remains available for legacy callers.
    """

    def __init__(
        self,
        rtsp_url: str,
        reconnect_attempts: int = 5,
        reconnect_delay: float = 2.0,
        options: dict | None = None,
        *,
        open_timeout: float = 10.0,
        read_timeout: float = 10.0,
        _connect_on_init: bool = True,
    ):
        super().__init__()
        if open_timeout <= 0 or read_timeout <= 0:
            raise ValueError("RTSP open and read timeouts must be positive")
        self.rtsp_url = rtsp_url
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.open_timeout = open_timeout
        self.read_timeout = read_timeout
        self.container: av.container.InputContainer | None = None
        self.stream: av.video.VideoStream | None = None
        self._stopped = False
        self._frame_count = 0
        self.source_generation = 0
        self._frames = None
        # PyAV containers are not safe to close concurrently with demux/decode.
        self._io_lock = threading.Lock()
        self._recv_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._close_task = None
        self.options = options or {
            "rtsp_transport": "tcp",
            "max_delay": "500000",
            "rtsp_flags": "prefer_tcp",
        }
        if _connect_on_init:
            self._connect()

    @classmethod
    async def create(cls, rtsp_url: str, **kwargs):
        """Open a camera off the event loop, including cancellation cleanup."""
        track = cls(rtsp_url, _connect_on_init=False, **kwargs)
        opening = asyncio.create_task(asyncio.to_thread(track._connect))
        try:
            # Cancellation must not discard the handle to a still-opening camera.
            await asyncio.shield(opening)
            return track
        except BaseException:
            await track.aclose()
            raise
        finally:
            # A canceled caller may outlive a failed worker. Observe its exception
            # even if cleanup itself receives a second cancellation.
            opening.add_done_callback(cls._observe_worker)

    @staticmethod
    def _observe_worker(task):
        if not task.cancelled():
            task.exception()

    def _sanitize_url(self, url: str) -> str:
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)

    def _connect(self):
        """Open and initialize PyAV; all container operations share one lock."""
        with self._io_lock:
            if self._stopped:
                return
            safe_url = self._sanitize_url(self.rtsp_url)
            logger.info("Connecting to RTSP stream: %s", safe_url)
            container = None
            try:
                # PyAV's interrupt callback bounds both open and network read I/O.
                container = av.open(
                    self.rtsp_url,
                    options=self.options,
                    timeout=(self.open_timeout, self.read_timeout),
                )
                if not container.streams.video:
                    raise ValueError("No video stream found in RTSP source")
                stream = container.streams.video[0]
                if self._stopped:
                    container.close()
                    return
                self.container = container
                self.stream = stream
                self._frames = self._decode_frames(container, stream)
                self.source_generation += 1
                logger.info(
                    "RTSP connected successfully: %s %sx%s @%sfps",
                    stream.codec_context.name,
                    stream.width or "unknown",
                    stream.height or "unknown",
                    stream.average_rate or "unknown",
                )
            except BaseException:
                if container is not None:
                    try:
                        container.close()
                    except Exception:
                        logger.debug("Error closing failed RTSP connection", exc_info=True)
                self.container = None
                self.stream = None
                self._frames = None
                logger.error("Failed to connect to RTSP stream %s", safe_url)
                raise

    @staticmethod
    def _decode_frames(container, stream):
        # Keep this generator between recv() calls: a single packet can decode
        # into multiple frames, all of which must remain available to the caller.
        for packet in container.demux(stream):
            for frame in packet.decode():
                if isinstance(frame, VideoFrame):
                    yield frame

    async def recv(self) -> VideoFrame:
        """Decode one frame off the event loop, reconnecting after a read failure."""
        async with self._recv_lock:
            if self._stopped:
                raise MediaStreamError
            try:
                frame = await asyncio.to_thread(self._read_frame)
                if frame is None and not self._stopped:
                    logger.warning("RTSP stream ended unexpectedly, attempting reconnection")
                    await self._reconnect()
                    frame = await asyncio.to_thread(self._read_frame)
                if frame is None or self._stopped:
                    raise MediaStreamError
            except Exception as error:
                # aiortc's relay releases its source worker and wakes subscribers
                # only for MediaStreamError; arbitrary exceptions strand them.
                await self.aclose()
                if isinstance(error, MediaStreamError):
                    raise
                raise MediaStreamError("RTSP camera could not be read") from error
            self._frame_count += 1
            if self._frame_count % 300 == 0:
                logger.debug("RTSP: Received %s frames", self._frame_count)
            return frame

    def _read_frame(self) -> VideoFrame | None:
        """Advance the persistent decoder; never overlap a close or reconnect."""
        with self._io_lock:
            if self._stopped or self._frames is None:
                return None
            try:
                return next(self._frames, None)
            except av.error.EOFError:
                logger.warning("RTSP stream EOF")
            except Exception:
                logger.exception("Error decoding RTSP frame")
            return None

    def _close_container(self):
        with self._io_lock:
            container = self.container
            self.container = None
            self.stream = None
            self._frames = None
            if container is not None:
                try:
                    container.close()
                except Exception:
                    logger.warning("Error closing RTSP container", exc_info=True)

    async def _reconnect(self):
        """Reconnect off the event loop with bounded I/O and exponential backoff."""
        await asyncio.to_thread(self._close_container)
        for attempt in range(self.reconnect_attempts):
            if self._stopped:
                raise MediaStreamError
            if attempt > 0:
                delay = self.reconnect_delay * (2 ** (attempt - 1))
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
            if self._stopped:
                raise MediaStreamError
            try:
                await asyncio.to_thread(self._connect)
                if self._stopped:
                    raise MediaStreamError
                logger.info("RTSP reconnected successfully on attempt %s", attempt + 1)
                return
            except MediaStreamError:
                raise
            except Exception:
                logger.warning("RTSP reconnection attempt %s failed", attempt + 1)
                if attempt == self.reconnect_attempts - 1:
                    raise
        raise MediaStreamError

    def stop(self):
        """Stop delivery immediately and safely schedule container cleanup.

        In an event loop, use ``aclose()`` when cleanup must finish before returning.
        Outside an event loop this method closes the container synchronously.
        """
        self._stopped = True
        self._stop_event.set()
        super().stop()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._close_container()
        else:
            if self._close_task is None:
                self._close_task = loop.create_task(asyncio.to_thread(self._close_container))
                self._close_task.add_done_callback(self._observe_worker)

    async def aclose(self):
        """Wait for pending PyAV I/O before releasing this camera's resources."""
        self.stop()
        # Shield the cleanup worker so caller cancellation cannot strand a camera.
        await asyncio.shield(self._close_task)

    @property
    def is_connected(self) -> bool:
        return self.container is not None and not self._stopped

    def get_stats(self) -> dict:
        stats = {
            "url": self._sanitize_url(self.rtsp_url),
            "connected": self.is_connected,
            "frames_received": self._frame_count,
            "stopped": self._stopped,
        }
        stream = self.stream
        if stream:
            stats.update(
                {
                    "codec": stream.codec_context.name,
                    "width": stream.width,
                    "height": stream.height,
                    "fps": float(stream.average_rate) if stream.average_rate else None,
                }
            )
        return stats
