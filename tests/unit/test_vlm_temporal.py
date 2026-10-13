"""Temporal requests, encoded media and cancellation ownership without model inference."""

import asyncio
import base64
import io
import json
import threading
from unittest.mock import AsyncMock

import av
import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image

from live_vlm_webui import vlm_service
from live_vlm_webui.vlm_service import VLMService


@pytest.fixture
async def api(monkeypatch):
    bodies = []
    clients = []
    state = {"text": "The person walks toward the checkout.", "status": 200}

    def handle(request):
        bodies.append(json.loads(request.content))
        if state["status"] != 200:
            return httpx.Response(state["status"], json={"error": {"message": "Video unsupported"}})
        return httpx.Response(
            200,
            json={
                "id": "temporal-test",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": state["text"]},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def create_client(**kwargs):
        client = AsyncOpenAI(
            **kwargs,
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )
        clients.append(client)
        return client

    monkeypatch.setattr(vlm_service, "AsyncOpenAI", create_client)
    yield bodies, state
    for client in clients:
        await client.close()


def images(count=4, size=(960, 540)):
    return [Image.new("RGB", size, (index * 50, 20, 100)) for index in range(count)]


async def test_native_video_encodes_all_frames_with_timestamps_without_mutation(api):
    bodies, _ = api
    service = VLMService("test-model", backend="edgellm", image_max_edge=512, temperature=0)
    originals = images()
    original_bytes = [image.tobytes() for image in originals]
    result = await service.analyze_video(originals, [2.0, 2.31, 2.97, 3.61], "What changed?")
    assert result == "The person walks toward the checkout."
    assert service.total_inferences == 1
    assert service.last_inference_time > 0
    assert len(bodies) == 1
    body = bodies[0]
    assert body["reuse_context"] is False and body["cache_generated_tokens"] is False
    assert body["temperature"] == 0
    content = body["messages"][0]["content"]
    assert "What changed?" in content[0]["text"]
    assert "0.000, 0.310, 0.970, 1.610" in content[0]["text"]
    assert [item["type"] for item in content] == ["text", "video_url"]
    video = content[1]
    assert video["nframes"] == 4 and "fps" not in video
    assert video["video_url"]["url"].startswith("data:video/mp4;base64,")
    encoded = base64.b64decode(video["video_url"]["url"].split(",", 1)[1])
    with av.open(io.BytesIO(encoded)) as container:
        frames = list(container.decode(video=0))
    assert len(frames) == 4
    assert {(frame.width, frame.height) for frame in frames} == {(512, 288)}
    assert [float(frame.pts * frame.time_base) for frame in frames] == pytest.approx(
        [0, 0.31, 0.97, 1.61], abs=1 / 90000
    )
    red = [frame.to_ndarray(format="rgb24")[:, :, 0].mean() for frame in frames]
    assert all(later > earlier + 35 for earlier, later in zip(red, red[1:]))
    assert all(image.size == (960, 540) for image in originals)
    assert [image.tobytes() for image in originals] == original_bytes


async def test_generic_temporal_request_preserves_image_order_and_provider_contract(api):
    bodies, _ = api
    service = VLMService("test-model", backend="openai", max_tokens=96)
    originals = images(3)
    await service.process_video(originals, [0.0, 0.5, 1.0], "Describe the action.")
    body = bodies[0]
    assert "reuse_context" not in body and "cache_generated_tokens" not in body
    assert body["max_tokens"] == 96
    content = body["messages"][0]["content"]
    assert [item["type"] for item in content] == [
        "text",
        "text",
        "image_url",
        "text",
        "image_url",
        "text",
        "image_url",
    ]
    assert [content[index]["text"] for index in (1, 3, 5)] == [
        "Frame at 0.000 seconds:",
        "Frame at 0.500 seconds:",
        "Frame at 1.000 seconds:",
    ]
    for index, item in enumerate(content[2::2]):
        encoded = base64.b64decode(item["image_url"]["url"].split(",", 1)[1])
        with Image.open(io.BytesIO(encoded)) as image:
            assert image.size == (640, 360)  # Bounded temporal default with original-size setting.
            assert image.getpixel((0, 0))[0] == pytest.approx(index * 50, abs=3)
    assert service.current_response == "The person walks toward the checkout."
    assert service.is_processing is False


@pytest.mark.parametrize(
    "count,times,backend,error",
    [
        (1, [0], "openai", "2-32 frames"),
        (33, list(range(33)), "openai", "2-32 frames"),
        (2, [0], "openai", "requires a timestamp"),
        (2, [0, 0], "openai", "strictly increasing"),
        (2, [1, 0], "openai", "strictly increasing"),
        (2, [0, float("nan")], "openai", "finite non-negative"),
        (2, [0, True], "openai", "finite non-negative"),
        (2, [-1, 0], "openai", "finite non-negative"),
        (2, [0, 31], "openai", "30 seconds"),
        (3, [0, 0.5, 1], "edgellm", "even number"),
        (2, [0, 0.000001], "edgellm", "too close"),
    ],
)
async def test_invalid_temporal_window_never_sends_or_counts_inference(
    api, count, times, backend, error
):
    bodies, _ = api
    service = VLMService("test-model", backend=backend)
    result = await service.analyze_video(images(count, (8, 8)), times)
    assert result.startswith("Error:") and error in result
    assert not bodies
    assert service.total_inferences == 0


async def test_stationary_scene_is_valid_when_observation_times_differ(api):
    bodies, _ = api
    service = VLMService("test-model", backend="edgellm")
    frame = Image.new("RGB", (16, 16))
    assert not (await service.analyze_video([frame, frame], [0, 0.5])).startswith("Error:")
    assert len(bodies) == 1


@pytest.mark.parametrize("failure", ["http", "empty"])
async def test_video_failures_do_not_silently_fall_back_to_image(api, failure):
    bodies, state = api
    service = VLMService("test-model", backend="edgellm")
    service.analyze_image = AsyncMock(side_effect=AssertionError("No silent fallback"))
    if failure == "http":
        state["status"] = 400
    else:
        state["text"] = "   "
    await service.process_video(images(2, (16, 16)), [0, 0.5])
    assert service.current_response.startswith("Error:")
    assert service.total_inferences == 0 and service.is_processing is False
    assert len(bodies) == 1
    service.analyze_image.assert_not_awaited()


async def test_video_and_single_frame_share_the_non_queuing_processing_lock(api):
    service = VLMService("test-model")
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*_args):
        entered.set()
        await release.wait()
        return "Temporal result"

    service.analyze_video = blocked
    service.analyze_image = AsyncMock(return_value="Single frame")
    frames = images(2, (16, 16))
    task = asyncio.create_task(service.process_video(frames, [0, 1]))
    await entered.wait()
    assert service.is_processing
    await service.process_frame(frames[0])
    await service.process_video(frames, [0, 1])
    service.analyze_image.assert_not_awaited()
    release.set()
    await task
    assert service.current_response == "Temporal result" and not service.is_processing
    await service.process_frame(frames[0])
    assert service.current_response == "Single frame"


async def test_encoding_runs_offloop_and_cancellation_waits_for_worker(api, monkeypatch):
    bodies, _ = api
    service = VLMService("test-model", backend="edgellm")
    entered, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    main_thread = threading.get_ident()
    threads = []

    def encoding(*_args):
        threads.append(threading.get_ident())
        loop.call_soon_threadsafe(entered.set)
        assert release.wait(timeout=5)
        return []

    monkeypatch.setattr(vlm_service, "_temporal_content", encoding)
    task = asyncio.create_task(service.process_video(images(2, (8, 8)), [0, 0.5]))
    try:
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and service.is_processing
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert threads and threads[0] != main_thread
        assert not bodies and service.current_response == "Initializing..."
        assert not service.is_processing and not service._processing_lock.locked()
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)


