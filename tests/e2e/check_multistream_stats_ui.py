#!/usr/bin/env python3
"""Offline Chromium checks for multi-stream Local System Stats.

Run: python tests/e2e/check_multistream_stats_ui.py
All cameras, models and sockets are mocked; no serving process is started.
"""

import asyncio
import json
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from check_demo_launch_ui import MOCK_BROWSER, STATIC
from playwright.async_api import async_playwright

ORIGIN = "http://127.0.0.1:18768"
ARTIFACTS = Path(__file__).resolve().parents[2] / "artifacts/multistream-stats-ui"
STATS = {
    "hostname": "thor-demo",
    "board_name": "Jetson AGX Thor Developer Kit",
    "gpu_name": "NVIDIA Thor",
    "cpu_model": "ARM CPU",
    "gpu_percent": 73.5,
    "vram_used_gb": 8.0,
    "vram_total_gb": 128.0,
    "vram_percent": 6.25,
    "cpu_percent": 11.3,
    "ram_used_gb": 12.0,
    "ram_total_gb": 128.0,
    "ram_percent": 9.375,
    "history": {
        "gpu_util": [20, 45, 73.5],
        "vram_used": [7.5, 7.8, 8.0],
        "cpu_util": [3, 7, 11.3],
        "ram_used": [10, 11, 12],
    },
}
INSTRUMENT_BROWSER = r"""
window.__statsTestDocument = crypto.randomUUID();
window.__statsTestSent = [];
window.__statsTestStrokes = {};
const originalSend = WebSocket.prototype.send;
WebSocket.prototype.send = function(data) {
  window.__statsTestSent.push({url: this.url, data});
  return originalSend?.call(this, data);
};
const originalStroke = CanvasRenderingContext2D.prototype.stroke;
CanvasRenderingContext2D.prototype.stroke = function(...args) {
  const id = this.canvas.id;
  window.__statsTestStrokes[id] = (window.__statsTestStrokes[id] || 0) + 1;
  return originalStroke.apply(this, args);
};
window.__statsSockets = () => window.__sockets.filter(socket =>
  socket.url && new URL(socket.url, location.href).pathname === '/ws');
window.__emitStatsEvent = (payload, raw = false) => {
  const socket = window.__statsSockets().at(-1);
  const event = new MessageEvent('message', {data: raw ? payload : JSON.stringify(payload)});
  socket.dispatchEvent(event);
};
"""


