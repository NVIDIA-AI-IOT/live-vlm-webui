"""Smoke probes clean up their own session and never disturb unrelated sessions."""

import importlib.util
import io
import json
import urllib.error
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/check_thor_demo.py"
spec = importlib.util.spec_from_file_location("check_thor_demo", SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.fixture
def api(monkeypatch):
    calls = []
    control = {"complete": True, "create_error": None, "delete_error": None, "failure": None}
    clock = [0.0]
    monkeypatch.setattr(probe.uuid, "uuid4", lambda: SimpleNamespace(hex="owned-fixture"))
    monkeypatch.setattr(probe.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        probe.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds)
    )

    def open_response(req, **kwargs):
        path = urllib.parse.urlsplit(req.full_url).path
        method = req.get_method()
        data = json.loads(req.data) if req.data else None
        calls.append((method, path, data))
        if path == "/models":
            result = {"models": [{"id": "cosmos-nano-int4"}]}
        elif path == "/api/segmentation/status":
            result = {"available": True, "backend": "tensorrt", "recommended_confidence": 0.05}
        elif path == "/api/demo/videos":
            result = {
                "videos": [
                    {"available": True, "preferred_for_realtime": True, "demo_id": f"store-{i}"}
                    for i in range(4)
                ]
            }
        elif method == "POST":
            if control["create_error"]:
                raise control["create_error"]
            control["payload"] = data
            result = {"session_id": data["session_id"]}
        elif method == "DELETE":
            if control["delete_error"]:
                raise control["delete_error"]
            result = {"stopped": True}
        else:
            assert method == "GET" and path.endswith("/portability-check-owned-fixture")
            result = {
                "cameras": [
                    {
                        "camera_id": camera["camera_id"],
                        "error": control["failure"],
                        "metrics": {
                            "last_request_succeeded": control["complete"],
                            "last_e2e_latency_ms": 1300,
                            "temporal_enabled": control["payload"]["temporal"]["enabled"],
                            "temporal_frame_count": 4,
                        },
                        "segmentation": {"metrics": {"frames_completed": 10, "fps": 25}},
                    }
                    for camera in control["payload"]["cameras"]
                ]
            }
        return io.BytesIO(json.dumps(result).encode())

    monkeypatch.setattr(probe.urllib.request, "urlopen", open_response)
    return calls, control


def http_error(code):
    return urllib.error.HTTPError("https://demo.invalid", code, "fixture error", {}, None)


def deletions(calls):
    return [path for method, path, _ in calls if method == "DELETE"]


def test_readiness_only_has_no_session_mutations(api):
    calls, _ = api
    result = probe.smoke("https://demo.invalid")
    assert result["inference_tested"] is False
    assert [method for method, _, _ in calls] == ["GET", "GET"]


@pytest.mark.parametrize("cameras", [2, 4])
def test_success_removes_only_created_session_and_uses_distinct_clips(api, cameras):
    calls, control = api
    result = probe.smoke("https://demo.invalid", cameras=cameras, temporal=True)
    assert result["inference_tested"] is True
    assert result["temporal"] is True
    assert len(result["cameras"]) == cameras
    payload = control["payload"]
    assert len({camera["demo_id"] for camera in payload["cameras"]}) == cameras
    assert payload["segmentation"]["confidence"] == 0.05
    assert deletions(calls) == ["/api/multi-camera/sessions/" + payload["session_id"]]
    assert not any(path.endswith("/sessions") and method == "DELETE" for method, path, _ in calls)


def test_inference_failure_still_removes_owned_session(api):
    calls, control = api
    control["failure"] = "CUDA fixture failure"
    with pytest.raises(RuntimeError, match="Camera inference failed"):
        probe.smoke("https://demo.invalid", cameras=2)
    assert deletions(calls) == ["/api/multi-camera/sessions/portability-check-owned-fixture"]


def test_timeout_still_removes_owned_session(api):
    calls, control = api
    control["complete"] = False
    with pytest.raises(RuntimeError, match="Timed out"):
        probe.smoke("https://demo.invalid", cameras=4, timeout=2)
    assert deletions(calls) == ["/api/multi-camera/sessions/portability-check-owned-fixture"]


def test_ambiguous_post_failure_attempts_cleanup(api):
    calls, control = api
    control["create_error"] = urllib.error.URLError("connection lost after sending POST")
    with pytest.raises(urllib.error.URLError):
        probe.smoke("https://demo.invalid", cameras=2)
    assert deletions(calls) == ["/api/multi-camera/sessions/portability-check-owned-fixture"]


def test_creation_conflict_never_deletes_existing_session(api):
    calls, control = api
    control["create_error"] = http_error(409)
    with pytest.raises(urllib.error.HTTPError):
        probe.smoke("https://demo.invalid", cameras=2)
    assert deletions(calls) == []


def test_already_cleaned_session_is_successful(api):
    calls, control = api
    control["delete_error"] = http_error(404)
    assert probe.smoke("https://demo.invalid", cameras=2)["inference_tested"] is True
    assert len(deletions(calls)) == 1


def test_cleanup_error_is_reported_with_session_id(api):
    _, control = api
    control["delete_error"] = http_error(500)
    with pytest.raises(
        RuntimeError, match="Could not remove smoke session portability-check-owned-fixture"
    ):
        probe.smoke("https://demo.invalid", cameras=2)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cameras": 1},
        {"cameras": True},
        {"timeout": 0},
        {"timeout": -1},
        {"timeout": float("nan")},
        {"timeout": float("inf")},
    ],
)
def test_invalid_parameters_do_not_send_requests(api, kwargs):
    calls, _ = api
    with pytest.raises(ValueError):
        probe.smoke("https://demo.invalid", **kwargs)
    assert calls == []
