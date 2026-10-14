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
window.__browserCameraCount = Number(new URLSearchParams(location.search).get('test_cameras') ?? 4);
window.__cameraDeviceEvents = new EventTarget();
window.__devicechange = () => window.__cameraDeviceEvents.dispatchEvent(new Event('devicechange'));
window.__browserEnumerations = 0;
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
    window.__browserEnumerations += 1;
    if (window.__browserEnumerationDelay) await new Promise(resolve => setTimeout(resolve, window.__browserEnumerationDelay));
    const devices = window.__permission ? Array.from({length: window.__browserCameraCount}, (_, index) => ({
      kind: 'videoinput', deviceId: `usb-${index + 1}`, groupId: `physical-${index + 1}`, label: `USB ${index + 1}`,
    })) : [];
    if (devices.length) devices.push({...devices[0], deviceId: 'alias-for-first-camera'});
    return devices;
  },
  async getUserMedia(constraints) {
    window.__cameraRequests.push(constraints); window.__permission = true;
    if (!window.__browserCameraCount) throw new DOMException('No camera available', 'NotFoundError');
    return new MediaStream();
  },
  addEventListener(...args) { window.__cameraDeviceEvents.addEventListener(...args); },
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
                state["usb_catalog_active"] = state.get("usb_catalog_active", 0) + 1
                state["usb_catalog_max_active"] = max(
                    state.get("usb_catalog_max_active", 0), state["usb_catalog_active"]
                )
                ids = state.get(
                    "usb_device_ids",
                    [f"video{index * 2}" for index in range(state.get("usb_cameras", 2))],
                )
                try:
                    await asyncio.sleep(state.get("usb_delay", 0))
                    if state.get("usb_error"):
                        return await request.fulfill(
                            status=503, json={"error": "USB discovery unavailable"}
                        )
                    return await request.fulfill(
                        json={
                            "cameras": [
                                {
                                    "device_id": device_id,
                                    "label": f"HD Pro Webcam C920 (Jetson {index + 1})",
                                }
                                for index, device_id in enumerate(ids)
                            ],
                        }
                    )
                finally:
                    state["usb_catalog_active"] -= 1
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
                    session_id = path.rsplit("/", 1)[-1]
                    state["deleted"].append(session_id)
                    state["session_events"].append(["delete", session_id])
                    await asyncio.sleep(state.get("delete_delay", 0))
                    return await request.fulfill(
                        status=503 if state.get("fail_delete") else 200,
                        json={"stopped": not state.get("fail_delete")},
                    )
                if path.endswith("/offer"):
                    await asyncio.sleep(state.get("offer_delay", 0))
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
                state["session_events"].append(["create", payload["session_id"]])
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
            state.update(created=[], deleted=[], session_events=[], **options)
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
                "!document.querySelector('#usb-cameras').disabled && [...document.querySelectorAll('.source')].every(item => item.value === 'usb')"
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
            "document.querySelector('#usb-selection-status').textContent.includes('2 of 4')"
        )
        assert await page.locator("#notice").is_hidden()
        assert await page.locator(".source").evaluate_all(
            "items => items.every(item => item.value === 'usb')"
        )
        assert await page.locator(".usb-device").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["video0", "video2", "", ""]
        assert await page.locator("#start").is_disabled()
        assert "2 of 4" in await page.locator("#usb-selection-status").text_content()
        assert await page.locator("#camera-3-usb-hint").is_visible()
        assert await page.locator("#camera-4-usb-hint").is_visible()
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        checks.append(
            "insufficient Jetson USB cameras enter USB mode with distinct available devices, local guidance and Start disabled"
        )

        await page.locator("#two-cameras").click()
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "!document.querySelector('#usb-cameras').disabled && [...document.querySelectorAll('.source')].every(item => item.value === 'usb')"
        )
        assert await page.locator('#camera-2-usb option[value="video0"]').is_disabled()
        await page.locator("#camera-2-usb").evaluate(
            "element => { element.value = 'video0'; element.dispatchEvent(new Event('change', {bubbles: true})); }"
        )
        assert await page.locator("#camera-2-usb").input_value() == "video2"
        # Keep configuration validation as a second defense if DOM values change
        # without normal input events; no duplicate session may reach the server.
        await page.locator("#camera-2-usb").evaluate("element => { element.value = 'video0'; }")
        await page.locator("#start").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('different Jetson USB device')"
        )
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        checks.append(
            "assigned USB choices are disabled in other tiles and duplicate configuration is rejected before session creation"
        )

        await page.locator("#camera-2-usb").select_option("video2")
        state["usb_cameras"] = 1
        await page.locator("#start").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('select an available USB camera')"
        )
        assert not state["created"]
        checks.append("Start refreshes Jetson devices and rejects a disconnected camera")

        await load("?test_cameras=0", usb_cameras=1)
        await page.locator("#usb-cameras").click()
        await page.wait_for_function("document.querySelector('#camera-2-source').value === 'usb'")
        assert await page.locator(".usb-device").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["video0", ""]
        assert await page.locator('#camera-2-usb option[value="video0"]').is_disabled()
        assert await page.locator("#camera-2-usb-hint").is_visible()
        assert "1 of 2" in await page.locator("#usb-selection-status").text_content()
        assert await page.locator("#start").is_disabled()
        assert not state["created"]
        state["usb_cameras"] = 2
        await page.wait_for_function("document.querySelector('#camera-2-usb').value === 'video2'")
        assert await page.locator("#start").is_enabled()
        assert not await page.locator("#usb-selection-status").is_visible()
        assert not await page.locator("#camera-2-usb-hint").is_visible()
        assert await page.locator(
            "#notice"
        ).is_hidden(), "Do not retain a stale USB count notice after hotplug"
        assert not state["created"], "A manual page must wait for Start after hotplug"
        await page.locator("#start").click()
        await connected()
        assert [camera["usb_device_id"] for camera in state["created"][0]["cameras"]] == [
            "video0",
            "video2",
        ]
        await stopped()
        checks.append(
            "one-camera USB layout waits visibly and enables a complete distinct pair after hotplug without auto-start"
        )
        state["usb_device_ids"] = ["video0", "video4"]
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#usb-selection-status').textContent.includes('reconnect the missing camera')"
        )
        assert await page.locator("#camera-2-usb").input_value() == ""
        assert "disconnected" in await page.locator("#camera-2-usb-hint").text_content()
        assert await page.locator("#start").is_disabled()
        state["usb_device_ids"] = ["video0", "video2"]
        await page.wait_for_function("document.querySelector('#camera-2-usb').value === 'video2'")
        assert await page.locator("#start").is_enabled()
        assert len(state["created"]) == 1
        checks.append(
            "a remembered missing USB camera stays reserved instead of silently selecting a replacement and is restored on return"
        )

        await load("?test_cameras=0", usb_cameras=2)
        await page.locator("#usb-cameras").click()
        await page.wait_for_function("!document.querySelector('#start').disabled")
        await page.locator("#camera-2-usb").select_option("")
        assert await page.locator("#camera-2-usb").input_value() == ""
        assert await page.locator("#start").is_disabled()
        await page.locator("#four-cameras").click()
        await page.locator("#two-cameras").click()
        await page.wait_for_timeout(3200)
        assert (
            await page.locator("#camera-2-usb").input_value() == ""
        ), "A manual clear must survive refresh and layout changes"
        await page.locator("#camera-1-source").select_option("rtsp")
        await page.locator("#camera-1-rtsp").fill("rtsp://camera.example/live")
        assert await page.locator('#camera-2-usb option[value="video0"]').is_enabled()
        await page.locator("#camera-2-usb").select_option("video0")
        assert await page.locator("#start").is_enabled()
        assert not await page.locator("#usb-selection-status").is_visible()
        await page.locator("#camera-1-source").select_option("usb")
        assert await page.locator("#camera-1-usb").input_value() == "video2"
        assert await page.locator("#camera-2-usb").input_value() == "video0"
        await page.locator("#camera-1-source").select_option("rtsp")
        await page.locator("#start").click()
        await connected()
        assert [camera["source"] for camera in state["created"][0]["cameras"]] == ["rtsp", "usb"]
        assert state["created"][0]["cameras"][1]["usb_device_id"] == "video0"
        await stopped()
        checks.append(
            "manual USB clearing persists and mixed sources free devices without stealing an existing USB selection"
        )

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
            await load(
                f"?kiosk=1&demo=auto&cameras=auto&autostart=1&test_cameras={count}",
                usb_cameras=count,
            )
            await connected()
            assert len(state["created"]) == 1
            assert len(state["created"][0]["cameras"]) == streams
            assert all(camera["source"] == "usb" for camera in state["created"][0]["cameras"])
            assert [camera["usb_device_id"] for camera in state["created"][0]["cameras"]] == [
                f"video{i * 2}" for i in range(streams)
            ]
            assert await page.evaluate("window.__cameraRequests.length") == 0
            assert "autostart=1" in page.url
            await stopped()
            await page.wait_for_timeout(1600)
            assert len(state["created"]) == 1, "Stop must leave kiosk streams stopped"
        checks.append(
            "kiosk selects two/four Jetson USB cameras without browser permission and honors Stop"
        )

        await load("?kiosk=1&demo=usb&cameras=4&autostart=1&test_cameras=2")
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Waiting for 4 USB cameras')"
        )
        assert not state["created"]
        checks.append("explicit four-camera kiosk refuses to duplicate two attached cameras")

        for count in (0, 1):
            await load(
                f"?kiosk=1&demo=auto&cameras=auto&autostart=1&test_cameras={count}",
                usb_cameras=count,
            )
            await connected()
            assert len(state["created"][0]["cameras"]) == 4
            assert all(camera["source"] == "demo" for camera in state["created"][0]["cameras"])
            await stopped()
        checks.append("kiosk without two cameras automatically uses four store videos")

        async def device_change():
            before = state.get("usb_catalog_requests", 0)
            await page.evaluate("window.__devicechange()")
            # A back-to-back event can share an in-flight refresh; the next
            # bounded poll then observes the latest catalog without overlap.
            for _ in range(250):
                if state.get("usb_catalog_requests", 0) > before:
                    return
                await asyncio.sleep(0.02)
            raise AssertionError("devicechange did not refresh the Jetson catalog")

        async def created_count(count, timeout=6):
            for _ in range(int(timeout / 0.02)):
                if len(state["created"]) >= count:
                    await connected()
                    return
                await asyncio.sleep(0.02)
            raise AssertionError(f"Expected {count} sessions; got {len(state['created'])}")

        await load("?test_cameras=0", usb_cameras=0)
        await page.locator("#camera-1-source").select_option("usb")
        state["usb_cameras"] = 2
        await page.wait_for_function("document.querySelector('#camera-1-usb').value === 'video0'")
        assert await page.locator("#camera-2-usb").input_value() == "video2"
        assert not state["created"] and await page.evaluate("window.__cameraRequests.length") == 0
        await page.locator("#camera-1-usb").select_option("video2")
        state["usb_cameras"] = 1
        await device_change()
        await page.wait_for_function("document.querySelector('#camera-1-usb').value === ''")
        state["usb_cameras"] = 2
        await device_change()
        await page.wait_for_function("document.querySelector('#camera-1-usb').value === 'video2'")
        assert not state["created"]
        checks.append(
            "idle polling discovers hotplugged Jetson cameras and restores selected IDs after unplug/replug without capture"
        )

        before_browser = await page.evaluate("window.__browserEnumerations")
        state["usb_delay"] = 0.5
        before = state["usb_catalog_requests"]
        await page.evaluate("for (let i = 0; i < 8; i++) window.__devicechange()")
        await page.wait_for_timeout(700)
        assert state["usb_catalog_requests"] == before + 1
        assert state["usb_catalog_max_active"] == 1
        assert await page.evaluate("window.__browserEnumerations") > before_browser
        assert await page.evaluate("window.__cameraRequests.length") == 0
        state["usb_delay"] = 0
        state["usb_error"] = True
        await page.evaluate("document.querySelector('#notice').textContent = 'Operator message'")
        await device_change()
        await page.wait_for_timeout(100)
        assert await page.locator("#notice").text_content() == "Operator message"
        checks.append(
            "devicechange refreshes browser and server catalogs without overlapping USB requests or noisy offline messages"
        )

        for offer_delay in (1.2, 0):
            await load(offer_delay=offer_delay)
            await page.locator("#enable-cameras").click()
            await page.wait_for_function("document.querySelector('#camera-1-device').value !== ''")
            selections = await page.locator(".device").evaluate_all(
                "items => items.map(item => item.value)"
            )
            await page.evaluate("window.__browserEnumerationDelay = 600; window.__devicechange()")
            await page.locator("#start").click()
            # Capture has already opened both selected devices, while the earlier
            # background enumeration is still pending across the phase change.
            await page.wait_for_function("window.__cameraRequests.length === 3")
            await page.evaluate("window.__browserCameraCount = 0")
            await page.wait_for_timeout(800)
            assert await page.locator("#session-status").text_content() == (
                "Connecting cameras" if offer_delay else "Cameras connected"
            )
            assert (
                await page.locator(".device").evaluate_all("items => items.map(item => item.value)")
                == selections
            )
            assert await page.evaluate("window.__cameraRequests.length") == 3
            await connected()
            await stopped()
        checks.append(
            "a delayed browser devicechange response preserves selectors during camera startup and active capture"
        )

        await load("?test_cameras=0", usb_cameras=2)
        await page.locator("#usb-cameras").click()
        await page.wait_for_function("document.querySelector('#camera-1-source').value === 'usb'")
        await page.locator("#start").click()
        await connected()
        selections = await page.locator(".usb-device").evaluate_all(
            "items => items.map(item => item.value)"
        )
        state["usb_cameras"] = 0
        await device_change()
        await page.wait_for_timeout(100)
        assert (
            await page.locator(".usb-device").evaluate_all("items => items.map(item => item.value)")
            == selections
        )
        assert len(state["created"]) == 1 and not state["deleted"]
        await stopped()
        checks.append(
            "catalog changes do not alter active manual camera selections or restart manual capture"
        )

        await load("?kiosk=1&demo=auto&cameras=auto&autostart=1", usb_cameras=0)
        await connected()
        original = state["created"][0]["session_id"]
        assert all(camera["source"] == "demo" for camera in state["created"][0]["cameras"])
        state["usb_cameras"] = 2
        # Server polling must detect hotplug even when this browser emits no devicechange.
        await created_count(2)
        assert [camera["usb_device_id"] for camera in state["created"][1]["cameras"]] == [
            "video0",
            "video2",
        ]
        assert state["session_events"][:3] == [
            ["create", original],
            ["delete", original],
            ["create", state["created"][1]["session_id"]],
        ]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        state["usb_cameras"] = 0
        await device_change()
        await created_count(3)
        assert all(camera["source"] == "demo" for camera in state["created"][2]["cameras"])
        state["usb_device_ids"] = ["video4", "video6"]
        await device_change()
        await created_count(4)
        assert [camera["usb_device_id"] for camera in state["created"][3]["cameras"]] == [
            "video4",
            "video6",
        ]
        await stopped()
        state["usb_device_ids"] = []
        await device_change()
        state["usb_device_ids"] = ["video0", "video2", "video4", "video6"]
        await device_change()
        await page.wait_for_timeout(3200)
        assert len(state["created"]) == 4
        checks.append(
            "zero-camera kiosk switches store to USB, deletes before replacing sessions, recovers unplug/replug, and explicit Stop cancels hotplug"
        )

        await load("?kiosk=1&demo=auto&cameras=auto&autostart=1", usb_cameras=2)
        await connected()
        state["usb_cameras"] = 3
        await device_change()
        await page.wait_for_timeout(100)
        assert len(state["created"]) == 1
        state["usb_cameras"] = 4
        await device_change()
        await created_count(2)
        assert len(state["created"][1]["cameras"]) == 4
        state["usb_cameras"] = 2
        await device_change()
        await created_count(3)
        assert len(state["created"][2]["cameras"]) == 2
        await stopped()
        checks.append(
            "automatic USB layouts stay at two for three devices and switch only between supported two/four counts"
        )

        await load("?kiosk=1&demo=usb&cameras=2&autostart=1", usb_cameras=0)
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Waiting for 2 USB cameras')"
        )
        assert await page.locator("#stop").is_enabled()
        await stopped()
        state["usb_cameras"] = 2
        await device_change()
        await page.wait_for_timeout(100)
        assert not state["created"]
        checks.append("Stop cancels an explicit USB kiosk while it is waiting for cameras")

        await load("?kiosk=1&demo=auto&autostart=1", usb_cameras=0, usb_delay=0.6)
        await page.locator("#stop").click()
        state["usb_cameras"] = 2
        await page.wait_for_timeout(800)
        await device_change()
        await page.wait_for_timeout(800)
        assert not state["created"]
        checks.append(
            "Stop during initial discovery prevents a late catalog response from starting a session"
        )

        await load("?kiosk=1&demo=auto&autostart=1", usb_cameras=0)
        await connected()
        state["delete_delay"] = 0.6
        state["usb_cameras"] = 2
        await device_change()
        await page.wait_for_function(
            "document.querySelector('#session-status').textContent === 'Stopping cameras'"
        )
        await stopped()
        await page.wait_for_timeout(800)
        assert len(state["created"]) == 1
        checks.append(
            "Stop during automatic handoff cancels replacement after pending session cleanup"
        )

        await load("?kiosk=1&demo=auto&autostart=1", usb_cameras=0)
        await connected()
        state["fail_delete"] = True
        state["usb_cameras"] = 2
        await device_change()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('could not confirm session cleanup')"
        )
        await page.wait_for_timeout(3200)
        assert len(state["created"]) == 1
        checks.append(
            "failed old-session cleanup blocks automatic replacement and avoids leaking an additional capture session"
        )

        await load("?kiosk=1&demo=auto&autostart=1", usb_cameras=0, usb_error=True)
        await page.wait_for_timeout(100)
        assert not state["created"]
        state["usb_error"] = False
        state["usb_cameras"] = 2
        await device_change()
        await created_count(1)
        await stopped()
        await load("?kiosk=1&demo=auto&autostart=1", usb_cameras=2, model_available=False)
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Waiting for demo models')"
        )
        state["model_available"] = True
        await device_change()
        await created_count(1)
        await stopped()
        checks.append(
            "automatic kiosk retries initially unavailable USB discovery and model readiness"
        )

        await load("?kiosk=1&demo=store&cameras=2&autostart=1", usb_cameras=0)
        await connected()
        state["usb_cameras"] = 4
        await device_change()
        await page.wait_for_timeout(100)
        assert len(state["created"]) == 1 and not state["deleted"]
        assert all(camera["source"] == "demo" for camera in state["created"][0]["cameras"])
        await stopped()
        checks.append(
            "an explicit store kiosk stays on recorded inputs when cameras are plugged in"
        )

        await load("?kiosk=1&demo=usb&cameras=2&autostart=1", usb_cameras=2)
        await connected()
        original = state["created"][0]
        await page.evaluate(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).at(-1).close()"
        )
        await page.wait_for_function(
            "window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).length === 2"
        )
        assert len(state["created"]) == 1
        await page.evaluate(
            """session => {
                const socket = window.__sockets.filter(socket => socket.url.includes('/api/multi-camera/')).at(-1);
                socket.dispatchEvent(new MessageEvent('message', {data: JSON.stringify({
                    type: 'session_status', session_id: session.session_id,
                    cameras: session.cameras.map((camera, index) => ({
                        ...camera, state: index === 0 ? 'failed' : 'streaming',
                    })),
                })}));
            }""",
            original,
        )
        # The unchanged catalog must still replace a failed capture reported only
        # by the reconnect snapshot, respecting the bounded automatic retry delay.
        await created_count(2, timeout=15)
        replacement = state["created"][1]
        assert [camera["usb_device_id"] for camera in replacement["cameras"]] == [
            camera["usb_device_id"] for camera in original["cameras"]
        ]
        assert state["session_events"] == [
            ["create", original["session_id"]],
            ["delete", original["session_id"]],
            ["create", replacement["session_id"]],
        ]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        await stopped()
        checks.append(
            "a failed USB camera in a reconnect snapshot recovers with unchanged device IDs after releasing the old session"
        )

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
