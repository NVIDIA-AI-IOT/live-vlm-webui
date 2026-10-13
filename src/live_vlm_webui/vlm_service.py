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
VLM Service
Handles async image analysis using any OpenAI-compatible VLM API
(Works with vLLM, SGLang, Ollama, OpenAI, etc.)
"""

import asyncio
import base64
import io
import math
import time
from openai import AsyncOpenAI
from PIL import Image, ImageOps
from fractions import Fraction
from typing import Optional
import logging

logger = logging.getLogger(__name__)

TEMPORAL_MAX_FRAMES = 32
TEMPORAL_MAX_SECONDS = 30.0
TEMPORAL_VIDEO_TIME_BASE = Fraction(1, 90000)


def _validate_temporal_frames(images, timestamps, backend):
    """Validate distinct observation times, including visually unchanged scenes."""
    if not isinstance(images, (list, tuple)) or not 2 <= len(images) <= TEMPORAL_MAX_FRAMES:
        raise ValueError(f"Temporal analysis requires 2-{TEMPORAL_MAX_FRAMES} frames")
    if not isinstance(timestamps, (list, tuple)) or len(timestamps) != len(images):
        raise ValueError("Each temporal frame requires a timestamp")
    if any(not isinstance(image, Image.Image) for image in images):
        raise ValueError("Temporal frames must be PIL images")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < 0
        for value in timestamps
    ):
        raise ValueError("Temporal timestamps must be finite non-negative seconds")
    times = tuple(float(value) for value in timestamps)
    if any(second <= first for first, second in zip(times, times[1:])):
        raise ValueError("Temporal timestamps must be strictly increasing")
    if times[-1] - times[0] > TEMPORAL_MAX_SECONDS:
        raise ValueError(f"Temporal windows must not exceed {TEMPORAL_MAX_SECONDS:g} seconds")
    if backend == "edgellm" and len(images) % 2:
        raise ValueError("Edge-LLM video requires an even number of frames; none were discarded")
    return tuple(images), times


def _temporal_content(images, timestamps, prompt, backend, image_max_edge):
    """Encode a bounded request on a worker without changing caller-owned images."""
    edge = image_max_edge or 640
    relative_times = [value - timestamps[0] for value in timestamps]
    instruction = (
        "Analyze these chronological observations as one video. Describe visible actions "
        "and changes over time, using only evidence in the sequence. "
        "Do not infer motion from a single pose. "
        "Observation times in seconds from the first frame: "
        + ", ".join(f"{value:.3f}" for value in relative_times)
        + ".\n"
        + prompt
    )
    content = [{"type": "text", "text": instruction}]
    if backend != "edgellm":
        for image, timestamp in zip(images, relative_times):
            resized = image.convert("RGB")
            resized.thumbnail((edge, edge), Image.Resampling.LANCZOS)
            encoded = io.BytesIO()
            resized.save(encoded, format="JPEG", quality=85)
            content.extend(
                [
                    {"type": "text", "text": f"Frame at {timestamp:.3f} seconds:"},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/jpeg;base64,"
                            + base64.b64encode(encoded.getvalue()).decode("ascii")
                        },
                    },
                ]
            )
        return content

    import av

    first = images[0].copy()
    first.thumbnail((edge, edge), Image.Resampling.LANCZOS)
    # YUV420 video needs even geometry. Padding keeps the complete image visible.
    size = (max(2, first.width - first.width % 2), max(2, first.height - first.height % 2))
    rate = Fraction(len(images) - 1) / Fraction(str(relative_times[-1]))
    rate = rate.limit_denominator(90000)
    pts = [round(value / TEMPORAL_VIDEO_TIME_BASE) for value in relative_times]
    if any(second <= first for first, second in zip(pts, pts[1:])):
        raise ValueError("Temporal timestamps are too close for the video time base")
    encoded = io.BytesIO()
    with av.open(encoded, mode="w", format="mp4") as container:
        stream = container.add_stream("libx264", rate=rate)
        stream.width, stream.height = size
        stream.pix_fmt = "yuv420p"
        stream.time_base = TEMPORAL_VIDEO_TIME_BASE
        stream.codec_context.time_base = TEMPORAL_VIDEO_TIME_BASE
        stream.codec_context.thread_count = 1
        stream.options = {"preset": "ultrafast", "tune": "zerolatency", "crf": "23"}
        for image, frame_pts in zip(images, pts):
            resized = image.convert("RGB")
            resized.thumbnail(size, Image.Resampling.LANCZOS)
            canvas = ImageOps.pad(resized, size, method=Image.Resampling.LANCZOS, color="black")
            frame = av.VideoFrame.from_image(canvas)
            frame.pts = frame_pts
            frame.time_base = TEMPORAL_VIDEO_TIME_BASE
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode():
            container.mux(packet)
    video = encoded.getvalue()
    if len(video) > 32 * 1024 * 1024:
        raise ValueError("Encoded temporal video exceeds the 32 MiB request limit")
    content.append(
        {
            "type": "video_url",
            "video_url": {
                "url": "data:video/mp4;base64," + base64.b64encode(video).decode("ascii")
            },
            # Explicit frame count is exact-or-reject in the native sampler.
            # Setting fps as well would be rejected by Edge-LLM.
            "nframes": len(images),
        }
    )
    return content


async def _prepare_temporal_content(*args):
    """Own encoding until the worker finishes, including cancellation cleanup."""
    worker = asyncio.create_task(asyncio.to_thread(_temporal_content, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not worker.cancelled():
            worker.exception()
        raise


class VLMService:
    """Service for analyzing images using VLM via OpenAI-compatible API"""

    def __init__(
        self,
        model: str,
        api_base: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        prompt: str = "Describe what you see in this image in one sentence.",
        max_tokens: int = 512,
        backend: str = "openai",
        image_max_edge: int = 0,
        temperature: float = 0.7,
    ):
        """
        Initialize VLM service

        Args:
            model: Model name (e.g., "llama-3.2-11b-vision-instruct" for vLLM)
            api_base: Base URL for the API (e.g., "http://localhost:8000/v1" for vLLM)
            api_key: API key (use "EMPTY" for local servers)
            prompt: Default prompt to use for image analysis
            max_tokens: Maximum tokens to generate
            backend: Request dialect, either openai or edgellm
            image_max_edge: Resize VLM input to fit this edge; 0 retains input size
            temperature: Sampling temperature, from 0 to 2
        """
        if backend not in ("openai", "edgellm"):
            raise ValueError("backend must be openai or edgellm")
        if (
            isinstance(image_max_edge, bool)
            or not isinstance(image_max_edge, int)
            or (image_max_edge != 0 and not 128 <= image_max_edge <= 2048)
        ):
            raise ValueError("image_max_edge must be 0 or an integer from 128 to 2048")
        if (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or (not math.isfinite(temperature) or not 0 <= temperature <= 2)
        ):
            raise ValueError("temperature must be a finite number from 0 to 2")
        self.image_max_edge = image_max_edge
        self.temperature = temperature
        self.backend = backend
        self.model = model
        self.api_base = api_base
        self.api_key = api_key if api_key else "EMPTY"
        self.prompt = prompt
        self.max_tokens = max_tokens
        self.client = AsyncOpenAI(base_url=api_base, api_key=api_key)
        self.current_response = "Initializing..."
        self.is_processing = False
        self._processing_lock = asyncio.Lock()

        # Metrics tracking
        self.last_inference_time = 0.0  # seconds
        self.total_inferences = 0
        self.total_inference_time = 0.0
        self._set_temporal_metrics()

    def _set_temporal_metrics(self, timestamps=None, *, attempted_video=False):
        """Metadata belongs to the last completed request, including an error."""
        span = timestamps[-1] - timestamps[0] if timestamps else None
        self._temporal_metrics = {
            "temporal_enabled": attempted_video,
            "temporal_frame_count": len(timestamps) if timestamps else 0,
            "temporal_window_seconds": span,
            "temporal_sample_fps": (len(timestamps) - 1) / span if span else None,
            "temporal_transport": (
                ("video" if self.backend == "edgellm" else "images") if attempted_video else None
            ),
        }

    async def analyze_image(self, image: Image.Image, prompt: Optional[str] = None) -> str:
        """
        Analyze an image using the VLM model

        Args:
            image: PIL Image to analyze
            prompt: Prompt for the VLM (uses default if None)

        Returns:
            Generated response string
        """
        if prompt is None:
            prompt = self.prompt

        try:
            start_time = time.perf_counter()

            # Resize only this VLM request. Segmentation and preview retain their
            # own resolution; the caller's unmasked image is never modified.
            if self.image_max_edge and max(image.size) > self.image_max_edge:
                image = image.copy()
                image.thumbnail(
                    (self.image_max_edge, self.image_max_edge), Image.Resampling.LANCZOS
                )
            # Convert PIL Image to base64
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format="JPEG", quality=85 if self.image_max_edge else 75)
            img_byte_arr = img_byte_arr.getvalue()
            img_base64 = base64.b64encode(img_byte_arr).decode("utf-8")

            # Create message with image
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{img_base64}"},
                        },
                    ],
                }
            ]

            # Call API
            request_options = {}
            if self.backend == "edgellm":
                # Independent camera frames must not reuse conversation state.
                request_options["extra_body"] = {
                    "reuse_context": False,
                    "cache_generated_tokens": False,
                }
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                **request_options,
            )

            # Calculate latency
            end_time = time.perf_counter()
            inference_time = end_time - start_time

            # Validate the response before counting a successful inference.
            result = response.choices[0].message.content.strip()
            if not result:
                raise ValueError("VLM returned an empty description")

            # Update metrics
            self.last_inference_time = inference_time
            self.total_inferences += 1
            self.total_inference_time += inference_time

            self._set_temporal_metrics()
            logger.info(f"VLM response: {result} (latency: {inference_time*1000:.0f}ms)")
            return result

        except Exception as e:
            self._set_temporal_metrics()
            logger.error(f"Error analyzing image: {e}")
            return f"Error: {str(e)}"

    async def analyze_video(
        self,
        images: list[Image.Image],
        timestamps: list[float],
        prompt: Optional[str] = None,
    ) -> str:
        """Analyze one bounded temporal window; never fall back to a single image.

        Edge-LLM receives an MP4 with all even-count samples. Other compatible
        servers receive timestamp-labelled images in chronological order. Times
        are relative seconds; equal pixels at different observation times are valid.
        """
        try:
            start_time = time.perf_counter()
            images, timestamps = _validate_temporal_frames(images, timestamps, self.backend)
            content = await _prepare_temporal_content(
                images,
                timestamps,
                self.prompt if prompt is None else prompt,
                self.backend,
                self.image_max_edge,
            )
            request_options = {}
            if self.backend == "edgellm":
                request_options["extra_body"] = {
                    "reuse_context": False,
                    "cache_generated_tokens": False,
                }
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": content}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                **request_options,
            )
            inference_time = time.perf_counter() - start_time
            result = response.choices[0].message.content.strip()
            if not result:
                raise ValueError("VLM returned an empty description")
            self.last_inference_time = inference_time
            self.total_inferences += 1
            self.total_inference_time += inference_time
            self._set_temporal_metrics(timestamps, attempted_video=True)
            logger.info(
                "Temporal VLM response: %s (frames=%s, latency=%.0fms)",
                result,
                len(images),
                inference_time * 1000,
            )
            return result
        except Exception as exc:
            self._set_temporal_metrics(attempted_video=True)
            logger.error("Error analyzing video: %s", exc)
            return f"Error: {exc}"

    async def process_video(
        self,
        images: list[Image.Image],
        timestamps: list[float],
        prompt: Optional[str] = None,
    ) -> None:
        """Update the response under the same non-queuing lock as single frames."""
        if self._processing_lock.locked():
            logger.debug("VLM busy, skipping temporal window")
            return
        async with self._processing_lock:
            self.is_processing = True
            try:
                self.current_response = await self.analyze_video(images, timestamps, prompt)
            finally:
                self.is_processing = False

    async def process_frame(self, image: Image.Image, prompt: Optional[str] = None) -> None:
        """
        Process a frame asynchronously. Updates self.current_response when done.
        If already processing, this call is skipped.

        Args:
            image: PIL Image to process
            prompt: Optional custom prompt (uses default if None)
        """
        # Non-blocking check if we're already processing
        if self._processing_lock.locked():
            logger.debug("VLM busy, skipping frame")
            return

        async with self._processing_lock:
            self.is_processing = True
            try:
                response = await self.analyze_image(image, prompt)
                self.current_response = response
            finally:
                self.is_processing = False

    def get_current_response(self) -> tuple[str, bool]:
        """
        Get the current response and processing status

        Returns:
            Tuple of (response, is_processing)
        """
        return self.current_response, self.is_processing

    def get_metrics(self) -> dict:
        """
        Get current performance metrics

        Returns:
            Dict with latency and throughput metrics
        """
        avg_latency = (
            self.total_inference_time / self.total_inferences if self.total_inferences > 0 else 0.0
        )

        return {
            "last_latency_ms": self.last_inference_time * 1000,
            "avg_latency_ms": avg_latency * 1000,
            "total_inferences": self.total_inferences,
            "is_processing": self.is_processing,
            **self._temporal_metrics,
        }

    def update_prompt(self, new_prompt: str, max_tokens: Optional[int] = None) -> None:
        """
        Update the default prompt and optionally max_tokens

        Args:
            new_prompt: New prompt to use
            max_tokens: Maximum tokens to generate (optional)
        """
        self.prompt = new_prompt
        if max_tokens is not None:
            self.max_tokens = max_tokens
            logger.info(f"Updated prompt to: {new_prompt}, max_tokens: {max_tokens}")
        else:
            logger.info(f"Updated prompt to: {new_prompt}")

    def update_api_settings(
        self, api_base: Optional[str] = None, api_key: Optional[str] = None
    ) -> None:
        """
        Update API base URL and/or API key, recreating the client

        Args:
            api_base: New API base URL (optional)
            api_key: New API key (optional, use empty string for local services)
        """
        if api_base:
            self.api_base = api_base
        if api_key is not None:  # Allow empty string
            self.api_key = api_key if api_key else "EMPTY"

        # Recreate the client with new settings
        self.client = AsyncOpenAI(base_url=self.api_base, api_key=self.api_key)

        masked_key = (
            "***" + self.api_key[-4:]
            if self.api_key and len(self.api_key) > 4 and self.api_key != "EMPTY"
            else "EMPTY"
        )
        logger.info(f"Updated API settings - base: {self.api_base}, key: {masked_key}")