async def test_temporal_metrics_describe_completed_window_and_clear_on_failure(api):
    _, state = api
    service = VLMService("test-model", backend="edgellm")
    await service.process_video(images(4, (16, 16)), [2, 2.5, 3, 3.5])
    metrics = service.get_metrics()
    assert metrics["temporal_enabled"] is True
    assert metrics["temporal_frame_count"] == 4
    assert metrics["temporal_window_seconds"] == 1.5
    assert metrics["temporal_sample_fps"] == 2
    assert metrics["temporal_transport"] == "video"
    state["status"] = 400
    await service.process_video(images(2, (16, 16)), [0, 0.5])
    metrics = service.get_metrics()
    assert metrics["temporal_enabled"] is True
    assert metrics["temporal_frame_count"] == 0
    assert metrics["temporal_window_seconds"] is None
    assert metrics["temporal_sample_fps"] is None
    assert metrics["temporal_transport"] == "video"
    assert metrics["total_inferences"] == 1
    assert service.current_response.startswith("Error:")


@pytest.mark.parametrize("fail", [False, True])
async def test_single_image_completion_clears_previous_temporal_metrics(api, fail):
    _, state = api
    service = VLMService("test-model")
    await service.process_video(images(2, (16, 16)), [0, 0.5])
    assert service.get_metrics()["temporal_transport"] == "images"
    if fail:
        state["status"] = 400
    await service.process_frame(images(1, (16, 16))[0])
    metrics = service.get_metrics()
    assert metrics["temporal_enabled"] is False
    assert metrics["temporal_frame_count"] == 0
    assert metrics["temporal_window_seconds"] is None
    assert metrics["temporal_sample_fps"] is None
    assert metrics["temporal_transport"] is None


async def test_inflight_and_cancelled_window_preserve_last_completed_metadata(api, monkeypatch):
    service = VLMService("test-model")
    await service.process_video(images(2, (16, 16)), [0, 0.5])
    previous = service.get_metrics()
    entered = asyncio.Event()

    async def blocked(*_args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(vlm_service, "_prepare_temporal_content", blocked)
    task = asyncio.create_task(service.process_video(images(4, (16, 16)), [0, 1, 2, 3]))
    await entered.wait()
    assert service.get_metrics() == {**previous, "is_processing": True}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert service.get_metrics() == previous


async def test_video_even_geometry_stays_within_odd_max_edge(api):
    bodies, _ = api
    service = VLMService("test-model", backend="edgellm", image_max_edge=511)
    await service.analyze_video(images(2), [0, 0.5])
    url = bodies[0]["messages"][0]["content"][1]["video_url"]["url"]
    with av.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as container:
        frames = list(container.decode(video=0))
    assert len(frames) == 2
    assert all(max(frame.width, frame.height) <= 511 for frame in frames)
    assert all(not frame.width % 2 and not frame.height % 2 for frame in frames)
