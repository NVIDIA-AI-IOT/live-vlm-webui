#!/usr/bin/env python3
"""Offline Chromium checks for the opt-in multi-stream demo launcher.

Run with a Python environment containing Playwright and its Chromium browser:
    python tests/e2e/check_demo_launch_ui.py

All model, camera and network traffic is mocked. No serving process is started.
"""

import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

STATIC = Path(__file__).resolve().parents[2] / "src/live_vlm_webui/static"
MOCK_BROWSER = r"""
window.__cameraRequests = [];
window.__transceiverRequests = [];
window.__permission = false;
window.__sockets = [];
const cameraCount = Number(new URLSearchParams(location.search).get('test_cameras') ?? 4);
class MockSocket extends EventTarget {
  constructor(url) {
    super(); this.url = String(url); this.readyState = 0; this.sent = [];
    window.__sockets.push(this);
    setTimeout(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); }, 5);
  }
  send(value) { this.sent.push(value); }
  close() {
    if (this.readyState === 3) return;
    this.readyState = 3; this.dispatchEvent(new Event('close'));
  }
}
window.WebSocket = MockSocket;
class MockPeer extends EventTarget {
  constructor() {
    super(); this.connectionState = 'new'; this.iceGatheringState = 'complete';
  }
  addTrack() {}
  addTransceiver(kind, options) { window.__transceiverRequests.push({kind, ...options}); }
  async createOffer() { return {type: 'offer', sdp: 'mock-offer'}; }
  async setLocalDescription(value) { this.localDescription = value; }
  async setRemoteDescription() {
    this.connectionState = 'connected';
    this.dispatchEvent(new Event('connectionstatechange'));
  }
  close() { this.connectionState = 'closed'; }
}
window.RTCPeerConnection = MockPeer;
Object.defineProperty(navigator, 'mediaDevices', {value: {
  async enumerateDevices() {
    const devices = window.__permission ? Array.from({length: cameraCount}, (_, index) => ({
      kind: 'videoinput', deviceId: `usb-${index + 1}`, groupId: `physical-${index + 1}`, label: `USB ${index + 1}`,
    })) : [];
    if (devices.length) devices.push({...devices[0], deviceId: 'alias-for-first-camera'});
    return devices;
  },
  async getUserMedia(constraints) {
    window.__cameraRequests.push(constraints); window.__permission = true;
    if (!cameraCount) throw new DOMException('No camera available', 'NotFoundError');
    return new MediaStream();
  },
  addEventListener() {},
}});
"""


