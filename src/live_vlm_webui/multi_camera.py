# SPDX-License-Identifier: Apache-2.0
"""Independent camera pipelines using the existing VLM and video processing flow."""

import asyncio
import base64
import io
import json
import logging
import math
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import web
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import SessionDescription
from PIL import Image

from .demo_catalog import demo_catalog, demo_playback_bounds, resolve_demo
from .local_video_track import LocalVideoTrack
from .rtsp_track import RTSPVideoTrack
from .segmentation import SegmentationConfig, SegmentationService, SegmentationUnavailable
from .temporal_buffer import TemporalConfig
from .video_processor import VideoProcessorTrack
from .vlm_service import VLMService

logger = logging.getLogger(__name__)
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
VLM_FIELDS = {
    "model",
    "api_base",
    "api_key",
    "prompt",
    "max_tokens",
    "backend",
    "image_max_edge",
    "temperature",
}


def api_error(status, message):
    return web.json_response({"error": message}, status=status)


def validate_id(value, name):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError(f"{name} must contain 1-64 letters, digits, underscores or hyphens")
    return value


def validate_number(value, name, minimum, maximum, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{name} must be a number")
    if integer and not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if not minimum <= value <= maximum or not math.isfinite(value):
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def validate_vlm(value):
    if not isinstance(value, dict) or set(value) - VLM_FIELDS:
        raise ValueError("vlm contains an unknown setting")
    result = dict(value)
    for key, val in result.items():
        if key == "max_tokens":
            validate_number(val, key, 1, 32768, integer=True)
        elif key == "image_max_edge":
            validate_number(val, key, 0, 2048, integer=True)
            if val != 0 and val < 128:
                raise ValueError("vlm.image_max_edge must be 0 or from 128 to 2048")
        elif key == "temperature":
            validate_number(val, key, 0, 2)
        elif not isinstance(val, str) or (key != "api_key" and not val.strip()):
            raise ValueError(f"vlm.{key} must be a nonempty string")
    if "backend" in result and result["backend"] not in ("openai", "edgellm"):
        raise ValueError("vlm.backend must be openai or edgellm")
    if "api_base" in result:
        url = urlsplit(result["api_base"])
        if url.scheme not in ("http", "https") or not url.hostname or url.username:
            raise ValueError("vlm.api_base must be an HTTP(S) URL without credentials")
    return result


class CameraPreviewTrack(VideoStreamTrack):
    """A bounded latest-frame preview; the camera consumer owns all source reads."""

    def __init__(self):
        super().__init__()
        self._available = asyncio.Event()
        self._frame = None

    def publish(self, frame):
        if self.readyState == "live":
            self._frame = frame
            self._available.set()

    async def recv(self):
        if self.readyState != "live":
            raise MediaStreamError
        await self._available.wait()
        self._available.clear()
        if self.readyState != "live":
            raise MediaStreamError
        return self._frame

    def stop(self):
        super().stop()
        self._frame = None
        self._available.set()


class MultiCameraVLMService(VLMService):
    """One response/lock/metrics set per camera, with a shared inference budget."""

    def __init__(self, *, semaphore, on_result, on_status=None, **settings):
        super().__init__(**settings)
        # A stalled backend cannot occupy all camera inference slots indefinitely.
        self.client.timeout = 30.0
        self.client.max_retries = 0
        self._semaphore = semaphore
        self._on_result = on_result
        self._on_status = on_status or (lambda _processing: None)
        self._frame_started = None
        self._frame_received_unix_ms = None
        self._frame_identity = {}
        self._request_queue_ms = None
        self._request_latency_ms = None
        self._result_completed_at = None
        self._completed_timing = {
            "last_frame_received_unix_ms": None,
            "last_result_unix_ms": None,
            "last_e2e_latency_ms": None,
            "last_queue_ms": None,
            "last_request_succeeded": None,
            "last_frame_id": None,
            "last_frame_pts": None,
            "last_frame_time_base": None,
            "last_frame_preview_mode": None,
        }

    async def process_identified_frame(
        self,
        image,
        received_at,
        received_unix_ms,
        *,
        frame_id,
        frame_pts,
        frame_time_base,
        preview_mode,
    ):
        """Keep the source identity paired with this completed VLM result."""
        if self._processing_lock.locked():
            return
        self._frame_identity = {
            "last_frame_id": frame_id,
            "last_frame_pts": frame_pts,
            "last_frame_time_base": frame_time_base,
            "last_frame_preview_mode": preview_mode,
        }
        try:
            await self.process_timed_frame(image, received_at, received_unix_ms)
        finally:
            self._frame_identity = {}

    async def process_identified_video(
        self, images, timestamps, received_at, received_unix_ms, **identity
    ):
        if self._processing_lock.locked():
            return
        self._frame_identity = {
            "last_frame_id": identity.get("frame_id"),
            "last_frame_pts": identity.get("frame_pts"),
            "last_frame_time_base": identity.get("frame_time_base"),
            "last_frame_preview_mode": identity.get("preview_mode"),
        }
        self._frame_started = received_at
        self._frame_received_unix_ms = received_unix_ms
        try:
            await self.process_video(images, timestamps)
        finally:
            self._frame_identity = {}
            self._frame_started = None
            self._frame_received_unix_ms = None

    async def process_timed_frame(self, image, received_at, received_unix_ms):
        """Measure from server frame receipt, including conversion and admission."""
        if self._processing_lock.locked():
            return
        self._frame_started = received_at
        self._frame_received_unix_ms = received_unix_ms
        try:
            await self.process_frame(image)
        finally:
            self._frame_started = None
            self._frame_received_unix_ms = None

    async def analyze_image(self, image, prompt=None):
        queued_at = time.perf_counter()
        async with self._semaphore:
            admitted_at = time.perf_counter()
            self._request_queue_ms = (admitted_at - queued_at) * 1000
            try:
                return await super().analyze_image(image, prompt)
            finally:
                self._request_latency_ms = (time.perf_counter() - admitted_at) * 1000

    async def analyze_video(self, images, timestamps, prompt=None):
        queued_at = time.perf_counter()
        async with self._semaphore:
            admitted_at = time.perf_counter()
            self._request_queue_ms = (admitted_at - queued_at) * 1000
            try:
                return await super().analyze_video(images, timestamps, prompt)
            finally:
                self._request_latency_ms = (time.perf_counter() - admitted_at) * 1000

    async def process_video(self, images, timestamps, prompt=None):
        parent = super()
        await self._process_observation(lambda: parent.process_video(images, timestamps, prompt))

    async def process_frame(self, image, prompt=None):
        parent = super()
        await self._process_observation(lambda: parent.process_frame(image, prompt))

    async def _process_observation(self, process):
        if self._processing_lock.locked():
            return
        started = self._frame_started if self._frame_started is not None else time.perf_counter()
        received_unix_ms = self._frame_received_unix_ms
        if received_unix_ms is None:
            received_unix_ms = time.time() * 1000
        self._on_status(True)
        try:
            previous_count = self.total_inferences
            await process()
            # Publish one coherent completed request. In-flight admission/timestamps
            # must never be paired with the previous result in GET snapshots.
            self._completed_timing = {
                "last_e2e_latency_ms": (time.perf_counter() - started) * 1000,
                "last_queue_ms": self._request_queue_ms,
                "last_latency_ms": self._request_latency_ms,
                "last_frame_received_unix_ms": received_unix_ms,
                "last_result_unix_ms": time.time() * 1000,
                "last_request_succeeded": self.total_inferences > previous_count,
                "last_frame_id": self._frame_identity.get("last_frame_id"),
                "last_frame_pts": self._frame_identity.get("last_frame_pts"),
                "last_frame_time_base": self._frame_identity.get("last_frame_time_base"),
                "last_frame_preview_mode": self._frame_identity.get("last_frame_preview_mode"),
            }
            self._result_completed_at = time.perf_counter()
            self._on_result(self.current_response, self.get_metrics())
        finally:
            self._on_status(False)

    def get_metrics(self):
        age = None
        if (
            self._result_completed_at is not None
            and self._completed_timing["last_request_succeeded"]
        ):
            age = self._completed_timing["last_e2e_latency_ms"] + max(
                0.0, (time.perf_counter() - self._result_completed_at) * 1000
            )
        return {**super().get_metrics(), **self._completed_timing, "observation_age_ms": age}


@dataclass(frozen=True)
class _SegmentationFrame:
    frame: object
    received_at: float
    received_unix_ms: float
    frame_id: int


def _encode_segmentation_preview(frame):
    """Encode one matched analysis frame, with bounded dimensions and JPEG size."""
    image = frame.to_image()
    image.thumbnail((320, 320), Image.Resampling.BILINEAR)
    with io.BytesIO() as buffer:
        image.save(buffer, format="JPEG", quality=75)
        encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return {
        "image": f"data:image/jpeg;base64,{encoded}",
        "width": image.width,
        "height": image.height,
        "frame_pts": frame.pts,
        "frame_time_base": str(frame.time_base) if frame.time_base is not None else None,
    }


async def _analysis_preview(frame):
    """Keep ownership until a submitted JPEG operation completes, even on stop."""
    job = asyncio.get_running_loop().run_in_executor(None, _encode_segmentation_preview, frame)
    return await SegmentationService._await_worker(job)


@dataclass
class Camera:
    camera_id: str
    source: str
    settings: dict = field(repr=False)
    process_every: int
    max_latency: float
    temporal: TemporalConfig = field(default_factory=TemporalConfig)
    rtsp_url: str | None = field(default=None, repr=False)
    demo_id: str | None = None
    service: VLMService | None = field(default=None, repr=False)
    state: str = "waiting"
    error: str | None = None
    track: object = field(default=None, repr=False)
    processor: object = field(default=None, repr=False)
    consumer: object = field(default=None, repr=False)
    pc: object = field(default=None, repr=False)
    preview: object = field(default=None, repr=False)
    close_task: object = field(default=None, repr=False)
    offer_task: object = field(default=None, repr=False)
    segmentation_queue: asyncio.Queue = field(
        default_factory=lambda: asyncio.Queue(maxsize=1), repr=False
    )
    segmentation_task: object = field(default=None, repr=False)
    segmentation_state: str = "disabled"
    segmentation_metrics: dict = field(default_factory=dict)
    segmentation_error: str | None = None
    segmentation_dropped: int = 0
    frames_received: int = 0
    offer_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def snapshot(self):
        return {
            "camera_id": self.camera_id,
            "source": self.source,
            "demo_id": self.demo_id,
            "state": self.state,
            "error": self.error,
            "model": self.settings.get("model"),
            "prompt": self.settings["prompt"],
            "process_every": self.process_every,
            "max_latency": self.max_latency,
            **(
                self.processor.temporal_status()
                if self.processor
                else {
                    "temporal": self.temporal.as_dict(),
                    "temporal_enabled": self.temporal.enabled,
                    "temporal_buffer_frames": 0,
                    "temporal_buffer_ready": False,
                }
            ),
            "response": self.service.current_response if self.service else "VLM analysis disabled",
            "metrics": self.service.get_metrics() if self.service else {},
            "frames_received": (
                self.processor.frame_count if self.processor else self.frames_received
            ),
            "dropped_frames": self.processor.dropped_frames if self.processor else 0,
            "segmentation": {
                "state": self.segmentation_state,
                "metrics": self.segmentation_metrics,
                "error": self.segmentation_error,
            },
        }


@dataclass
class CameraSession:
    session_id: str
    segmentation: SegmentationConfig = field(default_factory=SegmentationConfig)
    vlm_enabled: bool = True
    preview_mode: str = "segmented"
    cameras: dict = field(default_factory=dict)
    state: str = "starting"
    subscribers: set = field(default_factory=set, repr=False)
    sockets: set = field(default_factory=set, repr=False)
    close_task: object = field(default=None, repr=False)

    def snapshot(self):
        return {
            "session_id": self.session_id,
            "state": self.state,
            "camera_count": len(self.cameras),
            "vlm_enabled": self.vlm_enabled,
            "preview_mode": self.preview_mode,
            "segmentation_enabled": self.segmentation.enabled,
            "cameras": [camera.snapshot() for camera in self.cameras.values()],
        }

    def publish(self, message):
        if self.state == "stopped":
            return
        event = {"session_id": self.session_id, **message}
        for queue in self.subscribers:
            # Slow viewers cannot accumulate an unbounded history of results.
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)


