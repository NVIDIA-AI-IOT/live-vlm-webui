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
