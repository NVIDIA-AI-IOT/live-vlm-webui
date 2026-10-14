#!/usr/bin/env python3
"""Offline browser checks for the multi-stream sidebar's original WebUI controls.

Run: python tests/e2e/check_sidebar_parity.py
Uses the existing demo browser mocks. No server, model, GPU, or camera is used.
"""

import asyncio
import hashlib
import json
import mimetypes
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from check_demo_launch_ui import MOCK_BROWSER, STATIC
from check_single_kiosk_ui import CONFIG as SINGLE_CONFIG
from check_single_kiosk_ui import MOCK as SINGLE_MOCK
from playwright.async_api import async_playwright

ORIGIN = "http://127.0.0.1:18767"
ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts/sidebar-parity"


def original_hashes():
    return {
        name: hashlib.sha256((STATIC / name).read_bytes()).hexdigest()
        for name in ("index.html", "webui.css")
    }


async def control_panel(page, selector):
    return await page.locator(selector).evaluate(
        "element => element.closest('.panel').querySelector('.panel-title').textContent.trim()"
    )


async def style(page, selector):
    return await page.locator(selector).first.evaluate(
        """element => {
            const css = getComputedStyle(element);
            return Object.fromEntries(['fontFamily','fontSize','fontWeight','color',
                'backgroundColor','borderRadius'].map(name => [name, css[name]]));
        }"""
    )