class MultiCameraManager:
    """Own sessions, camera resources and bounded concurrency for one web application."""

    def __init__(self, defaults_provider: Callable, max_concurrent_inferences=2, max_sessions=4):
        self.defaults_provider = defaults_provider
        self.demo_max_width, self.demo_max_height = demo_playback_bounds()
        self.sessions = {}
        self.max_sessions = max_sessions
        self.max_concurrent_inferences = max_concurrent_inferences
        self.semaphore = asyncio.Semaphore(max_concurrent_inferences)
        self._lock = asyncio.Lock()
        self._closing = False
        self.segmentation = SegmentationService()

    def parse_session(self, data):
        if not isinstance(data, dict):
            raise TypeError("Request must be a JSON object")
        if set(data) - {
            "session_id",
            "cameras",
            "vlm",
            "process_every",
            "max_latency",
            "segmentation",
            "vlm_enabled",
            "preview_mode",
            "temporal",
        }:
            raise ValueError("Unknown session setting")
        session_id = validate_id(data.get("session_id", uuid4().hex), "session_id")
        cameras = data.get("cameras")
        if not isinstance(cameras, list) or len(cameras) not in (2, 4):
            raise ValueError("A multi-camera session must contain exactly 2 or 4 cameras")
        defaults = self.defaults_provider()
        settings = {
            "backend": "openai",
            "api_base": "http://localhost:8000/v1",
            "api_key": "EMPTY",
            "prompt": "Describe what you see in this image in one sentence.",
            "max_tokens": 512,
            "image_max_edge": 0,
            "temperature": 0.7,
        }
        if defaults is not None:
            settings.update({key: getattr(defaults, key, settings.get(key)) for key in VLM_FIELDS})
        settings.update(validate_vlm(data.get("vlm", {})))
        process_every = validate_number(
            data.get("process_every", VideoProcessorTrack.process_every_n_frames),
            "process_every",
            1,
            3600,
            integer=True,
        )
        max_latency = validate_number(
            data.get("max_latency", VideoProcessorTrack.max_frame_latency),
            "max_latency",
            0,
            10,
        )
        temporal = TemporalConfig.from_dict(data.get("temporal"))
        segmentation = SegmentationConfig.from_dict(data.get("segmentation"))
        vlm_enabled = data.get("vlm_enabled", True)
        if not isinstance(vlm_enabled, bool):
            raise TypeError("vlm_enabled must be a boolean")
        if not vlm_enabled and not segmentation.enabled:
            raise ValueError("Enable VLM analysis or segmentation")
        preview_mode = data.get("preview_mode", "segmented")
        if preview_mode not in ("smooth", "segmented"):
            raise ValueError("preview_mode must be smooth or segmented")
        session = CameraSession(
            session_id,
            segmentation=segmentation,
            vlm_enabled=vlm_enabled,
            preview_mode=preview_mode,
        )
        for item in cameras:
            if not isinstance(item, dict) or set(item) - {
                "camera_id",
                "source",
                "rtsp_url",
                "demo_id",
                "vlm",
                "process_every",
                "max_latency",
                "temporal",
            }:
                raise ValueError("Invalid camera settings")
            camera_temporal = temporal
            if "temporal" in item:
                # A camera may override the shared window without affecting peers.
                override = item["temporal"]
                if not isinstance(override, dict):
                    raise ValueError("camera.temporal must be an object")
                camera_temporal = TemporalConfig.from_dict({**temporal.as_dict(), **override})
            camera_id = validate_id(item.get("camera_id"), "camera_id")
            if camera_id in session.cameras:
                raise ValueError("camera_id values must be unique within a session")
            source = item.get("source", "webrtc")
            if source not in ("webrtc", "rtsp", "demo"):
                raise ValueError("Camera source must be webrtc, rtsp or demo")
            rtsp_url = item.get("rtsp_url")
            if source == "rtsp":
                if not isinstance(rtsp_url, str):
                    raise ValueError("RTSP cameras require rtsp_url")
                url = urlsplit(rtsp_url)
                if url.scheme not in ("rtsp", "rtsps") or not url.hostname:
                    raise ValueError("rtsp_url must be a valid RTSP(S) URL")
            elif rtsp_url is not None:
                raise ValueError("rtsp_url is only valid for RTSP cameras")
            demo_id = item.get("demo_id")
            if source == "demo":
                try:
                    resolve_demo(demo_id)
                except FileNotFoundError as exc:
                    raise web.HTTPServiceUnavailable(
                        text=json.dumps({"error": str(exc)}), content_type="application/json"
                    ) from exc
            elif demo_id is not None:
                raise ValueError("demo_id is only valid for demo sources")
            camera_settings = {**settings, **validate_vlm(item.get("vlm", {}))}
            if vlm_enabled and not camera_settings.get("model"):
                raise web.HTTPServiceUnavailable(
                    text=json.dumps({"error": "Configure a VLM model before starting cameras"}),
                    content_type="application/json",
                )
            camera_settings["api_key"] = camera_settings.get("api_key") or "EMPTY"
            session.cameras[camera_id] = Camera(
                camera_id,
                source,
                camera_settings,
                validate_number(
                    item.get("process_every", process_every), "process_every", 1, 3600, integer=True
                ),
                validate_number(item.get("max_latency", max_latency), "max_latency", 0, 10),
                temporal=camera_temporal,
                rtsp_url=rtsp_url,
                demo_id=demo_id,
            )
        return session

    async def create(self, data):
        session = self.parse_session(data)
        async with self._lock:
            if self._closing:
                raise web.HTTPServiceUnavailable()
            if session.session_id in self.sessions:
                raise web.HTTPConflict(text="Session already exists")
            if len(self.sessions) >= self.max_sessions:
                raise web.HTTPServiceUnavailable(text="Multi-camera session limit reached")
            self.sessions[session.session_id] = session
            tasks = []
            try:
                if session.segmentation.enabled:
                    await self.segmentation.prepare(session.segmentation)
                for camera in session.cameras.values():
                    camera.segmentation_state = (
                        "waiting" if session.segmentation.enabled else "disabled"
                    )
                    if session.vlm_enabled:
                        camera.service = MultiCameraVLMService(
                            semaphore=self.semaphore,
                            on_result=lambda text, metrics, camera=camera: session.publish(
                                {
                                    "type": "vlm_response",
                                    "camera_id": camera.camera_id,
                                    "text": text,
                                    "metrics": metrics,
                                }
                            ),
                            on_status=lambda is_processing, camera=camera: session.publish(
                                {
                                    "type": "vlm_status",
                                    "camera_id": camera.camera_id,
                                    "is_processing": is_processing,
                                }
                            ),
                            **camera.settings,
                        )
                    if camera.source == "rtsp":
                        tasks.append(asyncio.create_task(self._open_rtsp(session, camera)))
                    elif camera.source == "demo":
                        tasks.append(asyncio.create_task(self._open_demo(session, camera)))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                if any(isinstance(result, BaseException) for result in results):
                    raise web.HTTPBadGateway(text="Failed to open a camera source")
                session.state = "active"
                return session
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await self._close_session(session)
                self.sessions.pop(session.session_id, None)
                raise

    async def _open_rtsp(self, session, camera):
        camera.track = await RTSPVideoTrack.create(camera.rtsp_url)
        self._attach(session, camera, camera.track)

    async def _open_demo(self, session, camera):
        camera.track = await LocalVideoTrack.create(
            resolve_demo(camera.demo_id),
            max_width=self.demo_max_width,
            max_height=self.demo_max_height,
        )
        self._attach(session, camera, camera.track)

    def _attach(self, session, camera, track):
        camera.track = track
        camera.processor = VideoProcessorTrack(
            track,
            camera.service,
            process_every_n_frames=camera.process_every,
            max_frame_latency=camera.max_latency,
            temporal=camera.temporal,
            temporal_callback=lambda status: session.publish(
                {
                    "type": "temporal_status",
                    "camera_id": camera.camera_id,
                    **status,
                }
            ),
            process_on_recv=not (
                session.segmentation.enabled and session.preview_mode == "segmented"
            ),
        )
        camera.state = "streaming"
        if session.segmentation.enabled:
            camera.segmentation_state = "running"
            camera.segmentation_task = asyncio.create_task(self._segment(session, camera))
        camera.consumer = asyncio.create_task(self._consume(session, camera))

    async def _consume(self, session, camera):
        try:
            while True:
                frame = await camera.processor.recv()
                camera.frames_received += 1
                segmentation_active = (
                    session.segmentation.enabled and camera.segmentation_state != "failed"
                )
                # Smooth playback is independent of analysis completion. Only
                # source frames enter this preview, never a delayed mask overlay.
                if camera.preview and (session.preview_mode == "smooth" or not segmentation_active):
                    camera.preview.publish(frame)
                if segmentation_active:
                    received_at = getattr(camera.processor, "last_frame_received_at", None)
                    received_unix_ms = getattr(
                        camera.processor, "last_frame_received_unix_ms", None
                    )
                    queued = _SegmentationFrame(
                        frame,
                        received_at if received_at is not None else time.perf_counter(),
                        received_unix_ms if received_unix_ms is not None else time.time() * 1000,
                        camera.processor.frame_count,
                    )
                    if camera.segmentation_queue.full():
                        camera.segmentation_queue.get_nowait()
                        camera.segmentation_dropped += 1
                    camera.segmentation_queue.put_nowait(queued)
                elif not camera.processor.process_on_recv:
                    # A failed segmentation pipeline already displays raw fallback
                    # frames. Keep VLM available and identify that fallback clearly.
                    camera.processor.submit_frame(
                        frame,
                        received_at=camera.processor.last_frame_received_at,
                        received_unix_ms=camera.processor.last_frame_received_unix_ms,
                        frame_id=camera.processor.frame_count,
                        preview_mode="raw",
                    )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - source failures must remain isolated to one camera
            if session.state != "stopped":
                camera.state = "failed"
                camera.error = "Camera stream ended or could not be read"
                session.publish({"type": "camera_status", **camera.snapshot()})
                self._schedule_camera_close(camera)

    async def _segment(self, session, camera):
        completed_at = deque(maxlen=125)
        frames_completed = 0
        next_start = None
        period = 1.0 / session.segmentation.target_fps
        next_preview = 0.0
        try:
            while True:
                # Pace submissions, then take the latest frame. Never replay an
                # old mask just to make the displayed frame rate meet a target.
                if next_start is not None:
                    delay = next_start - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                queued = await camera.segmentation_queue.get()
                started_at = time.monotonic()
                if next_start is None or started_at - next_start >= period:
                    # A whole missed period starts a fresh schedule, avoiding
                    # catch-up bursts after slow GPU work or a stalled source.
                    next_start = started_at + period
                else:
                    # Retain the deadline grid across small timer overshoots.
                    # Resetting from each wake-up accumulates jitter and lowers FPS.
                    next_start += period
                annotated, metrics = await self.segmentation.render(
                    queued.frame, session.segmentation
                )
                if session.state == "stopped" or camera.state != "streaming":
                    return
                now = time.monotonic()
                completed_at.append(now)
                frames_completed += 1
                elapsed = now - completed_at[0]
                metrics = {
                    **metrics,
                    "fps": (len(completed_at) - 1) / elapsed if elapsed > 0 else 0.0,
                    "target_fps": session.segmentation.target_fps,
                    "frames_completed": frames_completed,
                    "dropped_frames": camera.segmentation_dropped,
                    "frame_age_ms": max(0.0, (time.perf_counter() - queued.received_at) * 1000),
                    "frame_id": queued.frame_id,
                    "frame_pts": queued.frame.pts,
                    "frame_time_base": (
                        str(queued.frame.time_base) if queued.frame.time_base is not None else None
                    ),
                    "frame_received_unix_ms": queued.received_unix_ms,
                }
                camera.segmentation_metrics = metrics
                event = {
                    "type": "segmentation_result",
                    "camera_id": camera.camera_id,
                    "state": "running",
                    "metrics": metrics,
                }
                if session.preview_mode == "segmented":
                    if camera.preview:
                        camera.preview.publish(annotated)
                    camera.processor.submit_frame(
                        queued.frame,
                        received_at=queued.received_at,
                        received_unix_ms=queued.received_unix_ms,
                        frame_id=queued.frame_id,
                        preview_mode="segmented",
                    )
                elif session.subscribers and now >= next_preview:
                    preview = await _analysis_preview(annotated)
                    if session.state == "stopped" or camera.state != "streaming":
                        return
                    next_preview = time.monotonic() + 0.2
                    preview.update(
                        frame_age_ms=max(0.0, (time.perf_counter() - queued.received_at) * 1000),
                        frame_received_unix_ms=queued.received_unix_ms,
                        generated_unix_ms=time.time() * 1000,
                        # Internal only: include subscriber queue time in the
                        # age sent over the socket, without relying on clock sync.
                        _received_at=queued.received_at,
                    )
                    event["preview"] = preview
                session.publish(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # Segmentation failure must not stop another camera or VLM.
            logger.exception("Segmentation failed for camera %s", camera.camera_id)
            camera.segmentation_state = "failed"
            camera.segmentation_error = "Segmentation failed; check the server runtime log"
            session.publish(
                {
                    "type": "segmentation_result",
                    "camera_id": camera.camera_id,
                    "state": "failed",
                    "error": camera.segmentation_error,
                    "metrics": {},
                }
            )

    def _schedule_camera_close(self, camera):
        if camera.close_task is None:
            camera.close_task = asyncio.create_task(self._close_camera(camera))
        return camera.close_task

    async def _close_camera(self, camera):
        if camera.offer_task and not camera.offer_task.done():
            camera.offer_task.cancel()
            await asyncio.gather(camera.offer_task, return_exceptions=True)
        if camera.consumer and not camera.consumer.done():
            camera.consumer.cancel()
            await asyncio.gather(camera.consumer, return_exceptions=True)
        # Cancel queued VLM work before waiting for non-cancelable GPU rendering.
        if camera.processor:
            camera.processor.stop()
        if camera.segmentation_task and not camera.segmentation_task.done():
            camera.segmentation_task.cancel()
            await asyncio.gather(camera.segmentation_task, return_exceptions=True)
        while not camera.segmentation_queue.empty():
            camera.segmentation_queue.get_nowait()
        camera.segmentation_state = "stopped"
        # Attempt every release, even if one source or peer fails to close.
        actions = []
        if camera.processor:
            actions.append(camera.processor.aclose)
        if camera.preview:
            actions.append(camera.preview.stop)
        if camera.track:
            actions.append(getattr(camera.track, "aclose", camera.track.stop))
        if camera.pc:
            actions.append(camera.pc.close)
        if camera.service:
            actions.append(camera.service.client.close)
        for action in actions:
            try:
                result = action()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001 - release every resource despite driver errors
                logger.error("Camera %s cleanup failed: %s", camera.camera_id, type(exc).__name__)

    async def _close_session(self, session):
        if session.close_task is None:
            session.state = "stopped"
            for camera in session.cameras.values():
                camera.state = "stopped"
            session.close_task = asyncio.create_task(self._release_session(session))
        # Teardown belongs to the manager, not the HTTP request that triggered it.
        await asyncio.shield(session.close_task)

    async def _release_session(self, session):
        try:
            await asyncio.gather(
                *[self._schedule_camera_close(camera) for camera in session.cameras.values()]
            )
            await asyncio.gather(*[ws.close() for ws in list(session.sockets)])
        finally:
            if self.sessions.get(session.session_id) is session:
                self.sessions.pop(session.session_id)

    async def delete(self, session_id):
        async with self._lock:
            session = self.get(session_id)
            await self._close_session(session)
            self.sessions.pop(session_id, None)

    def get(self, session_id):
        session = self.sessions.get(session_id)
        if session is None:
            raise web.HTTPNotFound(text="Unknown multi-camera session")
        return session

    async def close(self):
        self._closing = True
        async with self._lock:
            await asyncio.gather(*[self._close_session(s) for s in self.sessions.values()])
            self.sessions.clear()
            await self.segmentation.close()

    async def offer(self, session, camera, params):
        if not isinstance(params, dict) or params.get("type") != "offer":
            raise ValueError("A WebRTC offer with type='offer' is required")
        sdp = params.get("sdp")
        if not isinstance(sdp, str) or not sdp.strip():
            raise ValueError("sdp must be a nonempty string")
        try:
            description = SessionDescription.parse(sdp)
            video = [m for m in description.media if m.kind == "video"]
            if len(video) != 1 or video[0].port == 0:
                raise ValueError("Offer must contain exactly one active video section")
            direction = video[0].direction or "sendrecv"
            allowed = (
                ("sendrecv", "sendonly") if camera.source == "webrtc" else ("recvonly", "sendrecv")
            )
            if direction not in allowed:
                raise ValueError("Offer video direction does not match camera source")
        except (AssertionError, IndexError, TypeError) as exc:
            raise ValueError("Invalid SDP offer") from exc
        async with camera.offer_lock:
            if session.state != "active" or camera.state in ("failed", "stopped"):
                raise web.HTTPConflict(text="Camera is not available; create a new session")
            if camera.pc is not None:
                raise web.HTTPConflict(text="Camera already has a WebRTC connection")
            # Host ICE candidates support the Jetson LAN without external STUN.
            pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
            camera.pc = pc
            camera.offer_task = asyncio.current_task()

            @pc.on("connectionstatechange")
            async def connection_state():
                if pc.connectionState in ("failed", "closed"):
                    if camera.source == "webrtc" and camera.state != "stopped":
                        camera.state = "failed"
                        camera.error = "WebRTC connection closed"
                        session.publish({"type": "camera_status", **camera.snapshot()})
                        self._schedule_camera_close(camera)
                    elif camera.source != "webrtc" and camera.pc is pc:
                        # Closing an optional preview must not stop server-side inference.
                        if camera.preview:
                            camera.preview.stop()
                        camera.preview = None
                        camera.pc = None
                        await pc.close()

            @pc.on("track")
            def on_track(track):
                if (
                    track.kind == "video"
                    and camera.source == "webrtc"
                    and camera.track is None
                    and session.state == "active"
                    and camera.close_task is None
                ):
                    self._attach(session, camera, track)
                    camera.preview = CameraPreviewTrack()
                    pc.addTrack(camera.preview)
                else:
                    track.stop()

            try:
                await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="offer"))
                if camera.source != "webrtc":
                    camera.preview = CameraPreviewTrack()
                    pc.addTrack(camera.preview)
                await pc.setLocalDescription(await pc.createAnswer())
                if session.state != "active" or camera.close_task is not None:
                    raise web.HTTPConflict(text="Camera stopped during negotiation")
                return {
                    "sdp": pc.localDescription.sdp,
                    "type": pc.localDescription.type,
                    "session_id": session.session_id,
                    "camera_id": camera.camera_id,
                }
            except BaseException:
                # Do not await a cleanup task that is itself waiting for this offer.
                camera.offer_task = None
                if camera.source == "webrtc":
                    camera.state = "failed"
                    if camera.close_task is None:
                        await asyncio.shield(self._schedule_camera_close(camera))
                else:
                    if camera.preview:
                        camera.preview.stop()
                    camera.preview = None
                    camera.pc = None
                    await pc.close()
                raise
            finally:
                camera.offer_task = None


