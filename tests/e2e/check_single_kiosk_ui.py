#!/usr/bin/env python3
"""Check the upstream single-camera kiosk hooks with an offline mocked browser.

Run: python tests/e2e/check_single_kiosk_ui.py
Requires Playwright and Chromium. No model, GPU, or physical camera is used.
"""

import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

STATIC = Path(__file__).resolve().parents[2] / "src/live_vlm_webui/static"
CONFIG = {
    "type": "server_config",
    "model": "kiosk-cosmos",
    "api_base": "http://127.0.0.1:8012/v1",
    "backend": "edgellm",
    "prompt": "Describe the kiosk camera.",
    "max_tokens": 48,
    "process_every": 25,
    "temporal": {"enabled": True, "window_seconds": 2, "sample_fps": 2},
}
RESPONSE = 'Observed <img src=x onerror="window.injected=true"> safely.'
MOCK = r"""
window.__sockets = []; window.__sent = []; window.__mediaRequests = [];
window.__enumerated = false;
class MockSocket {
  static OPEN = 1;
  constructor() {
    this.readyState = 0; window.__sockets.push(this);
    setTimeout(() => {
      this.readyState = 1; this.onopen?.({});
      setTimeout(() => this.emit(window.__serverConfig), 5);
    }, 5);
  }
  emit(data) { this.onmessage?.({data: JSON.stringify(data)}); }
  send(data) { window.__sent.push(JSON.parse(data)); }
  close() { this.readyState = 3; this.onclose?.({}); }
}
window.WebSocket = MockSocket;
class MockPeer {
  addTrack() {}
  async createOffer() { return {type: 'offer', sdp: 'mock-offer'}; }
  async setLocalDescription(value) { this.localDescription = value; }
  async setRemoteDescription() { this.iceConnectionState = 'connected'; }
  close() { this.iceConnectionState = 'closed'; }
}
window.RTCPeerConnection = MockPeer;
window.RTCSessionDescription = class { constructor(value) { Object.assign(this, value); } };
Object.defineProperty(navigator, 'mediaDevices', {value: {
  async getUserMedia(constraints) {
    window.__mediaRequests.push(constraints);
    if (window.__mediaRequests.length === 1) {
      await new Promise(resolve => setTimeout(resolve, 150));
    }
    return new MediaStream();
  },
  async enumerateDevices() {
    window.__enumerated = true;
    return [{kind: 'videoinput', deviceId: 'usb-kiosk', label: 'Kiosk camera'}];
  },
  addEventListener() {},
}});
"""


async def main():
    checks = []
    state = {"offers": [], "discoveries": [], "enumerated_on_offer": []}
    errors = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context()
        await context.add_init_script("window.__serverConfig = " + json.dumps(CONFIG) + ";" + MOCK)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        async def route(request):
            url = urlparse(request.request.url)
            path = url.path
            if url.hostname != "127.0.0.1":
                # Upstream's icon and markdown CDNs are optional in kiosk mode.
                return await request.fulfill(body="", content_type="application/javascript")
            if path in ("/models", "/detect-services"):
                state["discoveries"].append(path)
                return await request.fulfill(
                    json={
                        "configured_model": CONFIG["model"],
                        "api_base": CONFIG["api_base"],
                        "backend": "edgellm",
                        "models": [{"id": CONFIG["model"], "current": True}],
                    }
                )
            if path == "/offer":
                state["offers"].append(request.request.post_data_json)
                state["enumerated_on_offer"].append(await page.evaluate("window.__enumerated"))
                return await request.fulfill(json={"type": "answer", "sdp": "mock-answer"})
            asset = STATIC / ("index.html" if path == "/" else path.lstrip("/"))
            if asset.is_file():
                return await request.fulfill(
                    path=asset,
                    content_type=mimetypes.guess_type(str(asset))[0] or "application/octet-stream",
                )
            return await request.fulfill(status=404, body="Mock route unavailable")

        await context.route("**/*", route)
        await page.goto("http://127.0.0.1:18766/?autostart=1")
        await page.wait_for_function("isAnalysisRunning")
        assert len(state["offers"]) == 1
        assert state["enumerated_on_offer"] == [True]
        assert state["offers"][0]["temporal"] == CONFIG["temporal"]
        assert state["discoveries"] == []
        assert await page.locator("#modelSelect").input_value() == CONFIG["model"]
        assert await page.locator("#apiBaseUrl").input_value() == CONFIG["api_base"]
        assert await page.locator("#promptText").input_value() == CONFIG["prompt"]
        assert await page.locator("#maxTokens").input_value() == str(CONFIG["max_tokens"])
        assert await page.locator("#processEvery").input_value() == str(CONFIG["process_every"])
        assert not any(
            message["type"] in ("update_model", "update_prompt", "update_processing")
            for message in await page.evaluate("window.__sent")
        )
        checks.append("autostart waits for enumeration and preserves server inference defaults")

        await page.evaluate("Promise.all([start(), start()])")
        await page.evaluate("window.__sockets[0].emit(window.__serverConfig)")
        assert len(state["offers"]) == 1
        checks.append("duplicate starts and repeated configuration do not create extra peers")

        await page.evaluate(
            "text => window.__sockets[0].emit({type:'vlm_response', text, "
            "metrics:{last_request_succeeded:true,total_inferences:1}})",
            RESPONSE,
        )
        assert await page.locator("#resultTextContent").text_content() == RESPONSE
        assert await page.locator("#resultTextContent img").count() == 0
        assert await page.evaluate("window.injected") is None
        checks.append("offline CDN fallbacks render model text safely")

        original_stream = await page.evaluate("streamId")
        await page.locator("#stopBtn").click()
        assert not await page.evaluate("isAnalysisRunning")
        assert len(state["offers"]) == 1
        await page.evaluate("window.__sockets[0].close()")
        await page.wait_for_function("old => streamId !== old", arg=original_stream)
        await page.wait_for_function("isAnalysisRunning")
        assert len(state["offers"]) == 2
        checks.append("upstream websocket reconnection reloads and restarts the kiosk")

        await page.goto("http://127.0.0.1:18766/")
        await page.wait_for_function("websocket && websocket.readyState === WebSocket.OPEN")
        await page.wait_for_function("window.__enumerated")
        assert not await page.evaluate("isAnalysisRunning")
        assert await page.evaluate("peerConnection") is None
        assert len(state["offers"]) == 2
        assert "/models" in state["discoveries"]
        assert await page.locator("#videoUnderstanding").input_value() == "temporal"
        assert await page.locator("#temporalWindow").input_value() == "2"
        checks.append("normal single-camera URL remains manual with existing temporal controls")
        assert not errors, errors
        await browser.close()
    print(json.dumps({"checks": checks, "page_errors": errors}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
