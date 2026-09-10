"""Thinking controls: real HTTP/WebSocket/WebRTC with a synthetic camera and backend.

Run separately from unit tests (the synchronous Playwright fixture owns an event loop).
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

import pytest
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]
PROMPT = "Describe the test camera."
RESPONSE = "The camera shows a green image."


@pytest.fixture
def reasoning_server(tmp_path, request):
    requests = []

    class Backend(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"data": [{"id": "reasoning/test-model"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            body = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": RESPONSE}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    api_base = f"http://127.0.0.1:{backend.server_port}/v1"
    url = f"http://localhost:{port}/"
    log = (tmp_path / "server.log").open("w+")
    command = [
        sys.executable,
        "-m",
        "live_vlm_webui.server",
        "--no-ssl",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--api-base",
        api_base,
        "--model",
        "reasoning/test-model",
        "--prompt",
        PROMPT,
        "--process-every",
        "1",
    ]
    if getattr(request, "param", None) is not None:
        command.extend(["--reasoning-effort", request.param])
    env = {key: value for key, value in os.environ.items() if not key.startswith("LIVE_VLM_")}
    env["PYTHONPATH"] = str(ROOT / "src")
    processes = []

    def launch():
        proc = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        processes.append(proc)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None:
            try:
                with urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        return proc
            except URLError:
                time.sleep(0.1)
        log.seek(0)
        pytest.fail(f"Server did not start:\n{log.read()}")

    try:
        yield url, api_base, requests, launch(), launch
    finally:
        for proc in processes:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        backend.shutdown()
        backend.server_close()
        thread.join()
        log.close()


@pytest.fixture
def reasoning_page(playwright):
    browser = playwright.chromium.launch(
        args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
    )
    context = browser.new_context()
    page = context.new_page()
    # Decorative CDN icons are unrelated to the reasoning control.
    page.add_init_script("window.lucide = {createIcons() {}};")
    page.route("https://**/*", lambda route: route.abort())
    try:
        yield page
    finally:
        browser.close()


@pytest.mark.parametrize("reasoning_server", ["none"], indirect=True)
def test_thinking_control_updates_inference_requests(reasoning_page, reasoning_server):
    page = reasoning_page
    url, api_base, requests, _, _ = reasoning_server
    service = {"name": "Test backend", "url": api_base}
    page.context.route(
        "**/detect-services",
        lambda route: route.fulfill(json={"default": service, "detected": [service]}),
    )
    page.goto(url)
    expect(page.locator("#modelSelect")).to_have_value("reasoning/test-model")
    expect(page.get_by_label("Thinking", exact=True)).to_be_enabled()
    page.wait_for_function("selectedCameraId !== null")
    page.locator("#maxTokens").fill("128")
    page.locator("#maxTokens").blur()
    page.locator("#bigStartBtn").click()
    expect(page.locator("#resultTextContent")).to_have_text(RESPONSE, timeout=30000)
    control = page.get_by_label("Thinking", exact=True)
    expect(control).to_have_value("none")
    assert requests[-1]["reasoning_effort"] == "none"

    for effort in ["low", "medium", "high", "", "none"]:
        first_new_request = len(requests)
        control.select_option(effort)
        expect(control).to_be_enabled()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            matching = [
                body
                for body in requests[first_new_request:]
                if body.get("reasoning_effort") == (effort or None)
            ]
            if matching:
                break
            page.wait_for_timeout(100)
        else:
            pytest.fail(f"No inference request used thinking setting {effort!r}")
        if effort == "":
            assert "reasoning_effort" not in matching[-1]

    # Reject malformed settings without changing the current session's configuration.
    page.evaluate(
        "websocket.send(JSON.stringify({type: 'update_reasoning', reasoning_effort: 'invalid'}))"
    )
    expect(page.locator("#connectionStatus")).to_contain_text("Unsupported thinking setting")
    expect(control).to_have_value("none")

    # A fresh tab starts from the server default, independently of another tab's setting.
    control.select_option("high")
    expect(control).to_be_enabled()
    other_page = page.context.new_page()
    try:
        other_page.goto(url)
        expect(other_page.get_by_label("Thinking", exact=True)).to_be_enabled()
        expect(other_page.get_by_label("Thinking", exact=True)).to_have_value("none")
        expect(control).to_have_value("high")
    finally:
        other_page.close()
