# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Video Track Processor
Handles video frames, adds text overlays, and manages VLM processing
"""

import asyncio
import logging
import math
import time

import av
import cv2
import numpy as np
from aiortc import VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from PIL import Image

from .temporal_buffer import TemporalBuffer, TemporalConfig
from .vlm_service import VLMService

# Enable swscaler warnings to track hardware acceleration status
# TODO: Implement hardware-accelerated color space conversion on Jetson using NVMM/VPI
av.logging.set_level(av.logging.WARNING)

logger = logging.getLogger(__name__)


class VideoProcessorTrack(VideoStreamTrack):
    """
    Receive frames with bounded source latency and optional VLM analysis.

    A None VLM service retains source timing and passthrough without conversion
    or inference, so segmentation-only cameras use the same latency policy.
    """

    # Class variable for frame processing interval (can be updated dynamically)
    process_every_n_frames = 30
    # Max allowed latency before dropping frames (in seconds, 0 = disabled)
    max_frame_latency = 0.0

    def __init__(
        self,
        track: VideoStreamTrack,
        vlm_service: VLMService | None,
        text_callback=None,
        *,
        process_every_n_frames: int | None = None,
        max_frame_latency: float | None = None,
        process_on_recv: bool = True,
        temporal=None,
        temporal_callback=None,
        source_generation_provider=None,
    ):
        super().__init__()
        # Only override explicit settings. Legacy tracks continue to observe updates
        # to the class defaults, while each camera can use independent settings.
        if process_every_n_frames is not None:
            if (
                isinstance(process_every_n_frames, bool)
                or not isinstance(process_every_n_frames, int)
                or process_every_n_frames < 1
            ):
                raise ValueError("process_every_n_frames must be a positive integer")
            self.process_every_n_frames = process_every_n_frames
        if max_frame_latency is not None:
            if (
                isinstance(max_frame_latency, bool)
                or not isinstance(max_frame_latency, (int, float))
                or not math.isfinite(max_frame_latency)
                or max_frame_latency < 0
            ):
                raise ValueError("max_frame_latency must be a finite non-negative number")
            self.max_frame_latency = max_frame_latency
        if not isinstance(process_on_recv, bool):
            raise TypeError("process_on_recv must be a boolean")
        if source_generation_provider is not None and not callable(source_generation_provider):
            raise TypeError("source_generation_provider must be callable")
        self._source_generation_provider = source_generation_provider or (
            lambda: getattr(track, "source_generation", getattr(track, "loop_count", None))
        )
        self._source_generation_seen = False
        self._source_generation = None
        self._minimum_temporal_frame_id = 0
        self.process_on_recv = process_on_recv
        self.temporal = TemporalConfig.from_dict(temporal)
        self.temporal_buffer = TemporalBuffer(self.temporal)
        self.temporal_callback = temporal_callback
        self._temporal_settings = None
        self._temporal_status = None
        self._last_temporal_sample_id = None
        self.track = track
        self.vlm_service = vlm_service
        self.text_callback = text_callback  # Callback to send text updates
        self.last_frame: np.ndarray | None = None
        self.frame_count = 0
        self._last_inference_frame = 0
        self.last_frame_received_at = None
        self.last_frame_received_unix_ms = None
        self.dropped_frames = 0
        self.first_frame_pts = None  # Track first frame PTS to calculate relative time
        self.first_frame_time = None  # Wall clock time of first frame
        self.frame_time_base = None  # Time base for PTS conversion (e.g., 1/90000)
        self._inference_task: asyncio.Task | None = None

    def stop(self):
        """Stop inference without stopping the borrowed input track."""
        if self.readyState == "ended":
            return
        super().stop()
        self.temporal_buffer.clear()
        self.last_frame = None
        if self._inference_task is not None and not self._inference_task.done():
            self._inference_task.cancel()

    async def aclose(self):
        """Stop the processor and wait for its in-flight inference to finish cleanup."""
        inference_task = self._inference_task
        self.stop()
        if inference_task is not None:
            await asyncio.gather(inference_task, return_exceptions=True)

    def _inference_done(self, task: asyncio.Task):
        # Always retrieve failures, including when the processor has already stopped.
        if self._inference_task is task:
            self._inference_task = None
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error(
                    "VLM frame processing failed",
                    exc_info=(type(error), error, error.__traceback__),
                )

    def reset_temporal(self):
        """Immediately invalidate pending context when its meaning changes."""
        if not self.temporal.enabled:
            return
        self.temporal_buffer.clear()
        self._temporal_settings = None
        self._last_temporal_sample_id = None
        if self._inference_task is not None and not self._inference_task.done():
            self._inference_task.cancel()
        self._temporal_status = self.temporal_status()
        if self.temporal_callback and self.readyState == "live":
            self.temporal_callback(self._temporal_status)

    def temporal_status(self):
        return {
            "temporal": self.temporal.as_dict(),
            "temporal_enabled": self.temporal.enabled,
            "temporal_buffer_frames": len(self.temporal_buffer.samples),
            "temporal_buffer_ready": self.temporal_buffer.ready,
        }

    def _submit_temporal(self, frame, received_at, received_unix_ms, frame_id, preview_mode):
        # Prompt/model changes start a fresh visual window. No generated answers
        # are fed back as observations, and no buffer is shared between cameras.
        settings = (self.temporal,) + tuple(
            getattr(self.vlm_service, name, None)
            for name in (
                "prompt",
                "model",
                "api_base",
                "backend",
                "max_tokens",
                "temperature",
                "image_max_edge",
            )
        )
        generation = self.temporal_buffer.generation
        if settings != self._temporal_settings:
            self.temporal_buffer.clear()
            self._temporal_settings = settings
        source_time = (
            float(frame.pts * frame.time_base)
            if frame.pts is not None and frame.time_base is not None
            else None
        )
        due = self.temporal_buffer.due(received_at, source_time)
        if self.temporal_buffer.generation != generation:
            # Invalidated windows must never publish after a source/settings reset.
            # Keep the task owned until its cancellation cleanup finishes.
            if self._inference_task is not None and not self._inference_task.done():
                self._inference_task.cancel()
            self._last_temporal_sample_id = None
        if due:
            image = frame.to_image()
            self.temporal_buffer.offer(
                image,
                received_at,
                frame_id,
                received_unix_ms,
                frame.pts,
                str(frame.time_base) if frame.time_base is not None else None,
            )
            self.last_frame = frame.to_ndarray(format="bgr24")
        status = self.temporal_status()
        if status != self._temporal_status:
            self._temporal_status = status
            if self.temporal_callback:
                self.temporal_callback(status)
        if not self.temporal_buffer.ready:
            return False
        if self._inference_task is not None and not self._inference_task.done():
            return False
        if frame_id - self._last_inference_frame < self.process_every_n_frames:
            return False
        samples = self.temporal_buffer.snapshot()
        latest = samples[-1]
        if latest.frame_id == self._last_temporal_sample_id:
            return False
        images = [sample.image for sample in samples]
        timestamps = [sample.timestamp - samples[0].timestamp for sample in samples]
        identified_process = getattr(self.vlm_service, "process_identified_video", None)
        if identified_process is not None:
            processing = identified_process(
                images,
                timestamps,
                latest.timestamp,
                latest.received_unix_ms,
                frame_id=latest.frame_id,
                frame_pts=latest.frame_pts,
                frame_time_base=latest.frame_time_base,
                preview_mode=preview_mode,
            )
        else:
            processing = self.vlm_service.process_video(images, timestamps)
        self._last_inference_frame = frame_id
        self._last_temporal_sample_id = latest.frame_id
        self._inference_task = asyncio.create_task(processing)
        self._inference_task.add_done_callback(self._inference_done)
        return True

    def submit_frame(
        self,
        frame,
        *,
        received_at,
        received_unix_ms,
        frame_id,
        preview_mode="raw",
    ):
        """Offer an eligible original frame without waiting for its VLM response.

        frame_id counts source frames, including frames skipped by segmentation.
        External callers can therefore keep source-based cadence while admitting
        only originals whose annotated counterparts were published to the viewer.
        """
        if self.readyState != "live" or self.vlm_service is None:
            return False
        if self.temporal.enabled:
            # A mask may finish after recv has crossed a source reconnect/loop.
            # Its original frame ID must not repopulate the new visual window.
            if frame_id < self._minimum_temporal_frame_id:
                return False
            return self._submit_temporal(
                frame, received_at, received_unix_ms, frame_id, preview_mode
            )
        interval = self.process_every_n_frames
        inference_available = self._inference_task is None or self._inference_task.done()
        should_process = frame_id - self._last_inference_frame >= interval and inference_available
        if not should_process and not (frame_id == 1 and self.last_frame is None):
            return False
        img = frame.to_ndarray(format="bgr24")
        self.last_frame = img.copy()
        if not should_process:
            return False
        pil_img = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        identified_process = getattr(self.vlm_service, "process_identified_frame", None)
        timed_process = getattr(self.vlm_service, "process_timed_frame", None)
        if identified_process is not None:
            processing = identified_process(
                pil_img,
                received_at,
                received_unix_ms,
                frame_id=frame_id,
                frame_pts=frame.pts,
                frame_time_base=str(frame.time_base) if frame.time_base is not None else None,
                preview_mode=preview_mode,
            )
        elif timed_process is not None:
            processing = timed_process(pil_img, received_at, received_unix_ms)
        else:
            processing = self.vlm_service.process_frame(pil_img)
        self._last_inference_frame = frame_id
        self._inference_task = asyncio.create_task(processing)
        self._inference_task.add_done_callback(self._inference_done)
        logger.info("Frame %s: Sending to VLM (interval=%s)", frame_id, interval)
        return True

    async def recv(self):
        """
        Receive frame from input track, process it, and return with text overlay
        """
        if self.readyState != "live":
            raise MediaStreamError
        try:
            # Get frame from incoming track
            frame = await self.track.recv()
            received_at = time.perf_counter()
            received_unix_ms = time.time() * 1000
            if self.readyState != "live":
                raise MediaStreamError

            # Initialize timing on first frame
            if self.first_frame_pts is None and frame.pts is not None:
                self.first_frame_pts = frame.pts
                self.first_frame_time = time.time()
                # Store time_base for PTS conversion (e.g., 1/90000 for 90kHz clock)
                self.frame_time_base = float(frame.time_base)
                logger.info(
                    f"Latency tracking initialized: PTS={frame.pts}, time_base={frame.time_base} ({self.frame_time_base}s per tick)"
                )

            # Calculate actual frame age (latency) using PTS and time_base
            # Note: Some streams (like RTSP) may not have PTS set, so skip latency checks
            frame_latency = 0.0
            if frame.pts is not None and self.first_frame_pts is not None:
                # PTS is in time_base units, convert to seconds: pts * time_base
                frame_time_offset = (frame.pts - self.first_frame_pts) * self.frame_time_base
                expected_wall_time = self.first_frame_time + frame_time_offset
                current_time = time.time()
                frame_latency = current_time - expected_wall_time

            # Check for accumulated latency and drop old frames if needed (only if max_latency > 0)
            max_latency = self.max_frame_latency
            if max_latency > 0 and frame_latency > max_latency and frame.pts is not None:
                logger.warning(
                    f"Frame is {frame_latency:.2f}s behind, dropping frames (threshold: {max_latency}s)"
                )

                # Drop frames until we get a fresh one
                dropped_count = 0
                while frame_latency > max_latency:
                    self.dropped_frames += 1
                    dropped_count += 1

                    # Get next frame
                    frame = await self.track.recv()
                    received_at = time.perf_counter()
                    received_unix_ms = time.time() * 1000
                    if self.readyState != "live":
                        raise MediaStreamError

                    # Recalculate latency for new frame (using time_base for correct conversion)
                    if frame.pts is not None and self.first_frame_pts is not None:
                        frame_time_offset = (
                            frame.pts - self.first_frame_pts
                        ) * self.frame_time_base
                        expected_wall_time = self.first_frame_time + frame_time_offset
                        frame_latency = time.time() - expected_wall_time
                    else:
                        # If PTS becomes unavailable, stop dropping frames
                        break

                    # Prevent infinite loop
                    if dropped_count > 100:
                        logger.error(
                            f"Dropped {dropped_count} frames, but still behind. Resetting timing."
                        )
                        if frame.pts is not None:
                            self.first_frame_pts = frame.pts
                            self.first_frame_time = time.time()
                            self.frame_time_base = float(frame.time_base)
                        break

                if dropped_count > 0:
                    logger.info(
                        f"Dropped {dropped_count} frames, now at {frame_latency:.2f}s latency"
                    )

            if self.temporal.enabled:
                generation = self._source_generation_provider()
                if self._source_generation_seen and generation != self._source_generation:
                    self._minimum_temporal_frame_id = self.frame_count + 1
                    self.reset_temporal()
                self._source_generation = generation
                self._source_generation_seen = True

            # Retain the receipt time of the selected source frame for analysis
            # snapshots. This excludes camera/network time before server receipt.
            self.last_frame_received_at = received_at
            self.last_frame_received_unix_ms = received_unix_ms

            # Increment frame counter
            self.frame_count += 1

            if self.vlm_service is None:
                return frame

            if self.process_on_recv:
                self.submit_frame(
                    frame,
                    received_at=received_at,
                    received_unix_ms=received_unix_ms,
                    frame_id=self.frame_count,
                )

            # Get current response (may be old if VLM is still processing)
            response, _is_processing = self.vlm_service.get_current_response()

            # Get metrics
            metrics = {**self.vlm_service.get_metrics(), **self.temporal_status()}

            # Send text update via callback (for WebSocket)
            if self.text_callback and self.readyState == "live":
                self.text_callback(response, metrics)

            # Return original frame directly - zero-copy passthrough!
            # This avoids expensive BGR→YUV conversion
            return frame

        except MediaStreamError:
            raise
        except Exception:
            logger.exception("Error processing frame")
            raise

    def _add_text_overlay(self, img: np.ndarray, text: str, status: str = "") -> np.ndarray:
        """
        Add text overlay to image

        Args:
            img: Input image (BGR format)
            text: Text to overlay (VLM response)
            status: Optional status text

        Returns:
            Image with text overlay
        """
        img_copy = img.copy()
        height, width = img_copy.shape[:2]

        # Prepare text
        full_text = f"{text} {status}" if status else text

        # Text wrapping - split long captions
        max_chars_per_line = 60
        words = full_text.split()
        lines = []
        current_line = []
        current_length = 0

        for word in words:
            if current_length + len(word) + 1 <= max_chars_per_line:
                current_line.append(word)
                current_length += len(word) + 1
            else:
                if current_line:
                    lines.append(" ".join(current_line))
                current_line = [word]
                current_length = len(word)

        if current_line:
            lines.append(" ".join(current_line))

        # Text properties
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = 0.7
        font_thickness = 2
        text_color = (255, 255, 255)  # White
        bg_color = (0, 0, 0)  # Black background
        padding = 10
        line_height = 30

        # Calculate total height needed
        total_text_height = len(lines) * line_height + 2 * padding

        # Create semi-transparent overlay at bottom
        overlay = img_copy.copy()
        cv2.rectangle(overlay, (0, height - total_text_height), (width, height), bg_color, -1)

        # Blend overlay with original image
        alpha = 0.7
        cv2.addWeighted(overlay, alpha, img_copy, 1 - alpha, 0, img_copy)

        # Add text lines
        y_position = height - total_text_height + padding + line_height
        for line in lines:
            cv2.putText(
                img_copy,
                line,
                (padding, y_position),
                font,
                font_scale,
                text_color,
                font_thickness,
                cv2.LINE_AA,
            )
            y_position += line_height

        return img_copy