MULTI_CAMERA_KEY = web.AppKey("multi_camera", MultiCameraManager)


async def read_json(request):
    try:
        return await request.json()
    except (ValueError, TypeError) as exc:
        raise ValueError("Request must contain valid JSON") from exc


async def create_session(request):
    try:
        session = await request.app[MULTI_CAMERA_KEY].create(await read_json(request))
        return web.json_response(session.snapshot(), status=201)
    except (TypeError, ValueError) as exc:
        return api_error(400, str(exc))
    except SegmentationUnavailable as exc:
        return api_error(503, str(exc))


async def demo_videos(request):
    manager = request.app[MULTI_CAMERA_KEY]
    return web.json_response(
        demo_catalog(
            playback_max_width=manager.demo_max_width,
            playback_max_height=manager.demo_max_height,
        )
    )


async def segmentation_status(request):
    return web.json_response(await request.app[MULTI_CAMERA_KEY].segmentation.status())


async def session_status(request):
    return web.json_response(
        request.app[MULTI_CAMERA_KEY].get(request.match_info["session_id"]).snapshot()
    )


async def delete_session(request):
    await request.app[MULTI_CAMERA_KEY].delete(request.match_info["session_id"])
    return web.json_response({"session_id": request.match_info["session_id"], "status": "stopped"})