async def main():
    checks, errors = [], []
    state = {"created": [], "deleted": []}
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-gpu"]
        )
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        await context.add_init_script(MOCK_BROWSER + INSTRUMENT_BROWSER)

        async def route(request):
            path = urlparse(request.request.url).path
            if path == "/models":
                return await request.fulfill(
                    json={
                        "models": [{"id": "stats-cosmos", "current": True}],
                        "configured_model": "stats-cosmos",
                        "api_base": "http://127.0.0.1:8012/v1",
                        "backend": "edgellm",
                    }
                )
            if path == "/api/segmentation/status":
                return await request.fulfill(
                    json={
                        "available": True,
                        "backend": "tensorrt",
                        "fixed_classes": ["person"],
                        "fixed_imgsz": 480,
                        "recommended_confidence": 0.1,
                    }
                )
            if path == "/api/usb-cameras":
                return await request.fulfill(
                    json={
                        "cameras": [
                            {"device_id": f"video{index * 2}", "label": f"Jetson USB {index + 1}"}
                            for index in range(4)
                        ]
                    }
                )
            if path == "/api/demo/videos":
                return await request.fulfill(json={"max_fps": 25, "videos": []})
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
            asset = STATIC / ("multi-camera.html" if path == "/multi-camera" else path.lstrip("/"))
            if asset.is_file():
                return await request.fulfill(
                    path=asset,
                    content_type=mimetypes.guess_type(str(asset))[0] or "application/octet-stream",
                )
            return await request.fulfill(status=404, body="Offline mock route not found")

        await context.route("**/*", route)
        page = await context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.goto(ORIGIN + "/multi-camera")
        await page.add_style_tag(
            content="*,*::before,*::after { transition: none !important; animation: none !important; }"
        )
        await page.wait_for_function("window.__statsSockets().length === 1")
        document_id = await page.evaluate("window.__statsTestDocument")
        assert await page.locator("#systemStatsCard").is_visible()
        assert (
            "Local System Stats"
            in await page.locator("#systemStatsCard .result-title").text_content()
        )
        assert await page.locator("#systemStatsCard .stat-card").count() == 4
        assert await page.locator("#systemStatsCard canvas.sparkline").count() == 4
        assert (
            await page.evaluate(
                "new URL(window.__statsSockets()[0].url).searchParams.get('stream_id')"
            )
            == "multi-stats"
        )
        assert not state["created"]
        assert await page.evaluate("window.__cameraRequests.length") == 0
        assert await page.evaluate("window.__statsTestSent") == []

        async def emit(stats):
            await page.evaluate(
                "stats => window.__emitStatsEvent({type:'gpu_stats', stats})", stats
            )

        async def gpu(value):
            await page.wait_for_function(
                "value => document.querySelector('#gpuUtil').textContent === value", arg=value
            )

        async def connected():
            await page.wait_for_function(
                "document.querySelector('#session-status').textContent === 'Cameras connected'"
            )

        await emit(STATS)
        await gpu("73.5%")
        assert await page.locator("#cpuUtil").text_content() == "11.3%"
        assert (await page.locator("#vramUsage").text_content()).replace(" ", "") == "8.0/128.0GB"
        assert (await page.locator("#ramUsage").text_content()).replace(" ", "") == "12.0/128.0GB"
        header = await page.locator("#systemInfoHeader").text_content()
        assert "thor-demo" in header and "Thor" in header
        for selector, value in (
            ("#gpuUtilBar", "73.5%"),
            ("#vramBar", "6.25%"),
            ("#cpuBar", "11.3%"),
            ("#ramBar", "9.375%"),
        ):
            assert await page.locator(selector).evaluate("element => element.style.width") == value
        colors = await page.locator("#systemStatsCard .stat-bar-fill").evaluate_all(
            "items => items.map(item => getComputedStyle(item).backgroundColor)"
        )
        assert colors == [
            "rgb(76, 175, 80)",
            "rgb(156, 39, 176)",
            "rgb(33, 150, 243)",
            "rgb(255, 152, 0)",
        ]
        await page.wait_for_function(
            "['gpuUtilSparkline','vramSparkline','cpuSparkline','ramSparkline'].every(id => window.__statsTestStrokes[id] > 0)"
        )
        checks.append(
            "idle page subscribes without camera permission or configuration writes and renders all four live metrics/history"
        )

        original_model = await page.locator("#model").input_value()
        for payload in (
            "{",
            "null",
            "[]",
            '{"type":"gpu_stats","stats":null}',
            '{"type":"gpu_stats","stats":"invalid"}',
            '{"type":"gpu_stats","stats":[]}',
            '{"type":"server_config","model":"do-not-use"}',
            '{"type":"vlm_response","text":"not a stats event"}',
        ):
            await page.evaluate("payload => window.__emitStatsEvent(payload, true)", payload)
        assert await page.locator("#gpuUtil").text_content() == "73.5%"
        assert await page.locator("#model").input_value() == original_model
        assert not errors, errors
        malicious = '<img src=x onerror="window.statsInjected=true">'
        await emit({**STATS, "hostname": malicious, "board_name": malicious, "gpu_name": malicious})
        await page.wait_for_function(
            "value => document.querySelector('#systemInfoHeader').textContent.includes(value)",
            arg=malicious,
        )
        assert await page.locator("#systemInfoHeader img, #systemInfoHeader script").count() == 0
        assert await page.evaluate("window.statsInjected") is None
        checks.append(
            "malformed/irrelevant messages are ignored and host metadata cannot create DOM markup"
        )

        unavailable = {
            **STATS,
            **{
                key: None
                for key in (
                    "gpu_percent",
                    "vram_used_gb",
                    "vram_total_gb",
                    "vram_percent",
                    "cpu_percent",
                    "ram_used_gb",
                    "ram_total_gb",
                    "ram_percent",
                )
            },
        }
        await emit(unavailable)
        await gpu("N/A")
        for selector in ("#vramUsage", "#cpuUtil", "#ramUsage"):
            assert await page.locator(selector).text_content() == "N/A"
        assert await page.locator("#systemStatsCard .stat-bar-fill").evaluate_all(
            "items => items.every(item => item.style.width === '0%')"
        )
        await emit(STATS)
        await gpu("73.5%")
        checks.append("unavailable hardware measurements show N/A without stale utilization bars")

        await page.locator("#four-cameras").click()
        await page.locator("#usb-cameras").click()
        await page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Jetson USB cameras ready')"
        )
        await page.locator("#start").click()
        await connected()
        assert len(state["created"]) == 1 and len(state["created"][0]["cameras"]) == 4
        assert await page.evaluate("window.__statsSockets().length") == 1
        await emit({**STATS, "gpu_percent": 81.0})
        await gpu("81.0%")
        await page.evaluate("window.__statsSockets()[0].close()")
        await gpu("N/A")
        assert "reconnect" in (await page.locator("#systemInfoHeader").text_content()).lower()
        await page.wait_for_function("window.__statsSockets().length === 2", timeout=4000)
        assert await page.evaluate("window.__statsTestDocument") == document_id
        assert len(state["created"]) == 1 and not state["deleted"]
        assert await page.locator("#session-status").text_content() == "Cameras connected"
        assert await page.evaluate("window.__cameraRequests.length") == 0
        await emit({**STATS, "gpu_percent": 52.0})
        await gpu("52.0%")
        checks.append(
            "stats reconnect independently during four streams without page reload, capture restart or session recreation"
        )

        ARTIFACTS.mkdir(parents=True, exist_ok=True)
        for theme in ("light", "dark"):
            for _ in range(3):
                if (await page.locator("#themeText").text_content()).lower() == theme:
                    break
                await page.locator("#themeToggle").click()
            assert (await page.locator("#themeText").text_content()).lower() == theme
            await page.wait_for_function(
                "document.querySelector('#systemStatsCard').getBoundingClientRect().height > 0"
            )
            stats_bounds = await page.locator("#systemStatsCard").bounding_box()
            assert (
                stats_bounds
                and stats_bounds["y"] >= 0
                and stats_bounds["y"] + stats_bounds["height"] <= 1081
            ), stats_bounds
            frames = await page.locator(".video-stage").evaluate_all(
                "items => items.map(item => {const r = item.getBoundingClientRect(); return {top:r.top,bottom:r.bottom,height:r.height};})"
            )
            assert len(frames) == 4 and all(
                frame["height"] >= 80 and frame["top"] >= 0 and frame["bottom"] <= stats_bounds["y"]
                for frame in frames
            ), frames
            columns = await page.locator("#systemStatsCard .stats-grid").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
            )
            assert columns == 4
            await page.screenshot(path=str(ARTIFACTS / f"desktop-four-{theme}.png"), full_page=True)
        checks.append(
            "four video previews and original four-column stats fit 1080p desktop in light and dark themes"
        )

        await page.locator("#presentation-toggle").click()
        assert await page.locator("#systemStatsCard").is_visible()
        await page.locator("#focus-stop").click()
        await page.wait_for_function(
            "document.querySelector('#session-status').textContent === 'Not started'"
        )
        assert len(state["deleted"]) == 1
        assert await page.evaluate("window.__statsSockets().at(-1).readyState") == 1
        await emit({**STATS, "gpu_percent": 6.0})
        await gpu("6.0%")
        assert await page.evaluate("window.__statsTestSent") == []
        checks.append(
            "Focus view keeps stats visible and stopping cameras leaves the read-only stats subscription live"
        )

        await page.set_viewport_size({"width": 390, "height": 844})
        await page.locator("#systemStatsCard").scroll_into_view_if_needed()
        for theme in ("light", "dark"):
            for _ in range(3):
                if (await page.locator("#themeText").text_content()).lower() == theme:
                    break
                await page.locator("#themeToggle").click()
            await page.locator("#systemStatsCard").scroll_into_view_if_needed()
            columns = await page.locator("#systemStatsCard .stats-grid").evaluate(
                "element => getComputedStyle(element).gridTemplateColumns.split(' ').length"
            )
            assert columns in (1, 2)
            bounds = await page.locator("#systemStatsCard").bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= 390, bounds
            assert await page.locator("#gpuUtil").text_content() == "6.0%"
            await page.locator("#systemStatsCard").screenshot(
                path=str(ARTIFACTS / f"mobile-stats-{theme}.png")
            )
        checks.append(
            "mobile stats use readable responsive columns without overflow and preserve values across themes"
        )
        assert not errors, errors
        await browser.close()
    print(json.dumps({"checks": checks, "page_errors": errors}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
