"""
Lifecycle tests for RTSPVideoTrack.

SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
SPDX-License-Identifier: Apache-2.0
"""

import shutil
import subprocess
import sys
import textwrap
import time

import pytest


def _free_udp_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
@pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg is needed to serve a test stream"
)
def test_stop_during_active_decode_does_not_crash():
    """Stopping a live stream must not free the container under the reader thread.

    Regression test for a crash, not a failure: frames are demuxed in an executor thread while
    stop() runs on the event loop, so closing the container without serialising the two frees it
    underneath libav and takes down the whole server process with SIGSEGV. Because the failure is
    a core dump rather than an exception, this has to run in a subprocess and assert on the exit
    code — an in-process test would kill the test runner itself.

    A *live* stream is required to reproduce it. Against a local file, demux() returns
    immediately and the reader is almost never parked inside libav when close() lands, so the
    race is invisible; against a network stream the reader blocks waiting for packets, which is
    exactly the state a real RTSP camera is in when a user presses Stop.
    """
    port = _free_udp_port()
    url = f"udp://127.0.0.1:{port}"

    streamer = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
            "error",
            "-re",
            "-stream_loop",
            "-1",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=30",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-f",
            "mpegts",
            f"{url}?pkt_size=1316",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    program = textwrap.dedent(f"""
        import asyncio
        from live_vlm_webui.rtsp_track import RTSPVideoTrack

        async def main():
            track = RTSPVideoTrack({url!r})

            async def pump():
                try:
                    while True:
                        await track.recv()
                except Exception:
                    pass

            task = asyncio.create_task(pump())
            await asyncio.sleep(1.0)      # let frames actually flow

            # Exactly what _stop_rtsp_session() does: cancelling the task does NOT stop the
            # executor thread that is already inside _read_frame().
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            track.stop()
            await asyncio.sleep(1.0)
            print("SURVIVED")

        asyncio.run(main())
        """)

    try:
        time.sleep(2.0)  # let the stream come up before the reader attaches
        result = subprocess.run(
            [sys.executable, "-c", program], capture_output=True, text=True, timeout=120
        )
    finally:
        streamer.terminate()
        streamer.wait(timeout=10)

    assert result.returncode == 0, (
        f"process exited {result.returncode} " f"(-11/139 means SIGSEGV): {result.stderr[-500:]}"
    )
    assert "SURVIVED" in result.stdout
