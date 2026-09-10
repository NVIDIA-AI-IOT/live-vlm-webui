"""Check optional reasoning configuration at the HTTP and session boundaries."""

import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI
from PIL import Image

from live_vlm_webui.vlm_service import VLMService


@pytest.mark.parametrize("effort", [None, "none"])
def test_reasoning_request_and_final_answer(effort):
    async def run():
        requests = []

        def respond(request):
            body = json.loads(request.content)
            requests.append(body)
            return httpx.Response(
                200,
                json={"choices": [{"message": {"role": "assistant", "content": "A red image."}}]},
            )

        service = VLMService("gemma4:e2b", max_tokens=128, reasoning_effort=effort)
        await service.client.close()
        service.client = AsyncOpenAI(
            api_key="EMPTY",
            base_url="http://ollama.test/v1",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
        )
        try:
            assert await service.analyze_image(Image.new("RGB", (16, 16), "red")) == "A red image."
            assert requests[0]["max_tokens"] == 128
            assert requests[0]["messages"][0]["content"][1]["image_url"]["url"].startswith(
                "data:image/jpeg;base64,"
            )
            if effort is None:
                assert "reasoning_effort" not in requests[0]
                assert "reasoning_effort" not in service.get_last_request_payload()
            else:
                assert requests[0]["reasoning_effort"] == "none"
                assert service.get_last_request_payload()["reasoning_effort"] == "none"
        finally:
            await service.client.close()

    asyncio.run(run())


def test_new_sessions_inherit_reasoning_setting(monkeypatch):
    from live_vlm_webui import server

    monkeypatch.setattr(server, "sessions", {})
    monkeypatch.setattr(server, "default_vlm_config", {"reasoning_effort": "none"})
    service = server.get_or_create_session("test-reasoning")["vlm_service"]
    try:
        assert service.reasoning_effort == "none"
    finally:
        asyncio.run(service.client.close())