async def main():
    checks, errors = [], []
    initial_hashes = original_hashes()
    state = {
        "created": [],
        "deleted": [],
        "model_requests": [],
        "models": ["sidebar-cosmos", "other-model"],
    }
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(viewport={"width": 1440, "height": 1100})
        await context.add_init_script(MOCK_BROWSER)
        original_context = await browser.new_context(viewport={"width": 1440, "height": 1100})
        await original_context.add_init_script(
            "window.__serverConfig = " + json.dumps(SINGLE_CONFIG) + ";" + SINGLE_MOCK
        )

        async def route(request):
            url = urlparse(request.request.url)
            path = url.path
            if url.hostname != "127.0.0.1":
                return await request.fulfill(body="", content_type="application/javascript")
            if path == "/models":
                state["model_requests"].append(parse_qs(url.query))
                await asyncio.sleep(state.get("model_delay", 0))
                return await request.fulfill(
                    json={
                        "api_base": state.get("server_api_base", "http://127.0.0.1:8012/v1"),
                        "configured_model": "sidebar-cosmos",
                        "backend": state.get("server_backend", "edgellm"),
                        "models": [
                            {"id": model, "current": index == 0}
                            for index, model in enumerate(state["models"])
                        ],
                    }
                )
            if path == "/detect-services":
                return await request.fulfill(json={"services": []})
            if path == "/api/segmentation/status":
                return await request.fulfill(
                    json={
                        "available": True,
                        "backend": "tensorrt",
                        "fixed_classes": ["person", "shopping cart"],
                        "fixed_imgsz": 480,
                        "recommended_confidence": 0.1,
                    }
                )
            if path == "/api/usb-cameras":
                return await request.fulfill(
                    json={
                        "cameras": [
                            {
                                "device_id": f"video{index * 2}",
                                "label": f"Jetson USB camera {index + 1}",
                            }
                            for index in range(4)
                        ]
                    }
                )
            if path == "/api/demo/videos":
                return await request.fulfill(
                    json={
                        "max_fps": 25,
                        "videos": [
                            {
                                "demo_id": f"clip-{index}",
                                "available": True,
                                "source_fps": 25,
                                "preferred_for_realtime": True,
                            }
                            for index in range(4)
                        ],
                    }
                )
            if path.startswith("/api/multi-camera/sessions"):
                if request.request.method == "DELETE":
                    state["deleted"].append(path.rsplit("/", 1)[-1])
                    return await request.fulfill(json={"stopped": True})
                if request.request.method == "GET":
                    return await request.fulfill(json={"status": "streaming"})
                if path.endswith("/offer"):
                    return await request.fulfill(json={"type": "answer", "sdp": "mock-answer"})
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
            asset = STATIC / (
                {"/": "index.html", "/multi-camera": "multi-camera.html"}.get(
                    path, path.lstrip("/")
                )
            )
            if asset.is_file():
                return await request.fulfill(
                    path=asset,
                    content_type=mimetypes.guess_type(str(asset))[0] or "application/octet-stream",
                )
            return await request.fulfill(status=404, body="Offline mock route not found")

        await context.route("**/*", route)
        await original_context.route("**/*", route)
        original = await original_context.new_page()
        original.on("pageerror", lambda error: errors.append("single: " + str(error)))
        await original.goto(ORIGIN + "/")
        await original.add_style_tag(
            content="*,*::before,*::after { transition: none !important; animation: none !important; }"
        )
        await original.wait_for_function("websocket && websocket.readyState === WebSocket.OPEN")
        reference_titles = [
            " ".join(value.split())
            for value in await original.locator(".sidebar .panel-title").all_text_contents()
        ][:3]
        reference_prompts = await original.locator("#promptPreset option").evaluate_all(
            "options => options.map(option => ({value:option.value,text:option.textContent.trim()}))"
        )
        reference_api = await original.locator(".api-preset-item").evaluate_all(
            "items => items.map(item => item.dataset.url)"
        )
        assert reference_titles == [
            "VLM API Configuration",
            "Camera and App Control",
            "Prompt Editor",
        ]
        assert await control_panel(original, "#startBtn") == "Camera and App Control"
        assert await control_panel(original, "#videoUnderstanding") == "Camera and App Control"
        reference_panel = await style(original, ".sidebar .panel")
        reference_title = await style(original, ".sidebar .panel-title")
        reference_model = await style(original, "#modelSelect")
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        await original.screenshot(path=str(ARTIFACTS / "original-sidebar.png"), full_page=True)

        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append("multi: " + str(error)))

        async def ready():
            await page.wait_for_function(
                "document.querySelector('#model-status').hidden || !document.querySelector('#model-status').textContent.includes('Loading')"
            )

        async def connected():
            await page.wait_for_function(
                "document.querySelector('#session-status').textContent === 'Cameras connected'"
            )

        async def stopped():
            await page.wait_for_function(
                "document.querySelector('#session-status').textContent === 'Not started'"
            )

        await page.goto(ORIGIN + "/multi-camera")
        await page.add_style_tag(
            content="*,*::before,*::after { transition: none !important; animation: none !important; }"
        )
        await ready()
        titles = [
            " ".join(value.split())
            for value in await page.locator(".sidebar .panel-title").all_text_contents()
        ]
        assert titles[:3] == reference_titles, titles
        assert await page.locator("#model").evaluate("element => element.tagName") == "SELECT"
        assert await page.locator("#model").input_value() == "sidebar-cosmos"
        assert await page.locator("#api-base").input_value() == "http://127.0.0.1:8012/v1"
        assert await style(page, ".sidebar .panel") == reference_panel, (
            reference_panel,
            await style(page, ".sidebar .panel"),
        )
        assert await style(page, ".sidebar .panel-title") == reference_title, (
            reference_title,
            await style(page, ".sidebar .panel-title"),
        )
        assert await style(page, "#model") == reference_model, (
            reference_model,
            await style(page, "#model"),
        )
        await page.screenshot(path=str(ARTIFACTS / "multi-stream-sidebar.png"), full_page=True)
        for selector in (
            "#start",
            "#stop",
            "#video-understanding",
            "#temporal-window",
            "#process-every",
            "#source-webcam",
            "#source-rtsp",
        ):
            assert await control_panel(page, selector) == "Camera and App Control", selector
        assert await control_panel(page, "#shared-prompt") == "Prompt Editor"
        assert await page.locator(".stream-toolbar #start, .stream-toolbar #stop").count() == 0
        assert not await page.locator("#focus-stop").is_visible()
        assert await page.locator("#start").is_visible()
        assert await page.locator("#stop").is_disabled()
        checks.append(
            "original sidebar panel order, select model, native styles and camera-control placement"
        )

        actual_prompts = await page.locator("#prompt-preset option").evaluate_all(
            "options => options.map(option => ({value:option.value,text:option.textContent.trim()}))"
        )
        reference_prompt_values = {item["text"]: item["value"] for item in reference_prompts}
        expected_core_prompts = {
            "Scene Description",
            "Object Detection",
            "Activity Recognition",
            "Safety Monitoring",
            "Accessibility",
            "OCR / Text Reading",
            "Yes/No Question",
        }
        assert {item["text"] for item in actual_prompts if item["value"]} == expected_core_prompts
        assert all(
            reference_prompt_values.get(item["text"]) == item["value"] for item in actual_prompts
        )
        activity = next(
            item["value"] for item in reference_prompts if item["text"] == "Activity Recognition"
        )
        await page.locator("#prompt-preset").select_option(activity)
        assert await page.locator("#shared-prompt").input_value() == activity
        await page.locator("#shared-prompt").fill("A custom visible-action prompt.")
        assert await page.locator("#prompt-preset").input_value() == ""
        await page.locator("#prompt-preset").select_option(activity)
        checks.append(
            "offered core presets retain original literal prompts, and custom typing clears preset selection"
        )

        await page.locator("#model").select_option("other-model")
        requests_before = len(state["model_requests"])
        await page.locator("#refresh-models").click()
        await ready()
        assert len(state["model_requests"]) > requests_before
        assert await page.locator("#model").input_value() == "other-model"
        page.once("dialog", lambda dialog: dialog.accept("manual/my-model"))
        await page.locator("#add-model").click()
        assert await page.locator("#model").input_value() == "manual/my-model"
        await page.locator("#refresh-models").click()
        await ready()
        assert await page.locator("#model").input_value() == "manual/my-model"
        checks.append(
            "model selection, refresh and an explicitly added custom model survive refresh"
        )

        state["server_api_base"] = "http://127.0.0.1:8022/v1"
        state["server_backend"] = "openai"
        await page.locator("#refresh-models").click()
        await ready()
        assert await page.locator("#api-base").input_value() == state["server_api_base"]
        assert await page.locator("#vlm-backend").input_value() == "openai"
        assert await page.locator("#model").input_value() == "manual/my-model"
        assert "api_base" not in state["model_requests"][-1]
        manual_endpoint = "http://manually-configured.example/v1"
        await page.locator("#api-base").fill(manual_endpoint)
        await page.locator("#refresh-models").click()
        await ready()
        state["server_api_base"] = "http://127.0.0.1:8033/v1"
        state["server_backend"] = "edgellm"
        await page.locator("#refresh-models").click()
        await ready()
        assert await page.locator("#api-base").input_value() == manual_endpoint
        assert await page.locator("#vlm-backend").input_value() == "openai"
        assert state["model_requests"][-1].get("api_base") == [manual_endpoint]
        checks.append(
            "Refresh follows changed server endpoint/backend until the URL is explicitly edited"
        )

        actual_api = await page.locator("#api-presets-menu [data-api-url]").evaluate_all(
            "items => items.map(item => item.dataset.apiUrl)"
        )
        assert actual_api == reference_api
        for endpoint in reference_api:
            await page.locator("#api-presets").click()
            await page.locator("#api-presets-menu [data-api-url]").filter(has_text=endpoint).click()
            await ready()
            assert await page.locator("#api-base").input_value() == endpoint
            assert await page.locator("#vlm-backend").input_value() == "openai"
            assert state["model_requests"][-1].get("api_base") == [endpoint]
            assert not await page.locator("#api-presets-menu").is_visible()
        checks.append(
            "all five original API presets configure their endpoint and refresh compatible models"
        )

        page.once("dialog", lambda dialog: dialog.accept("manual/my-model"))
        await page.locator("#add-model").click()
        assert await page.locator("#model").input_value() == "manual/my-model"

        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Jetson USB cameras ready')"
        )
        assert await page.locator(".source").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["usb", "usb"]
        await page.locator("#source-rtsp").click()
        assert await page.locator(".source").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["rtsp", "rtsp"]
        for number in (1, 2):
            await page.locator(".rtsp-url").nth(number - 1).fill(f"rtsp://camera-{number}/stream")
        await page.locator("#source-webcam").click()
        assert await page.locator(".source").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["usb", "usb"]
        assert await page.locator(".usb-device").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["video0", "video2"]
        await page.locator("#source-rtsp").click()
        assert await page.locator(".rtsp-url").evaluate_all(
            "items => items.map(item => item.value)"
        ) == ["rtsp://camera-1/stream", "rtsp://camera-2/stream"]
        await page.locator("#source-webcam").click()
        assert await page.evaluate("window.__cameraRequests.length") == 0
        checks.append(
            "source tabs bulk-switch and restore explicit Jetson USB IDs and RTSP URLs without browser permission"
        )

        await page.locator("#video-understanding").select_option("temporal")
        assert await page.locator("#temporal-window-field").is_visible()
        await page.locator("#temporal-window").select_option("8")
        await page.locator("#process-every").fill("17")
        await page.locator("#start").click()
        await connected()
        created = state["created"][-1]
        assert len(created["cameras"]) == 2
        assert created["temporal"] == {"enabled": True, "window_seconds": 8, "sample_fps": 2}
        assert created["process_every"] == 17
        assert created["vlm"]["prompt"] == activity
        assert created["vlm"]["model"] == "manual/my-model"
        assert all(camera["source"] == "usb" for camera in created["cameras"])
        assert await page.locator("#source-webcam").is_disabled()
        assert await page.locator("#source-rtsp").is_disabled()
        assert await page.locator("#video-understanding").is_disabled()
        assert await page.locator("#process-every").is_disabled()
        assert await page.locator("#stop").is_enabled()
        await page.locator("#presentation-toggle").click()
        assert not await page.locator(".sidebar").is_visible()
        assert await page.locator("#focus-stop").is_visible()
        await page.locator("#focus-stop").click()
        await stopped()
        assert await page.locator(".sidebar").is_visible()
        assert not await page.locator("#focus-stop").is_visible()
        checks.append(
            "sidebar Start sends edited inference settings and focus Stop remains reachable"
        )

        await page.locator("#four-cameras").click()
        assert await page.locator("#camera-grid").get_attribute("data-count") == "4"
        assert await page.locator(".camera-card").count() == 4
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Jetson USB cameras ready')"
        )
        await page.locator("#start").click()
        await connected()
        assert len(state["created"][-1]["cameras"]) == 4
        if not await page.locator("#focus-stop").is_visible():
            await page.locator("#presentation-toggle").click()
        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(ARTIFACTS / "four-camera-focus.png"), full_page=True)
        await page.locator("#focus-stop").click()
        await stopped()
        await page.locator("#two-cameras").click()
        assert await page.locator(".camera-card").count() == 2
        checks.append("two/four source grids and four-camera focus lifecycle remain intact")

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.locator("#start").scroll_into_view_if_needed()
        await page.locator("#start").click()
        await connected()
        if not await page.locator("#focus-stop").is_visible():
            await page.locator("#presentation-toggle").click()
        await page.locator("#focus-stop").scroll_into_view_if_needed()
        bounds = await page.locator("#focus-stop").bounding_box()
        assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390
        await page.screenshot(path=str(ARTIFACTS / "mobile-focus-stop.png"), full_page=True)
        await page.locator("#focus-stop").click()
        await stopped()
        assert await page.locator("#start").is_enabled()
        checks.append(
            "mobile sidebar Start and focus Stop are visible, clickable and within viewport"
        )

        state["model_delay"] = 0.2
        await page.goto(ORIGIN + "/multi-camera")
        await page.locator("#api-base").fill("http://custom-server.example/v1")
        await ready()
        assert await page.locator("#api-base").input_value() == "http://custom-server.example/v1"
        checks.append("delayed initial discovery preserves a URL the user has already edited")
        assert not errors, errors
        assert (
            original_hashes() == initial_hashes
        ), "Original single-stream HTML/CSS changed while checks ran"
        await browser.close()
    print(
        json.dumps(
            {"checks": checks, "page_errors": errors, "original_hashes": initial_hashes}, indent=2
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
