"""Kiosk browser regression tests: real HTTP/WebRTC, synthetic camera and VLM.

Run: pytest tests/e2e/test_kiosk.py -v
Requires: pip install pytest-playwright && playwright install chromium
No GPU, physical webcam, model download, or external CDN connection is needed.
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
PROMPT = "Describe the kiosk test camera."
RESPONSE = 'Kiosk camera <img src=x onerror="window.injected=true"> works.'


@pytest.fixture
def kiosk_server(tmp_path):
    requests = []

    class Backend(BaseHTTPRequestHandler):
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
        "--localhost-http",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--api-base",
        api_base,
        "--model",
        "kiosk/test-model",
        "--prompt",
        PROMPT,
        "--process-every",
        "1",
        "--max-tokens",
        "128",
    ]
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
def kiosk_page(playwright):
    browser = playwright.chromium.launch(
        args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
    )
    page = browser.new_page()
    # Exercise the offline fallback rather than mocking application functions.
    page.route("https://**/*", lambda route: route.abort())
    try:
        yield page
    finally:
        browser.close()


def test_autostart_defaults_stop_and_server_recovery(kiosk_page, kiosk_server):
    page = kiosk_page
    url, api_base, requests, proc, launch = kiosk_server
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(f"{url}?autostart=1")
    expect(page.locator("#resultTextContent")).to_have_text(RESPONSE, timeout=30000)
    expect(page.locator("#modelSelect")).to_have_value("kiosk/test-model")
    expect(page.locator("#apiBaseUrl")).to_have_value(api_base)
    expect(page.locator("#promptText")).to_have_value(PROMPT)
    expect(page.locator("#maxTokens")).to_have_value("128")
    assert requests[0]["model"] == "kiosk/test-model"
    assert requests[0]["max_tokens"] == 128
    assert requests[0]["messages"][0]["content"][0]["text"] == PROMPT
    assert page.evaluate("window.injected") is None
    assert page.locator("#resultTextContent img").count() == 0

    page.locator("#smallStopBtn").click()
    page.wait_for_timeout(2500)
    assert page.evaluate("isAnalysisRunning") is False
    first_session = page.evaluate("sessionId")
    proc.terminate()
    proc.wait(timeout=10)
    launch()
    page.wait_for_function("old => sessionId !== old", arg=first_session, timeout=30000)
    expect(page.locator("#resultTextContent")).to_have_text(RESPONSE, timeout=30000)
    assert not errors


def test_regular_url_does_not_autostart(kiosk_page, kiosk_server):
    page = kiosk_page
    url, _, _, _, _ = kiosk_server
    page.goto(url)
    page.wait_for_function("websocket && websocket.readyState === WebSocket.OPEN")
    page.wait_for_timeout(1000)
    assert page.evaluate("isAnalysisRunning") is False
    assert page.evaluate("peerConnection") is None