async def main():
    checks = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context()
        await context.add_init_script(MOCK_BROWSER)
        state = {}

        async def route(request):
            path = urlparse(request.request.url).path
            if path == "/models":
                # Model/backend discovery must finish before session creation.
                await asyncio.sleep(0.1)
                state["models_ready"] = True
                return await request.fulfill(
                    json={
                        "backend": "edgellm",
                        "models": (
                            [{"id": "test-cosmos", "current": True}]
                            if state.get("model_available", True)
                            else []
                        ),
                    }
                )
            if path == "/api/segmentation/status":
                return await request.fulfill(
                    json={
                        "available": state.get("segmentation_available", True),
                        "backend": "tensorrt",
                        "fixed_classes": ["person", "shopping cart"],
                        "fixed_imgsz": 480,
                        "recommended_confidence": 0.1,
                    }
                )
            if path == "/api/usb-cameras":
                state["usb_catalog_requests"] = state.get("usb_catalog_requests", 0) + 1
                if state.get("usb_error"):
                    return await request.fulfill(
                        status=503, json={"error": "USB discovery unavailable"}
                    )
                return await request.fulfill(
                    json={
                        "cameras": [
                            {
                                "device_id": f"video{index * 2}",
                                "label": f"HD Pro Webcam C920 (Jetson {index + 1})",
                            }
                            for index in range(state.get("usb_cameras", 2))
                        ],
                    }
                )
            if path == "/api/demo/videos":
                return await request.fulfill(
                    json={
                        "max_fps": 25,
                        "videos": [
                            {
                                "demo_id": f"clip-{i}",
                                "available": True,
                                "source_fps": 25,
                                "preferred_for_realtime": True,
                            }
                            for i in range(1, state.get("clips", 4) + 1)
                        ],
                    }
                )
            if path.startswith("/api/multi-camera/sessions"):
                if request.request.method == "GET":
                    return await request.fulfill(
                        status=404 if state.get("session_missing") else 200,
                        json={"status": "streaming"},
                    )
                if request.request.method == "DELETE":
                    state["deleted"].append(path.rsplit("/", 1)[-1])
                    return await request.fulfill(json={"stopped": True})
                if path.endswith("/offer"):
                    return await request.fulfill(
                        status=503 if state.get("fail_offer") else 200,
                        json=(
                            {"error": "Test camera connection failed"}
                            if state.get("fail_offer")
                            else {"type": "answer", "sdp": "mock-answer"}
                        ),
                    )
                assert state.get("models_ready"), "Session created before model discovery"
                payload = request.request.post_data_json
                state["created"].append(payload)
                return await request.fulfill(
                    status=201,
                    json={
                        "session_id": payload["session_id"],
                        "cameras": [
                            dict(camera, state="streaming") for camera in payload["cameras"]
                        ],
                    },
                )
            asset = STATIC / ("multi-camera.html" if path == "/multi-camera" else path.lstrip("/"))
            if asset.is_file():
                return await request.fulfill(
                    path=asset,
                    content_type=mimetypes.guess_type(str(asset))[0] or "application/octet-stream",
                )
            return await request.fulfill(status=404, body="Mock route not found")

        await context.route("**/*", route)
        page = await context.new_page()
        page_errors = []
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        async def load(query="", *, origin="http://127.0.0.1:18765", **options):
            state.clear()
            state.update(created=[], deleted=[], **options)
            await page.goto(origin + "/multi-camera" + query)
            await page.wait_for_function(
                "!document.querySelector('#model-status').textContent.includes('Loading')"
            )

        async def connected():
            await page.wait_for_function(
                "document.querySelector('#session-status').textContent === 'Cameras connected'"
            )

        async def stopped():
            stop = (
                page.locator("#focus-stop")
                if await page.locator("#focus-stop").is_visible()
                else page.locator("#stop")
            )
            await stop.click()
            await page.wait_for_function(
                "document.querySelector('#session-status').textContent === 'Not started'"
            )

        await load()
        assert not state["created"]
        assert await page.locator("#camera-grid").get_attribute("data-count") == "2"
        assert await page.locator("#video-understanding").input_value() == "single"
        assert await page.locator("#temporal-window").input_value() == "4"
        checks.append("normal page defaults unchanged")

        for count in (2, 4):
            await load("?test_cameras=0", origin="https://jetson.example.test", usb_cameras=count)
            await page.locator("#two-cameras" if count == 2 else "#four-cameras").click()
            before_refresh = state.get("usb_catalog_requests", 0)
            await page.locator("#usb-cameras").click()
            await page.wait_for_function(
                "document.querySelector('#notice').textContent.includes('Jetson USB cameras ready')"
            )
            assert state["usb_catalog_requests"] > before_refresh
            assert not state["created"], "Selecting Jetson cameras must wait for Start"
            assert await page.locator(".source").evaluate_all(
                "items => items.every(item => item.value === 'usb')"
            )
            assert await page.locator(".usb-device").evaluate_all(
                "items => items.map(item => item.value)"
            ) == [f"video{index * 2}" for index in range(count)]
            assert "HD Pro Webcam C920" in await page.locator(".usb-device").first.text_content()
            assert await page.locator(".usb-field").first.is_visible()
            assert not await page.locator(".device-field").first.is_visible()
            await page.locator("#start").click()
            await connected()
            assert [camera["usb_device_id"] for camera in state["created"][0]["cameras"]] == [
                f"video{index * 2}" for index in range(count)
            ]
            assert all(camera["source"] == "usb" for camera in state["created"][0]["cameras"])
            assert await page.evaluate("window.__cameraRequests.length") == 0
            assert (
                await page.evaluate("window.__transceiverRequests")
                == [{"kind": "video", "direction": "recvonly"}] * count
            )
            await stopped()
            assert state["deleted"] == [state["created"][0]["session_id"]]
        checks.append(
            "remote browser selects two/four Jetson USB cameras with distinct IDs, recvonly video and no local camera permission"
        )

        await load("?test_cameras=0", usb_cameras=2)
        await page.locator("#four-cameras").click()
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('needs 4 USB cameras')"
        )
        assert "found 2" in await page.locator("#notice").text_content()
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        checks.append("insufficient Jetson USB cameras give a clear count without starting streams")

        await page.locator("#two-cameras").click()
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Jetson USB cameras ready')"
        )
        await page.locator("#camera-2-usb").select_option("video0")
        await page.locator("#start").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('different Jetson USB device')"
        )
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        checks.append("duplicate Jetson USB selections are rejected before session creation")

        await page.locator("#camera-2-usb").select_option("video2")
        state["usb_cameras"] = 1
        await page.locator("#start").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('select an available USB camera')"
        )
        assert not state["created"]
        checks.append("Start refreshes Jetson devices and rejects a disconnected camera")

        await load("?test_cameras=0", usb_error=True)
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('USB discovery unavailable')"
        )
        assert not state["created"]
        assert await page.locator("#usb-cameras").is_enabled()
        checks.append("USB discovery failures remain visible and allow retry")

        await load("?demo=store&cameras=4&autostart=1&temporal=1")
        await connected()
        assert len(state["created"]) == 1
        payload = state["created"][0]
        assert len(payload["cameras"]) == 4
        assert len({camera["demo_id"] for camera in payload["cameras"]}) == 4
        assert payload["vlm"]["model"] == "test-cosmos"
        assert payload["vlm"]["backend"] == "edgellm"
        assert payload["vlm"]["max_tokens"] == 32
        assert payload["segmentation"]["target_fps"] == 25
        assert payload["segmentation"]["imgsz"] == 480
        assert payload["segmentation"]["classes"] == ["person", "shopping cart"]
        assert payload["temporal"] == {"enabled": True, "window_seconds": 2, "sample_fps": 2}
        assert "autostart" not in page.url
        assert await page.locator("body").evaluate(
            "element => element.classList.contains('presentation-view')"
        )
        await stopped()
        assert state["deleted"] == [payload["session_id"]]
        await page.reload()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Store demo ready')"
        )
        assert len(state["created"]) == 1
        checks.append(
            "four streams wait for readiness, apply settings, clean up, and do not autostart on refresh"
        )

        await load("?demo=store&cameras=2&autostart=1&temporal=0")
        await connected()
        assert len(state["created"][0]["cameras"]) == 2
        assert not state["created"][0]["temporal"]["enabled"]
        await stopped()
        checks.append("two-stream single-frame preset")

        await load("?demo=usb&autostart=1")
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('USB demo ready')"
        )
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        assert await page.locator("#camera-grid").get_attribute("data-count") == "2"
        await page.locator("#start").click()
        await connected()
        assert all(camera["source"] == "webrtc" for camera in state["created"][0]["cameras"])
        requests = await page.evaluate("window.__cameraRequests")
        assert [item["video"]["deviceId"]["exact"] for item in requests[1:]] == ["usb-1", "usb-2"]
        await stopped()
        checks.append("USB waits for user permission and selects distinct cameras")

        for count, streams in ((2, 2), (3, 2), (4, 4)):
            await load(f"?kiosk=1&demo=auto&cameras=auto&autostart=1&test_cameras={count}")
            await connected()
            assert len(state["created"]) == 1
            assert len(state["created"][0]["cameras"]) == streams
            assert all(camera["source"] == "webrtc" for camera in state["created"][0]["cameras"])
            requests = await page.evaluate("window.__cameraRequests")
            assert [item["video"]["deviceId"]["exact"] for item in requests[1:]] == [
                f"usb-{i}" for i in range(1, streams + 1)
            ]
            assert "autostart=1" in page.url
            await stopped()
            await page.wait_for_timeout(1600)
            assert len(state["created"]) == 1, "Stop must leave kiosk streams stopped"
        checks.append(
            "kiosk selects two/four real USB cameras, deduplicates aliases, and honors Stop"
        )

        await load("?kiosk=1&demo=usb&cameras=4&autostart=1&test_cameras=2")
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('needs 4 different cameras')"
        )
        assert not state["created"]
        checks.append("explicit four-camera kiosk refuses to duplicate two attached cameras")

        for count in (0, 1):
            await load(f"?kiosk=1&demo=auto&cameras=auto&autostart=1&test_cameras={count}")
            await connected()
            assert len(state["created"][0]["cameras"]) == 4
            assert all(camera["source"] == "demo" for camera in state["created"][0]["cameras"])
            await stopped()
        checks.append("kiosk without two cameras automatically uses four store videos")

        await load("?kiosk=1&demo=store&cameras=2&autostart=1")
        await connected()
        await page.evaluate(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).at(-1).close()"
        )
        await page.wait_for_function(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).length === 2"
        )
        assert len(state["created"]) == 1, "Transient results outage duplicated the session"
        state["session_missing"] = True
        await page.evaluate(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).at(-1).close()"
        )
        await page.wait_for_function(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).length === 1"
        )
        await connected()
        assert len(state["created"]) == 2
        assert state["created"][0]["session_id"] != state["created"][1]["session_id"]
        await stopped()
        checks.append(
            "kiosk reconnects existing sessions and only recreates a session after confirmed server loss"
        )

        for query in (
            "?demo=store&cameras=3&autostart=1",
            "?demo=store&demo=usb&autostart=1",
            "?demo=unknown&autostart=1",
            "?demo=store&autostart=yes",
            "?demo=store&temporal=invalid&autostart=1",
            "?demo=auto&cameras=auto&autostart=1",
            "?kiosk=1&kiosk=1&demo=auto&autostart=1",
            "?kiosk=yes&demo=store&autostart=1",
        ):
            await load(query)
            assert "Invalid demo link" in await page.locator("#notice").text_content()
            assert not state["created"]
        await load("?autostart=1&cameras=4")
        assert not state["created"]
        checks.append("invalid or non-demo query never starts inference")

        for options, message in (
            ({"clips": 2}, "needs 4 different clips"),
            ({"model_available": False}, "The VLM is unavailable"),
            ({"segmentation_available": False}, "Object masks are unavailable"),
        ):
            await load("?demo=store&cameras=4&autostart=1", **options)
            await page.wait_for_function("!document.querySelector('#start').disabled")
            assert message in await page.locator("#notice").text_content()
            assert not state["created"]
        checks.append("missing prerequisites do not create partial sessions")

        await load("?demo=store&cameras=4&autostart=1", fail_offer=True)
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Test camera connection failed')"
        )
        assert len(state["created"]) == 1
        assert state["deleted"] == [state["created"][0]["session_id"]]
        assert await page.locator("#session-status").text_content() == "Not started"
        checks.append("failed negotiation cleans up the automatic session")
        assert not page_errors, page_errors
        await browser.close()
    print(json.dumps({"checks": checks, "page_errors": page_errors}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
