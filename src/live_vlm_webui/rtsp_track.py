"""
RTSP Video Track for IP Camera Support

This module provides VideoStreamTrack implementation for RTSP streams,
allowing live-vlm-webui to process IP camera feeds instead of just webcams.

SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
"""

import av
import asyncio
import fractions
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from aiortc import VideoStreamTrack
from av import VideoFrame

# Wall-clock timestamp base used when the source does not provide a usable
# presentation clock (see RTSPVideoTrack._restamp).
VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)

# Suppress verbose ffmpeg/libav logging (HEVC decoder errors are normal for IP cameras)
# These POC/slice errors happen due to network packet loss but stream recovers automatically
av.logging.set_level(av.logging.FATAL)  # Only show fatal errors that stop the stream

logger = logging.getLogger(__name__)


class RTSPVideoTrack(VideoStreamTrack):
    """
    Video track that reads from RTSP stream and converts to aiortc VideoFrame.

    This enables processing of IP camera feeds through the same pipeline as webcam input.
    Supports automatic reconnection on stream failure.

    Example:
        track = RTSPVideoTrack("rtsp://192.168.1.100:554/stream")
        frame = await track.recv()
    """

    def __init__(
        self,
        rtsp_url: str,
        reconnect_attempts: int = 5,
        reconnect_delay: float = 2.0,
        options: Optional[dict] = None,
        io_timeout: float = 10.0,
    ):
        """
        Initialize RTSP video track.

        Args:
            rtsp_url: Full RTSP URL (e.g., rtsp://user:pass@192.168.1.100:554/stream)
            reconnect_attempts: Number of reconnection attempts on failure (default: 5)
            reconnect_delay: Base delay between reconnection attempts in seconds (default: 2.0)
            options: Additional PyAV container options (default: TCP transport)
            io_timeout: Bound on blocking libav I/O in seconds (default: 10.0). Keeps a
                stalled source from wedging the decode thread and, through it, stop().
        """
        super().__init__()
        self.rtsp_url = rtsp_url
        self.reconnect_attempts = reconnect_attempts
        self.reconnect_delay = reconnect_delay
        self.io_timeout = io_timeout
        self.container: Optional[av.container.InputContainer] = None
        self.stream: Optional[av.video.VideoStream] = None
        self._stopped = False
        self._frame_count = 0

        # Guards every access to self.container. Demuxing runs in a worker thread
        # while stop()/_reconnect() run on the event loop, and closing a container
        # that a thread is still demuxing frees libav memory out from under it
        # (SIGSEGV/SIGBUS). Holding this lock makes close() wait for the worker.
        self._container_lock = threading.Lock()

        # Dedicated worker so a blocking demux cannot starve the shared default
        # executor, and so shutdown is scoped to this track.
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rtsp-track")

        # Wall-clock re-stamping state (see _restamp)
        self._restamp_pts = False
        self._start_time: Optional[float] = None

        # Default options for RTSP
        self.options = options or {
            "rtsp_transport": "tcp",  # TCP is more reliable than UDP for most networks
            "max_delay": "500000",  # 500ms max delay for low latency
            "rtsp_flags": "prefer_tcp",
        }

        # Connect to stream
        self._connect()

    def _sanitize_url(self, url: str) -> str:
        """
        Remove password from URL for safe logging.

        Args:
            url: RTSP URL potentially containing credentials

        Returns:
            URL with password replaced by ****
        """
        return re.sub(r"://([^:]+):([^@]+)@", r"://\1:****@", url)

    def _connect(self):
        """
        Connect to RTSP stream.

        Raises:
            Exception: If connection fails after all attempts
        """
        safe_url = self._sanitize_url(self.rtsp_url)

        try:
            logger.info(f"Connecting to RTSP stream: {safe_url}")

            # Open RTSP stream. The timeout bounds blocking I/O inside libav so a
            # dead source cannot wedge the worker thread (and with it, stop()).
            container = av.open(self.rtsp_url, options=self.options, timeout=self.io_timeout)

            # Get video stream
            if not container.streams.video:
                container.close()
                raise ValueError("No video stream found in RTSP source")

            stream = container.streams.video[0]

            # Log stream information
            codec = stream.codec_context.name
            width = stream.width or "unknown"
            height = stream.height or "unknown"
            fps = stream.average_rate or "unknown"

            # Containers that cannot report a frame rate (notably MJPEG over HTTP,
            # which carries no timestamps on the wire) get a synthesized PTS clock
            # from the demuxer - libav defaults to 25fps regardless of how fast
            # frames actually arrive. Trusting that clock makes downstream latency
            # math drift without bound, so re-stamp those sources instead.
            restamp = stream.average_rate is None

            # Publish to the decode thread only once fully initialized
            with self._container_lock:
                self.container = container
                self.stream = stream
                self._restamp_pts = restamp
                self._start_time = None

            if restamp:
                logger.info(
                    "Source reports no frame rate; re-stamping frames with wall-clock PTS "
                    "(demuxer timestamps would be synthesized and drift)"
                )

            logger.info(f"RTSP connected successfully: {codec} {width}x{height} @{fps}fps")

        except Exception as e:
            logger.error(f"Failed to connect to RTSP stream {safe_url}: {e}")
            raise

    async def recv(self) -> VideoFrame:
        """
        Receive next frame from RTSP stream.

        This is called by aiortc framework to get video frames.
        Runs demuxing/decoding in executor to avoid blocking event loop.

        Returns:
            VideoFrame: Next decoded video frame

        Raises:
            StopAsyncIteration: When stream ends or is stopped
        """
        if self._stopped:
            raise StopAsyncIteration

        try:
            # Read frame from container (blocking operation, run in executor)
            loop = asyncio.get_event_loop()
            frame = await loop.run_in_executor(self._executor, self._read_frame)

            if frame is None:
                if not self._stopped:
                    logger.warning("RTSP stream ended unexpectedly, attempting reconnection")
                    await self._reconnect()
                    # Try again after reconnection
                    frame = await loop.run_in_executor(self._executor, self._read_frame)
                    if frame is None:
                        raise StopAsyncIteration
                else:
                    raise StopAsyncIteration

            self._frame_count += 1

            # Log progress periodically
            if self._frame_count % 300 == 0:  # Every ~10 seconds at 30fps
                logger.debug(f"RTSP: Received {self._frame_count} frames")

            return self._restamp(frame)

        except StopAsyncIteration:
            raise
        except Exception as e:
            logger.error(f"Error receiving RTSP frame: {e}", exc_info=True)
            # Try to reconnect on error
            if not self._stopped:
                await self._reconnect()
            raise

    def _restamp(self, frame: VideoFrame) -> VideoFrame:
        """
        Give the frame a wall-clock presentation timestamp when the source has none.

        MJPEG-over-HTTP (and any other container without timestamps on the wire)
        gets a demuxer-synthesized 25fps PTS clock that is unrelated to how fast
        frames really arrive. Downstream latency tracking treats PTS as a
        wall-clock-anchored timeline, so a source running at any other rate drifts
        further behind on every frame. Re-stamping from a monotonic clock keeps
        that math honest; sources with a real clock are left untouched.
        """
        if not self._restamp_pts:
            return frame

        now = time.monotonic()
        if self._start_time is None:
            self._start_time = now

        frame.pts = int((now - self._start_time) * VIDEO_CLOCK_RATE)
        frame.time_base = VIDEO_TIME_BASE
        return frame

    def _read_frame(self) -> Optional[VideoFrame]:
        """
        Read and decode next frame from RTSP stream (blocking).

        This is a blocking operation and should be run in an executor. The
        container lock is held for the whole demux/decode so that stop() and
        _reconnect() cannot close the container underneath us.

        Returns:
            VideoFrame or None if stream ended or error occurred
        """
        with self._container_lock:
            if self._stopped:
                return None

            if not self.container or not self.stream:
                logger.error("Cannot read frame: container or stream not initialized")
                return None

            try:
                # Demux and decode packets until we get a video frame
                for packet in self.container.demux(self.stream):
                    # Bail out promptly when stop() is waiting on the lock
                    if self._stopped:
                        return None
                    for frame in packet.decode():
                        if isinstance(frame, VideoFrame):
                            return frame

                # No more frames available (stream ended)
                logger.info("RTSP stream reached end of file")
                return None

            except av.error.EOFError:
                logger.warning("RTSP stream EOF")
                return None
            except Exception as e:
                logger.error(f"Error decoding RTSP frame: {e}")
                return None

    async def _reconnect(self):
        """
        Attempt to reconnect to RTSP stream with exponential backoff.

        Tries multiple times with increasing delay between attempts.
        """
        safe_url = self._sanitize_url(self.rtsp_url)
        logger.info(f"Attempting RTSP reconnection to {safe_url}...")

        # Clean up existing connection. Take the container lock so we never close
        # while the decode thread is inside demux().
        with self._container_lock:
            if self.container:
                try:
                    self.container.close()
                except Exception as e:
                    logger.debug(f"Error closing container during reconnect: {e}")
                self.container = None
                self.stream = None
            # A fresh connection restarts the wall-clock timeline
            self._start_time = None

        # Try to reconnect with exponential backoff
        for attempt in range(self.reconnect_attempts):
            try:
                logger.info(f"Reconnection attempt {attempt + 1}/{self.reconnect_attempts}")

                # Wait with exponential backoff (2, 4, 8, 16, 32 seconds)
                if attempt > 0:
                    delay = self.reconnect_delay * (2 ** (attempt - 1))
                    logger.info(f"Waiting {delay}s before reconnection attempt...")
                    await asyncio.sleep(delay)

                # Attempt connection
                self._connect()
                logger.info(f"RTSP reconnected successfully on attempt {attempt + 1}")
                return

            except Exception as e:
                logger.warning(f"Reconnection attempt {attempt + 1} failed: {e}")
                if attempt == self.reconnect_attempts - 1:
                    logger.error(
                        f"RTSP reconnection failed after {self.reconnect_attempts} attempts"
                    )
                    raise

    def stop(self):
        """
        Stop the RTSP stream and clean up resources.

        Should be called when stream is no longer needed.

        Cancelling the asyncio task that awaits recv() does NOT stop the executor
        thread running _read_frame; it stays blocked inside libav's demux(). Closing
        the container from here while that is happening frees memory the thread is
        still reading, which crashes the whole process (SIGSEGV/SIGBUS). Setting
        _stopped first and then taking the container lock makes this wait for the
        decode thread to leave demux() before anything is freed.
        """
        self._stopped = True

        with self._container_lock:
            if self.container:
                try:
                    self.container.close()
                    logger.info(f"RTSP stream closed: {self._frame_count} frames received")
                except Exception as e:
                    logger.warning(f"Error closing RTSP container: {e}")
                finally:
                    self.container = None
                    self.stream = None

        # Release the worker thread. Not waited on: the lock above already
        # guarantees no thread is touching the container anymore.
        self._executor.shutdown(wait=False)

        super().stop()

    @property
    def is_connected(self) -> bool:
        """Check if RTSP stream is currently connected."""
        return self.container is not None and not self._stopped

    def get_stats(self) -> dict:
        """
        Get statistics about the RTSP stream.

        Returns:
            Dictionary with stream statistics
        """
        stats = {
            "url": self._sanitize_url(self.rtsp_url),
            "connected": self.is_connected,
            "frames_received": self._frame_count,
            "stopped": self._stopped,
        }

        if self.stream:
            stats.update(
                {
                    "codec": self.stream.codec_context.name,
                    "width": self.stream.width,
                    "height": self.stream.height,
                    "fps": float(self.stream.average_rate) if self.stream.average_rate else None,
                }
            )

        return stats
