"""Provider-specific request options must not leak to other VLM APIs."""

import asyncio
import base64
import io
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from aiohttp.test_utils import make_mocked_request
from openai import AsyncOpenAI
from PIL import Image

from live_vlm_webui import server, vlm_service
from live_vlm_webui.multi_camera import MultiCameraManager, MultiCameraVLMService, validate_vlm
from live_vlm_webui.vlm_service import VLMService


@pytest.fixture
async def requests_sent(monkeypatch):
    requests = []
    clients = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "A store aisle."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def client_factory(**kwargs):
        client = AsyncOpenAI(
            **kwargs, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        )
        clients.append(client)
        return client

    monkeypatch.setattr(vlm_service, "AsyncOpenAI", client_factory)
    yield requests
    for client in clients:
        await client.close()


@pytest.mark.parametrize("backend", ["openai", "edgellm"])
async def test_actual_json_request_is_provider_specific(requests_sent, backend):
    service = VLMService(model="test-model", backend=backend, max_tokens=128)
    result = await service.analyze_image(Image.new("RGB", (16, 16)), "Describe this aisle.")
    assert result == "A store aisle."
    assert len(requests_sent) == 1
    body = requests_sent[0]
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 128
    assert body["temperature"] == 0.7
    assert body["messages"][0]["content"][0]["text"] == "Describe this aisle."
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert "extra_body" not in body
    if backend == "edgellm":
        assert body["reuse_context"] is False
        assert body["cache_generated_tokens"] is False
    else:
        assert "reuse_context" not in body
        assert "cache_generated_tokens" not in body


async def test_legacy_api_update_preserves_selected_backend(requests_sent):
    service = VLMService(model="test-model", backend="edgellm")
    service.update_api_settings("http://localhost:8012/v1", "")
    assert service.backend == "edgellm"
    assert service.api_key == "EMPTY"
    await service.analyze_image(Image.new("RGB", (16, 16)))
    assert requests_sent[0]["reuse_context"] is False


@pytest.mark.parametrize("backend", ["vllm", "EdgeLLM", "", None, True])
def test_unknown_backend_is_rejected_before_client_creation(backend):
    with pytest.raises(ValueError, match="backend"):
        VLMService(model="test-model", backend=backend)
    with pytest.raises(ValueError, match="backend"):
        validate_vlm({"backend": backend})


@pytest.mark.parametrize("configured_backend", [None, "openai", "edgellm"])
def test_camera_backend_inherits_server_and_allows_camera_override(configured_backend):
    defaults = SimpleNamespace(
        model="test-model",
        api_base="http://localhost:8012/v1",
        api_key="EMPTY",
        prompt="Describe this aisle.",
        max_tokens=128,
    )
    if configured_backend is not None:
        defaults.backend = configured_backend
    manager = MultiCameraManager(lambda: defaults)
    session = manager.parse_session(
        {
            "cameras": [
                {"camera_id": "front"},
                {"camera_id": "rear", "vlm": {"backend": "openai"}},
            ]
        }
    )
    assert session.cameras["front"].settings["backend"] == (configured_backend or "openai")
    assert session.cameras["rear"].settings["backend"] == "openai"


@pytest.mark.parametrize("discovery_fails", [False, True])
async def test_model_discovery_exposes_configured_backend_even_on_fallback(
    monkeypatch, discovery_fails
):
    model_list = AsyncMock(
        return_value=SimpleNamespace(data=[SimpleNamespace(id="cosmos-nano-int4")])
    )
    if discovery_fails:
        model_list.side_effect = RuntimeError("Backend unavailable")
    monkeypatch.setattr(
        server,
        "vlm_service",
        SimpleNamespace(
            model="cosmos-nano-int4",
            backend="edgellm",
            api_base="http://localhost:8012/v1",
            client=SimpleNamespace(models=SimpleNamespace(list=model_list)),
        ),
    )
    response = await server.models(make_mocked_request("GET", "/models"))
    body = json.loads(response.text)
    assert body["backend"] == "edgellm"
    assert body["api_base"] == "http://localhost:8012/v1"
    assert body["configured_model"] == "cosmos-nano-int4"
    assert body["models"][0]["current"] is True
    assert "api_key" not in body


async def test_full_result_latency_includes_preparation_and_shared_slot_wait(requests_sent):
    semaphore = asyncio.Semaphore(0)
    results = []
    service = MultiCameraVLMService(
        model="test-model",
        semaphore=semaphore,
        on_result=lambda text, metrics: results.append((text, metrics)),
    )
    received_at = time.perf_counter() - 0.02
    received_unix_ms = time.time() * 1000 - 20
    task = asyncio.create_task(
        service.process_timed_frame(Image.new("RGB", (16, 16)), received_at, received_unix_ms)
    )
    try:
        await asyncio.sleep(0.03)
        assert not requests_sent, "Waiting for admission must not send a request"
        semaphore.release()
        await task
        text, metrics = results[0]
        assert text == "A store aisle."
        assert metrics["last_queue_ms"] >= 20
        assert metrics["last_e2e_latency_ms"] >= 45
        assert (
            metrics["last_e2e_latency_ms"] >= metrics["last_queue_ms"] + metrics["last_latency_ms"]
        )
        assert metrics["last_frame_received_unix_ms"] == received_unix_ms
        assert metrics["last_result_unix_ms"] >= received_unix_ms
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await service.client.close()


