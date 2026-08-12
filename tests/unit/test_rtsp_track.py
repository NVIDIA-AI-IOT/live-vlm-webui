# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Unit tests for RTSPVideoTrack teardown safety and PTS re-stamping.

Covers two regressions:

1. Closing the PyAV container while the decode thread is still inside demux()
   frees libav memory out from under that thread and crashes the process.
   stop() must wait for the decode thread to leave the container.

2. Sources without timestamps on the wire (MJPEG over HTTP) get a demuxer
   synthesized PTS clock that ignores the real arrival rate. Those frames must
   be re-stamped from a wall clock or downstream latency math drifts forever.
"""

import threading
import time
from unittest.mock import patch

import pytest

from live_vlm_webui.rtsp_track import VIDEO_CLOCK_RATE, RTSPVideoTrack


class FakeStream:
    """Stand-in for av.video.VideoStream."""

    def __init__(self, average_rate=None):
        self.average_rate = average_rate
        self.width = 640
        self.height = 480
        self.codec_context = type("Ctx", (), {"name": "mjpeg"})()
        self.time_base = None


class FakeStreams:
    def __init__(self, stream):
        self.video = [stream]


class FakeContainer:
    """
    Minimal av.container.InputContainer stand-in.

    demux() blocks on `release` so a test can hold the decode thread inside the
    container exactly the way a real blocking network read does.
    """

    def __init__(self, stream, release=None):
        self.streams = FakeStreams(stream)
        self.closed = False
        self.closed_at = None
        self.demux_exited_at = None
        self._release = release

    def demux(self, stream):
        if self._release is not None:
            self._release.wait(timeout=5)
        self.demux_exited_at = time.monotonic()
        return iter([])

    def close(self):
        self.closed = True
        self.closed_at = time.monotonic()


class FakeFrame:
    def __init__(self, pts=1000, time_base="1/25"):
        self.pts = pts
        self.time_base = time_base


def make_track(container):
    """Build a track against a fake container, bypassing real network I/O."""
    with patch("av.open", return_value=container):
        return RTSPVideoTrack("rtsp://fake/stream")


class TestTeardownSafety:
    """stop() must not free the container while the decode thread is using it."""

    def test_stop_waits_for_decode_thread(self):
        release = threading.Event()
        stream = FakeStream(average_rate=30)
        container = FakeContainer(stream, release=release)
        track = make_track(container)

        # Decode thread enters demux() and stays there
        reader_done = threading.Event()

        def reader():
            track._read_frame()
            reader_done.set()

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.2)  # let the reader get inside demux()
        assert not container.closed, "container closed before decode thread started"

        # stop() from the "event loop" side while the reader is still inside
        release.set()
        track.stop()
        t.join(timeout=5)

        assert reader_done.is_set(), "decode thread never finished"
        assert container.closed, "stop() did not close the container"
        # The crash was close() landing while demux() was still running
        assert container.demux_exited_at is not None
        assert container.closed_at >= container.demux_exited_at, (
            "container was closed before the decode thread left demux() - "
            "this is the use-after-free"
        )

    def test_stop_is_idempotent(self):
        container = FakeContainer(FakeStream(average_rate=30))
        track = make_track(container)
        track.stop()
        track.stop()
        assert container.closed

    def test_read_frame_returns_none_after_stop(self):
        container = FakeContainer(FakeStream(average_rate=30))
        track = make_track(container)
        track.stop()
        assert track._read_frame() is None


class TestPtsRestamping:
    """Sources with no usable clock get wall-clock PTS; others are left alone."""

    def test_restamp_enabled_when_source_reports_no_rate(self):
        container = FakeContainer(FakeStream(average_rate=None))
        track = make_track(container)
        assert track._restamp_pts is True

    def test_restamp_disabled_when_source_reports_rate(self):
        container = FakeContainer(FakeStream(average_rate=30))
        track = make_track(container)
        assert track._restamp_pts is False

    def test_frames_untouched_when_source_has_clock(self):
        container = FakeContainer(FakeStream(average_rate=30))
        track = make_track(container)
        frame = FakeFrame(pts=4242, time_base="1/25")
        out = track._restamp(frame)
        assert out.pts == 4242
        assert out.time_base == "1/25"

    def test_restamped_pts_tracks_wall_clock(self):
        """
        The regression: a 25fps synthesized clock on a 10fps source made PTS
        advance 0.04s per frame while 0.1s of wall time passed, so the computed
        latency grew ~0.6s for every second of streaming. Re-stamped PTS must
        stay locked to wall clock instead.
        """
        container = FakeContainer(FakeStream(average_rate=None))
        track = make_track(container)

        first = track._restamp(FakeFrame())
        assert first.pts == 0

        time.sleep(0.3)
        second = track._restamp(FakeFrame())

        elapsed_from_pts = (second.pts - first.pts) / VIDEO_CLOCK_RATE
        assert elapsed_from_pts == pytest.approx(
            0.3, abs=0.15
        ), f"re-stamped PTS advanced {elapsed_from_pts:.3f}s over ~0.3s of wall time"
        assert second.time_base.denominator == VIDEO_CLOCK_RATE

    def test_restamp_timeline_resets_on_reconnect(self):
        container = FakeContainer(FakeStream(average_rate=None))
        track = make_track(container)
        track._restamp(FakeFrame())
        assert track._start_time is not None

        # A fresh connection must re-anchor the timeline
        with patch("av.open", return_value=FakeContainer(FakeStream(average_rate=None))):
            track._connect()
        assert track._start_time is None