async def camera_offer(request):
    manager = request.app[MULTI_CAMERA_KEY]
    session = manager.get(request.match_info["session_id"])
    camera = session.cameras.get(request.match_info["camera_id"])
    if camera is None:
        return api_error(404, "Unknown camera")
    try:
        result = await manager.offer(session, camera, await read_json(request))
        return web.json_response(result)
    except (TypeError, ValueError) as exc:
        return api_error(400, str(exc))


async def session_websocket(request):
    session = request.app[MULTI_CAMERA_KEY].get(request.match_info["session_id"])
    if session.state != "active":
        raise web.HTTPConflict(text="Session is not active")
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    if session.state != "active":
        await ws.close()
        return ws
    queue = asyncio.Queue(maxsize=16)
    session.subscribers.add(queue)
    session.sockets.add(ws)

    async def send_events():
        try:
            while True:
                event = await queue.get()
                if "preview" in event:
                    preview = dict(event["preview"])
                    received_at = preview.pop("_received_at")
                    preview["frame_age_ms"] = max(0.0, (time.perf_counter() - received_at) * 1000)
                    event = {**event, "preview": preview}
                await ws.send_json(event)
        except (ConnectionError, RuntimeError):
            await ws.close()

    sender = None
    try:
        await ws.send_json({"type": "session_status", **session.snapshot()})
        sender = asyncio.create_task(send_events())
        async for _ in ws:
            pass
    finally:
        session.subscribers.discard(queue)
        session.sockets.discard(ws)
        if sender:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
    return ws


def register_multi_camera(app, defaults_provider):
    # The validated Edge-LLM profile supports four in-flight image requests.
    # Other OpenAI-compatible services retain the conservative existing budget.
    defaults = defaults_provider()
    concurrency = 4 if getattr(defaults, "backend", "openai") == "edgellm" else 2
    manager = MultiCameraManager(defaults_provider, max_concurrent_inferences=concurrency)
    app[MULTI_CAMERA_KEY] = manager
    base = "/api/multi-camera/sessions"
    app.router.add_get("/api/demo/videos", demo_videos)
    app.router.add_get("/api/segmentation/status", segmentation_status)
    app.router.add_post(base, create_session)
    app.router.add_get(base + "/{session_id}", session_status)
    app.router.add_delete(base + "/{session_id}", delete_session)
    app.router.add_get(base + "/{session_id}/ws", session_websocket)
    app.router.add_post(base + "/{session_id}/cameras/{camera_id}/offer", camera_offer)

    async def shutdown(_app):
        await manager.close()

    app.on_shutdown.append(shutdown)
