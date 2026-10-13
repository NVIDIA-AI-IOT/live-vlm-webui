"""Tests for kiosk-related server configuration helpers."""

import pytest

from live_vlm_webui.server import is_loopback_host


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.1.2.3", "::1", "[::1]"])
def test_is_loopback_host_accepts_local_hosts(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize(
    "host", ["0.0.0.0", "192.168.1.20", "127.example.com", "127.0.0.999", "", None]
)
def test_is_loopback_host_rejects_non_local_hosts(host):
    assert not is_loopback_host(host)


@pytest.fixture
def kiosk_cli(monkeypatch, tmp_path):
    """Exercise the real parser without sockets, certificate writes, or VLM calls."""
    import sys
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from aiohttp import web

    from live_vlm_webui import server

    service = Mock()
    runner = Mock()
    certificate = Mock()
    monkeypatch.setattr(server, "VLMService", service)
    monkeypatch.setattr(server, "vlm_service", None)
    monkeypatch.setattr(server, "create_app", AsyncMock(return_value=web.Application()))
    monkeypatch.setattr(server, "generate_self_signed_cert", certificate)
    monkeypatch.setattr(server, "get_app_config_dir", lambda: tmp_path)
    monkeypatch.setattr(server.web, "run_app", runner)
    monkeypatch.setattr(server.signal, "signal", Mock())
    monkeypatch.setattr(
        server.subprocess,
        "run",
        Mock(return_value=SimpleNamespace(returncode=0, stdout="10.0.0.2")),
    )
    monkeypatch.setattr(server.VideoProcessorTrack, "process_every_n_frames", 30)
    monkeypatch.delenv("LIVE_VLM_DEFAULT_PROMPT", raising=False)
    monkeypatch.delenv("LIVE_VLM_MAX_TOKENS", raising=False)

    def run(*extra):
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "live-vlm-webui",
                "--model",
                "kiosk-model",
                "--api-base",
                "http://127.0.0.1:8012/v1",
                *extra,
            ],
        )
        server.main()
        return service.call_args.kwargs, runner.call_args.kwargs

    return run, certificate


def test_localhost_http_overrides_remote_binding_and_skips_certificates(kiosk_cli):
    run, certificate = kiosk_cli
    config, listener = run(
        "--localhost-http",
        "--host",
        "0.0.0.0",
        "--port",
        "8181",
        "--prompt",
        "Describe the kiosk.",
        "--max-tokens",
        "128",
        "--vlm-backend",
        "edgellm",
    )
    assert listener == {"host": "127.0.0.1", "port": 8181, "ssl_context": None}
    assert config["prompt"] == "Describe the kiosk."
    assert config["max_tokens"] == 128
    assert config["backend"] == "edgellm"
    certificate.assert_not_called()


def test_prompt_and_token_environment_defaults_allow_cli_override(kiosk_cli, monkeypatch):
    run, _ = kiosk_cli
    monkeypatch.setenv("LIVE_VLM_DEFAULT_PROMPT", "Configured environment prompt")
    monkeypatch.setenv("LIVE_VLM_MAX_TOKENS", "96")
    config, _ = run("--localhost-http")
    assert config["prompt"] == "Configured environment prompt"
    assert config["max_tokens"] == 96
    config, _ = run("--localhost-http", "--prompt", "CLI prompt", "--max-tokens", "32")
    assert config["prompt"] == "CLI prompt"
    assert config["max_tokens"] == 32


def test_invalid_token_environment_falls_back_to_upstream_default(kiosk_cli, monkeypatch):
    run, _ = kiosk_cli
    monkeypatch.setenv("LIVE_VLM_MAX_TOKENS", "invalid")
    config, _ = run("--localhost-http")
    assert config["max_tokens"] == 512


@pytest.mark.parametrize("tokens", ["0", "-1"])
def test_invalid_token_limit_rejected_before_server_start(kiosk_cli, tokens):
    run, certificate = kiosk_cli
    with pytest.raises(SystemExit) as error:
        run("--localhost-http", "--max-tokens", tokens)
    assert error.value.code == 2
    certificate.assert_not_called()


def test_no_ssl_does_not_change_explicit_non_kiosk_host(kiosk_cli):
    run, _ = kiosk_cli
    _, listener = run("--no-ssl", "--host", "0.0.0.0")
    assert listener["host"] == "0.0.0.0"
    assert listener["ssl_context"] is None


@pytest.mark.asyncio
async def test_websocket_server_config_supplies_kiosk_inference_defaults(monkeypatch):
    from types import SimpleNamespace

    from aiohttp.test_utils import TestClient, TestServer

    from live_vlm_webui import server

    monkeypatch.setattr(
        server,
        "vlm_service",
        SimpleNamespace(
            model="kiosk-cosmos",
            api_base="http://127.0.0.1:8012/v1",
            backend="edgellm",
            prompt="Describe visible actions.",
            max_tokens=48,
        ),
    )
    monkeypatch.setattr(server.VideoProcessorTrack, "process_every_n_frames", 25)
    app = await server.create_app(test_mode=True)
    async with (
        TestClient(TestServer(app)) as client,
        client.ws_connect("/ws?stream_id=kiosk-check") as socket,
    ):
        assert (await socket.receive_json())["type"] == "status"
        assert await socket.receive_json() == {
            "type": "server_config",
            "model": "kiosk-cosmos",
            "api_base": "http://127.0.0.1:8012/v1",
            "backend": "edgellm",
            "prompt": "Describe visible actions.",
            "max_tokens": 48,
            "process_every": 25,
            "temporal": app[server.TEMPORAL_DEFAULT_KEY]["config"].as_dict(),
        }
        # Preserve the fork's existing per-stream temporal configuration event.
        assert (await socket.receive_json())["type"] == "temporal_config"