async def test_vlm_resize_changes_only_request_and_honors_temperature(requests_sent):
    service = VLMService(model="test-model", image_max_edge=512, temperature=0)
    original = Image.new("RGB", (960, 540), "red")
    await service.analyze_image(original)
    body = requests_sent[0]
    encoded = body["messages"][0]["content"][1]["image_url"]["url"].split(",", 1)[1]
    with Image.open(io.BytesIO(base64.b64decode(encoded))) as request_image:
        assert request_image.size == (512, 288)
    assert original.size == (960, 540)
    assert body["temperature"] == 0


@pytest.mark.parametrize(
    "setting,value",
    [
        ("image_max_edge", True),
        ("image_max_edge", 127),
        ("image_max_edge", 2049),
        ("image_max_edge", 512.0),
        ("temperature", True),
        ("temperature", float("nan")),
        ("temperature", -1),
        ("temperature", 3),
    ],
)
def test_invalid_vlm_performance_settings_are_rejected(setting, value):
    with pytest.raises((TypeError, ValueError)):
        validate_vlm({setting: value})
    with pytest.raises(ValueError):
        VLMService(model="test-model", **{setting: value})


async def test_inflight_snapshot_keeps_completed_timing_and_errors_do_not_reuse_latency(
    requests_sent,
):
    semaphore = asyncio.Semaphore(1)
    results = []
    service = MultiCameraVLMService(
        model="test-model",
        semaphore=semaphore,
        on_result=lambda text, metrics: results.append((text, metrics)),
    )
    image = Image.new("RGB", (16, 16))
    try:
        await service.process_timed_frame(image, time.perf_counter(), 1000)
        completed = service.get_metrics()
        await semaphore.acquire()
        task = asyncio.create_task(service.process_timed_frame(image, time.perf_counter(), 2000))
        await asyncio.sleep(0)
        inflight = service.get_metrics()
        for key in (
            "last_e2e_latency_ms",
            "last_queue_ms",
            "last_frame_received_unix_ms",
            "last_result_unix_ms",
        ):
            assert inflight[key] == completed[key]

        async def fail(**kwargs):
            await asyncio.sleep(0.02)
            raise RuntimeError("test backend unavailable")

        service.client.chat.completions.create = fail
        semaphore.release()
        await task
        failed = service.get_metrics()
        assert failed["last_request_succeeded"] is False
        assert failed["total_inferences"] == 1
        assert failed["last_frame_received_unix_ms"] == 2000
        assert failed["last_latency_ms"] >= 15
        assert results[-1][0].startswith("Error:")
    finally:
        await service.client.close()


@pytest.mark.parametrize("content", [None, ""])
async def test_empty_model_response_is_not_counted_as_success(requests_sent, content):
    service = VLMService(model="test-model")
    service.client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )
    )
    result = await service.analyze_image(Image.new("RGB", (16, 16)))
    assert result.startswith("Error:")
    assert service.total_inferences == 0


async def test_vlm_progress_clears_on_completion_and_cancellation(requests_sent):
    events = []
    service = MultiCameraVLMService(
        model="test-model",
        semaphore=asyncio.Semaphore(1),
        on_result=lambda text, metrics: events.append("result"),
        on_status=lambda processing: events.append(processing),
    )
    image = Image.new("RGB", (16, 16))
    await service.process_frame(image)
    assert events == [True, "result", False]
    events.clear()
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocked(**kwargs):
        started.set()
        await release.wait()

    service.client.chat.completions.create = blocked
    task = asyncio.create_task(service.process_frame(image))
    await started.wait()
    # A second call must not emit a false progress transition for the first.
    await service.process_frame(image)
    assert events == [True]
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert events == [True, False]
    assert service.is_processing is False
    await service.client.close()


async def test_reconnecting_viewer_receives_age_of_existing_observation(requests_sent):
    service = MultiCameraVLMService(
        model="test-model",
        semaphore=asyncio.Semaphore(1),
        on_result=lambda *_: None,
    )
    assert service.get_metrics()["observation_age_ms"] is None
    await service.process_timed_frame(Image.new("RGB", (16, 16)), time.perf_counter() - 0.03, 1000)
    first = service.get_metrics()
    await asyncio.sleep(0.02)
    later = service.get_metrics()
    assert first["observation_age_ms"] >= 30
    assert later["observation_age_ms"] >= first["observation_age_ms"] + 15
    assert later["last_e2e_latency_ms"] == first["last_e2e_latency_ms"]
    await service.client.close()
