"""Regressions for camera negotiation, bounded previews and canceled teardown."""

import asyncio
from contextlib import asynccontextmanager

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, VideoStreamTrack
from aiortc.mediastreams import MediaStreamError
from aiortc.sdp import SessionDescription

from live_vlm_webui.multi_camera import CameraPreviewTrack, MultiCameraManager


@asynccontextmanager
async def running_session():
    manager = MultiCameraManager(lambda: None)
    session = await manager.create(
        {
            "session_id": "lifecycle-test",
            "vlm": {"model": "synthetic-model"},
            "cameras": [{"camera_id": "front"}, {"camera_id": "rear"}],
        }
    )
    try:
        yield manager, session
    finally:
        await asyncio.gather(manager.close(), return_exceptions=True)
        # Retain cleanup even when an intentionally failing regression cancels the
        # production close task before it can release the HTTP clients.
        await asyncio.gather(
            *(camera.service.client.close() for camera in session.cameras.values())
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("second_video_port", [0, 9])
async def test_offer_rejects_extra_video_section_even_when_rejected(second_video_port):
    browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    browser.addTrack(VideoStreamTrack())
    browser.addTrack(VideoStreamTrack())
    try:
        offer = await browser.createOffer()
        description = SessionDescription.parse(offer.sdp)
        description.media[1].port = second_video_port
        async with running_session() as (manager, session):
            camera = session.cameras["front"]
            with pytest.raises(ValueError, match="one.*video"):
                await manager.offer(session, camera, {"type": "offer", "sdp": str(description)})
            assert camera.pc is None
            assert camera.processor is None
            assert camera.consumer is None
            assert camera.state == "waiting"
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_canceling_delete_does_not_cancel_resource_cleanup_and_retry_succeeds():
    close_started = asyncio.Event()
    release_close = asyncio.Event()
    close_cancelled = asyncio.Event()
    close_finished = asyncio.Event()

    class SlowClosingTrack(VideoStreamTrack):
        async def aclose(self):
            close_started.set()
            try:
                await release_close.wait()
            except asyncio.CancelledError:
                close_cancelled.set()
                raise
            self.stop()
            close_finished.set()

    async with running_session() as (manager, session):
        source = SlowClosingTrack()
        session.cameras["front"].track = source
        deleting = asyncio.create_task(manager.delete(session.session_id))
        try:
            await asyncio.wait_for(close_started.wait(), timeout=2)
            deleting.cancel()
            with pytest.raises(asyncio.CancelledError):
                await deleting
            assert not close_cancelled.is_set()
            release_close.set()
            await asyncio.wait_for(close_finished.wait(), timeout=2)
            await asyncio.wait_for(manager.delete(session.session_id), timeout=2)
            assert not manager.sessions
            assert source.readyState == "ended"
            assert all(camera.service.client.is_closed() for camera in session.cameras.values())
        finally:
            release_close.set()
            deleting.cancel()
            await asyncio.gather(deleting, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("attach_before_pause", [False, True])
async def test_delete_during_offer_cancels_negotiation_and_closes_all_resources(
    monkeypatch, attach_before_pause
):
    remote_entered = asyncio.Event()
    remote_cancelled = asyncio.Event()
    hold_remote = asyncio.Event()
    original_remote = RTCPeerConnection.setRemoteDescription

    async def held_remote(peer, description):
        if attach_before_pause:
            await original_remote(peer, description)
        remote_entered.set()
        try:
            await hold_remote.wait()
        except asyncio.CancelledError:
            remote_cancelled.set()
            raise
        if not attach_before_pause:
            await original_remote(peer, description)

    monkeypatch.setattr(RTCPeerConnection, "setRemoteDescription", held_remote)
    browser = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    browser.addTrack(VideoStreamTrack())
    negotiation = None
    try:
        offer = await browser.createOffer()
        async with running_session() as (manager, session):
            camera = session.cameras["front"]
            negotiation = asyncio.create_task(
                manager.offer(session, camera, {"type": "offer", "sdp": offer.sdp})
            )
            await asyncio.wait_for(remote_entered.wait(), timeout=2)
            server_peer = camera.pc
            assert (camera.processor is not None) == attach_before_pause
            await asyncio.wait_for(manager.delete(session.session_id), timeout=2)
            result = (await asyncio.gather(negotiation, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            assert remote_cancelled.is_set()
            assert server_peer.connectionState == "closed"
            assert camera.offer_task is None
            assert not manager.sessions
            assert all(item.service.client.is_closed() for item in session.cameras.values())
            if attach_before_pause:
                assert camera.processor.readyState == "ended"
                assert camera.consumer.done()
                assert camera.track.readyState == "ended"
                assert camera.preview.readyState == "ended"
    finally:
        hold_remote.set()
        if negotiation is not None:
            negotiation.cancel()
            await asyncio.gather(negotiation, return_exceptions=True)
        await browser.close()


@pytest.mark.asyncio
async def test_slow_preview_receives_only_latest_frame_and_waits_for_new_frame():
    preview = CameraPreviewTrack()
    waiting = None
    try:
        for number in range(100):
            frame = av.VideoFrame(width=8, height=8, format="yuv420p")
            frame.pts = number
            preview.publish(frame)
        assert await preview.recv() is frame
        waiting = asyncio.create_task(preview.recv())
        await asyncio.sleep(0)
        assert not waiting.done(), "A slow viewer must not build a backlog of old frames"
        next_frame = av.VideoFrame(width=8, height=8, format="yuv420p")
        next_frame.pts = 100
        preview.publish(next_frame)
        assert await asyncio.wait_for(waiting, timeout=1) is next_frame
    finally:
        preview.stop()
        if waiting is not None:
            await asyncio.gather(waiting, return_exceptions=True)


@pytest.mark.asyncio
async def test_stopping_preview_unblocks_waiter_and_discards_late_frames():
    preview = CameraPreviewTrack()
    waiting = asyncio.create_task(preview.recv())
    await asyncio.sleep(0)
    preview.stop()
    with pytest.raises(MediaStreamError):
        await asyncio.wait_for(waiting, timeout=1)
    preview.publish(av.VideoFrame(width=8, height=8, format="yuv420p"))
    with pytest.raises(MediaStreamError):
        await preview.recv()
