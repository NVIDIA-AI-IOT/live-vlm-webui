"""Integrity and offline behavior of the optional native25 retail installer."""

import hashlib
import importlib.util
from io import BytesIO
from pathlib import Path

import pytest


@pytest.fixture
def downloader(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "download_retail_25fps", scripts / "download_retail_25fps.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = b"small verified synthetic media fixture"
    monkeypatch.setattr(
        module,
        "CLIPS",
        {
            "8420772": {
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "source": "woman-looking-at-a-product-inside-a-grocery-store-8420772",
            }
        },
    )
    return module, data


def test_download_is_verified_and_offline_reuse_preserves_intel_notice(
    tmp_path, downloader, monkeypatch
):
    module, data = downloader
    intel = tmp_path / "NOTICE.txt"
    intel.write_text("Existing Intel CC BY-SA 4.0 notice")
    requests = []

    def download(request, timeout):
        requests.append(request.full_url)
        return BytesIO(data)

    monkeypatch.setattr(module, "urlopen", download)
    module.prepare(tmp_path)
    assert len(requests) == 1
    assert requests[0].startswith("https://videos.pexels.com/video-files/8420772/")
    module.prepare(tmp_path, offline=True)
    assert len(requests) == 1
    assert intel.read_text() == "Existing Intel CC BY-SA 4.0 notice"
    assert "Kampus Production" in (tmp_path / "NOTICE-PEXELS.txt").read_text()
    assert (tmp_path / "pexels-8420772-1080p.mp4").read_bytes() == data


def test_corrupt_download_is_never_published(tmp_path, downloader, monkeypatch):
    module, data = downloader
    monkeypatch.setattr(module, "urlopen", lambda *a, **kw: BytesIO(b"x" * len(data)))
    with pytest.raises(ValueError, match="Content verification failed"):
        module.prepare(tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_offline_missing_or_corrupt_file_fails_without_network(tmp_path, downloader, monkeypatch):
    module, data = downloader

    def network_forbidden(*a, **kw):
        pytest.fail("Offline verification must not access the network")

    monkeypatch.setattr(module, "urlopen", network_forbidden)
    assert module.main(["--offline", "--output-dir", str(tmp_path)]) == 1
    clip = tmp_path / "pexels-8420772-1080p.mp4"
    clip.write_bytes(b"x" * len(data))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        module.prepare(tmp_path, offline=True)
    assert clip.read_bytes() == b"x" * len(data)
