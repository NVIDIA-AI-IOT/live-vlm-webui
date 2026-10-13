# SPDX-License-Identifier: Apache-2.0
"""Bounded, camera-local visual context for temporal VLM requests."""

import math
from collections import deque
from dataclasses import asdict, dataclass

from PIL import Image


@dataclass(frozen=True)
class TemporalConfig:
    enabled: bool = False
    window_seconds: int = 4
    sample_fps: int = 2

    @classmethod
    def from_dict(cls, value=None):
        if value is None:
            return cls()
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or set(value) - {"enabled", "window_seconds", "sample_fps"}:
            raise ValueError("temporal must contain only enabled, window_seconds and sample_fps")
        enabled = value.get("enabled", False)
        window = value.get("window_seconds", 4)
        fps = value.get("sample_fps", 2)
        if not isinstance(enabled, bool):
            raise ValueError("temporal.enabled must be a boolean")
        if type(window) is not int or window not in (2, 4, 8):
            raise ValueError("temporal.window_seconds must be 2, 4 or 8")
        if type(fps) is not int or fps != 2:
            raise ValueError("temporal.sample_fps must be 2")
        return cls(enabled, window, fps)

    @property
    def max_frames(self):
        return self.window_seconds * self.sample_fps

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class TemporalSample:
    image: Image.Image
    timestamp: float
    frame_id: int
    received_unix_ms: float
    frame_pts: int | None = None
    frame_time_base: str | None = None


class TemporalBuffer:
    """Own sampled RGB images; snapshots stay valid as newer samples replace them.

    A source restart, timestamp regression or long gap invalidates the old window.
    Frames are copied and bounded to 640 pixels on their longest edge, without
    changing the source or the segmentation/preview frame.
    """

    def __init__(self, config=None):
        self.config = TemporalConfig.from_dict(config)
        self.generation = 0
        self.samples = deque(maxlen=self.config.max_frames)
        self._next_sample_at = None
        self._last_received_at = None
        self._last_source_time = None

    def clear(self):
        self.generation += 1
        self.samples.clear()
        self._next_sample_at = None
        self._last_received_at = None
        self._last_source_time = None

    def due(self, timestamp, source_time=None):
        if not math.isfinite(timestamp):
            return False
        if self._last_received_at is not None and (
            timestamp < self._last_received_at
            or timestamp - self._last_received_at > self.config.window_seconds
            or (
                source_time is not None
                and self._last_source_time is not None
                and source_time < self._last_source_time
            )
        ):
            self.clear()
        self._last_received_at = timestamp
        if source_time is not None:
            self._last_source_time = source_time
        while self.samples and timestamp - self.samples[0].timestamp > self.config.window_seconds:
            self.samples.popleft()
        return self._next_sample_at is None or timestamp + 1e-6 >= self._next_sample_at

    def offer(
        self, image, timestamp, frame_id, received_unix_ms, frame_pts=None, frame_time_base=None
    ):
        if self.samples and timestamp <= self.samples[-1].timestamp:
            return False
        if self._next_sample_at is not None and timestamp + 1e-6 < self._next_sample_at:
            return False
        owned = image.convert("RGB")
        owned.thumbnail((640, 640), Image.Resampling.BILINEAR)
        self.samples.append(
            TemporalSample(owned, timestamp, frame_id, received_unix_ms, frame_pts, frame_time_base)
        )
        interval = 1 / self.config.sample_fps
        if self._next_sample_at is None:
            self._next_sample_at = timestamp + interval
        else:
            self._next_sample_at += interval
            if self._next_sample_at <= timestamp:
                self._next_sample_at = timestamp + interval
        return True

    @property
    def ready(self):
        return len(self.samples) == self.config.max_frames

    def snapshot(self):
        return tuple(self.samples)
