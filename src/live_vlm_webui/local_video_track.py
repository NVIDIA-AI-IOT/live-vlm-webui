# SPDX-License-Identifier: Apache-2.0
"""Paced, looping video-only replay for approved local demonstration clips."""

import asyncio
import math
import threading
import time
from fractions import Fraction
from pathlib import Path

import av
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError

VIDEO_TIME_BASE = Fraction(1, 90000)


class LocalVideoTrack(VideoStreamTrack):
    """Replay one local video with continuous timestamps and bounded decoding.

    Defaults limit the complete inference input and preview to 25 FPS and an
    aspect-preserving 960x540 fit, without upscaling. Only video is decoded; each
    recv() decodes on demand off the event loop and keeps at most one output frame.
    This is CPU decoding, not a hardware-decoding implementation.
    """

    def __init__(self, path, *, max_fps=25, max_width=960, max_height=540):
        super().__init__()
        if (
            isinstance(max_fps, bool)
            or not isinstance(max_fps, (int, float))
            or not math.isfinite(max_fps)
            or not 0 < max_fps <= 60
        ):
            raise ValueError("max_fps must be a finite number greater than 0 and at most 60")
        if any(
            isinstance(size, bool) or not isinstance(size, int) or size < 2
            for size in (max_width, max_height)
        ):
            raise ValueError("Video size limits must be integers of at least 2 pixels")
        self.path = Path(path).expanduser().resolve()
        self.max_fps = max_fps
        self.max_width = max_width
        self.max_height = max_height
        self._container = None
        self._stream = None
        self._frames = None
        self._io_lock = threading.Lock()
        self._recv_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._stopped = False
        self._close_task = None
        self._source_period = Fraction(1, 30)
        self._period = (
            Fraction(1, max_fps) if isinstance(max_fps, int) else 1 / Fraction(str(max_fps))
        )
        self._loop_origin = Fraction(0)
        self._first_source_time = None
        self._last_source_time = None
        self._decoded_in_loop = 0
        self._output_index = -1
        self._started = None
        self.frames_received = 0
        self.loop_count = 0

    @classmethod
    async def create(cls, path: Path, **kwargs):
        """Open a regular local file off the event loop; no URLs are accepted."""
        track = cls(path, **kwargs)
        opening = asyncio.create_task(asyncio.to_thread(track._open))
        opening.add_done_callback(cls._observe_worker)
        try:
            await asyncio.shield(opening)
            return track
        except BaseException:
            await track.aclose()
            raise

    @staticmethod
    def _observe_worker(task):
        if not task.cancelled():
            task.exception()

    def _open(self):
        with self._io_lock:
            if self._stopped:
                return
            if not self.path.is_file():
                raise MediaStreamError("Demo video must be an existing regular local file")
            container = None
            try:
                container = av.open(str(self.path), mode="r")
                if not container.streams.video:
                    raise MediaStreamError("Demo file does not contain a video stream")
                stream = container.streams.video[0]
                stream.codec_context.thread_count = 1
                rate = stream.average_rate or stream.base_rate
                if rate is not None and math.isfinite(float(rate)) and rate > 0:
                    self._source_period = 1 / Fraction(rate)
                self._period = max(self._period, self._source_period)
                if self._stopped:
                    container.close()
                    return
                self._container, self._stream = container, stream
                self._frames = iter(container.decode(stream))
            except Exception as exc:
                if container is not None:
                    container.close()
                if isinstance(exc, MediaStreamError):
                    raise
                raise MediaStreamError("Demo video could not be opened") from exc

    def _read_frame(self):
        with self._io_lock:
            if self._stopped or self._frames is None:
                raise MediaStreamError
            while not self._stopped:
                frame = next(self._frames, None)
                if frame is None:
                    if not self._decoded_in_loop:
                        raise MediaStreamError("Demo video is empty or cannot be decoded")
                    self._loop_origin += self._last_source_time + self._source_period
                    self._container.seek(0, stream=self._stream, backward=True)
                    self._frames = iter(self._container.decode(self._stream))
                    self._first_source_time = None
                    self._last_source_time = None
                    self._decoded_in_loop = 0
                    self.loop_count += 1
                    continue

                if frame.pts is not None and frame.time_base is not None:
                    source_time = frame.pts * Fraction(frame.time_base)
                    if self._first_source_time is None:
                        self._first_source_time = source_time
                    source_time -= self._first_source_time
                else:
                    source_time = self._decoded_in_loop * self._source_period
                if self._last_source_time is not None and source_time <= self._last_source_time:
                    source_time = self._last_source_time + self._source_period
                self._last_source_time = source_time
                self._decoded_in_loop += 1
                absolute_time = self._loop_origin + source_time
                if absolute_time < (self._output_index + 1) * self._period:
                    continue
                # Quantize onto the output cadence. Sparse/VFR timestamps can
                # advance by several slots, preserving real gaps in the footage.
                self._output_index = max(self._output_index + 1, absolute_time // self._period)
                scale = min(1.0, self.max_width / frame.width, self.max_height / frame.height)
                if scale < 1:
                    width = max(2, int(frame.width * scale) // 2 * 2)
                    height = max(2, int(frame.height * scale) // 2 * 2)
                    frame = frame.reformat(width=width, height=height, format="yuv420p")
                frame.pts = round(self._output_index * self._period / VIDEO_TIME_BASE)
                frame.time_base = VIDEO_TIME_BASE
                return frame
            raise MediaStreamError

    async def recv(self):
        """Return the next paced frame; timestamps never rewind at clip boundaries."""
        async with self._recv_lock:
            if self._stopped:
                raise MediaStreamError
            reading = asyncio.create_task(asyncio.to_thread(self._read_frame))
            reading.add_done_callback(self._observe_worker)
            try:
                frame = await asyncio.shield(reading)
                if self._started is None:
                    self._started = time.monotonic()
                delay = self._started + float(frame.pts * frame.time_base) - time.monotonic()
                if delay > 0:
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    except asyncio.TimeoutError:
                        pass
                if self._stopped:
                    raise MediaStreamError
                self.frames_received += 1
                return frame
            except BaseException as exc:
                await self.aclose()
                if isinstance(exc, (asyncio.CancelledError, MediaStreamError)):
                    raise
                raise MediaStreamError("Demo video could not be decoded") from exc

    def _close_container(self):
        with self._io_lock:
            if self._container is not None:
                self._container.close()
            self._container = None
            self._stream = None
            self._frames = None

    def stop(self):
        """Wake playback pacing immediately and schedule safe decoder cleanup."""
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
        """Wait for any decoder operation before closing its container."""
        self.stop()
        await asyncio.shield(self._close_task)
