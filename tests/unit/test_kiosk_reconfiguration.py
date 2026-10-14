"""Exercise the real kiosk preflight when its managed VLM configuration changes."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "kiosk_reconfiguration", ROOT / "scripts/kiosk/configure_multistream.py"
)
kiosk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kiosk)


@pytest.fixture
def installed(tmp_path, monkeypatch):
    repo = ROOT
    config = tmp_path / "thor settings.json"
    env = tmp_path / "kiosk.env"
    old = {
        "ui_port": 18090,
        "vlm_api_base": "http://127.0.0.1:18012/v1",
        "vlm_model": "cosmos-nano-int4",
        "vlm_backend": "edgellm",
        "cosmos_port": 18012,
    }
    kiosk.configure(env, repo, config, old, "auto", "auto")
    process = tmp_path / "proc/456"
    process.mkdir(parents=True)
    command = [
        sys.executable,
        str(repo / "scripts/thor_demo.py"),
        "--config",
        str(config),
        "webui",
        "--skip-build",
        "--localhost-http",
    ]
    (process / "cmdline").write_bytes(b"\0".join(part.encode() for part in command) + b"\0")
    monkeypatch.setattr(kiosk, "PROC", process.parent)
    monkeypatch.setattr(kiosk, "local_addresses", list)
    monkeypatch.setattr(kiosk, "port_open", lambda url: url == "http://localhost:18090")
    identity = {
        "configured_model": old["vlm_model"],
        "backend": old["vlm_backend"],
        "api_base": old["vlm_api_base"],
    }
    monkeypatch.setattr(
        kiosk,
        "http_json",
        lambda url: identity if url.endswith("/models") else {"available": True},
    )
    state = {"active": True, "docker": True}
    calls = []

    def probe(command, **kwargs):
        calls.append(command)
        if command[:3] == ["systemctl", "--user", "show"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"ActiveState={'active' if state['active'] else 'inactive'}\nMainPID=456\n",
            )
        if command in (["docker", "info"], ["sudo", "-n", "docker", "info"]):
            return SimpleNamespace(returncode=0 if state["docker"] else 1)
        pytest.fail(f"Unexpected process: {command}")

    monkeypatch.setattr(kiosk.subprocess, "run", probe)
    return SimpleNamespace(
        repo=repo,
        config=config,
        env=env,
        old=old,
        process=process,
        identity=identity,
        state=state,
        calls=calls,
    )


def preflight(installed, monkeypatch, changed, *, with_env=True):
    installed.config.write_text(json.dumps(changed))
    arguments = [
        "configure_multistream.py",
        "--repo",
        str(installed.repo),
        "--config",
        str(installed.config),
        "check-start",
    ]
    if with_env:
        arguments += ["--env", str(installed.env)]
    monkeypatch.setattr(sys, "argv", arguments)
    return kiosk.main()


@pytest.mark.parametrize(
    "change",
    [
        {"cosmos_port": 18013, "vlm_api_base": "http://127.0.0.1:18013/v1"},
        {
            "vlm_api_base": "http://192.0.2.2:8000/v1",
            "vlm_backend": "openai",
            "vlm_model": "new-vlm",
        },
        {"vlm_model": "another-model"},
    ],
)
def test_actual_check_start_allows_previous_ui_owned_by_kiosk(installed, monkeypatch, change):
    original = installed.env.read_bytes()
    assert preflight(installed, monkeypatch, installed.old | change) == 0
    assert installed.env.read_bytes() == original
    assert installed.calls[-1] == ["docker", "info"]
    assert all("start" not in command and "restart" not in command for command in installed.calls)


@pytest.mark.parametrize(
    "case",
    [
        "reuse-monitor",
        "foreign-ui",
        "inactive-unit",
        "different-checkout",
        "missing-environment",
        "different-recorded-port",
        "different-owner",
    ],
)
def test_actual_check_start_preserves_unmanaged_servers(installed, monkeypatch, case, capsys):
    if case == "reuse-monitor":
        (installed.process / "cmdline").write_bytes(
            f"{sys.executable}\0{ROOT}/scripts/kiosk/configure_multistream.py\0run\0--kind\0server\0".encode()
        )
    elif case == "foreign-ui":
        installed.identity["configured_model"] = "unrelated-vlm"
    elif case == "inactive-unit":
        installed.state["active"] = False
    elif case == "different-checkout":
        path = installed.process / "cmdline"
        path.write_bytes(path.read_bytes().replace(str(ROOT).encode(), b"/another/checkout"))
    elif case == "different-recorded-port":
        installed.env.write_text(
            installed.env.read_text().replace("KIOSK_PORT=18090", "KIOSK_PORT=18091")
        )
    elif case == "different-owner":
        monkeypatch.setattr(kiosk.os, "getuid", lambda: installed.process.stat().st_uid + 1)
    with pytest.raises(SystemExit) as error:
        preflight(
            installed,
            monkeypatch,
            installed.old | {"vlm_api_base": "http://127.0.0.1:18013/v1", "cosmos_port": 18013},
            with_env=case != "missing-environment",
        )
    assert error.value.code == 1
    assert "occupied by another UI" in capsys.readouterr().err
    assert not any("docker" in command for command in installed.calls)


def test_reconfiguration_still_checks_noninteractive_docker(installed, monkeypatch, capsys):
    installed.state["docker"] = False
    with pytest.raises(SystemExit) as error:
        preflight(installed, monkeypatch, installed.old | {"vlm_model": "new-model"})
    assert error.value.code == 1
    assert "without an interactive password" in capsys.readouterr().err


@pytest.mark.parametrize("preparations", [1, 3])
def test_actual_preflight_after_deferred_configuration_keeps_running_identity(
    installed, monkeypatch, preparations
):
    pending = kiosk.pending_server_path(installed.env)
    assert not pending.exists()
    for number in range(preparations):
        changed = installed.old | {
            "cosmos_port": 18013 + number,
            "vlm_api_base": f"http://127.0.0.1:{18013 + number}/v1",
        }
        # This is the configure phase of --no-start; the old process keeps running.
        kiosk.configure(installed.env, installed.repo, installed.config, changed, "auto", "auto")
        assert (
            kiosk.read_pending_server(installed.env)["KIOSK_API_BASE"]
            == installed.old["vlm_api_base"]
        )
        assert f"KIOSK_API_BASE={changed['vlm_api_base']}" in installed.env.read_text()
    assert pending.stat().st_mode & 0o777 == 0o600
    assert preflight(installed, monkeypatch, changed) == 0
    assert pending.exists()  # Only the successful managed restart clears the handoff.


@pytest.mark.parametrize("case", ["foreign-ui", "reuse-monitor"])
def test_pending_identity_does_not_authorize_unmanaged_server(installed, monkeypatch, case):
    changed = installed.old | {"vlm_model": "new-model"}
    kiosk.configure(installed.env, installed.repo, installed.config, changed, "auto", "auto")
    if case == "foreign-ui":
        installed.identity["configured_model"] = "unrelated"
    else:
        (installed.process / "cmdline").write_bytes(b"python\0configure_multistream.py\0run\0")
    with pytest.raises(SystemExit) as error:
        preflight(installed, monkeypatch, changed)
    assert error.value.code == 1


def test_bad_pending_identity_preserves_configuration(installed):
    pending = kiosk.pending_server_path(installed.env)
    pending.write_text('{"unrelated":true}')
    original = installed.env.read_bytes()
    with pytest.raises(ValueError, match="Unrecognized pending"):
        kiosk.configure(
            installed.env,
            installed.repo,
            installed.config,
            installed.old | {"vlm_model": "new-model"},
            "auto",
            "auto",
        )
    assert installed.env.read_bytes() == original
    assert pending.read_text() == '{"unrelated":true}'


def test_camera_only_choice_does_not_record_server_handoff(installed):
    kiosk.configure(installed.env, installed.repo, installed.config, installed.old, "usb", "2")
    assert not kiosk.pending_server_path(installed.env).exists()


@pytest.mark.parametrize(
    ("flags", "recognized"),
    [
        (["--localhost-http"], True),
        (["--no-ssl"], True),
        ([], False),
        (["--no-ssl", "--localhost-http"], False),
        (["--no-ssl", "--host", "0.0.0.0"], False),
        (["--unknown-transport"], False),
    ],
)
def test_managed_identity_accepts_only_exact_local_or_lan_launch(installed, flags, recognized):
    path = installed.process / "cmdline"
    prefix = path.read_bytes().rstrip(b"\0").split(b"\0")[:-1]
    path.write_bytes(b"\0".join([*prefix, *(flag.encode() for flag in flags)]) + b"\0")
    assert kiosk.managed_server_running(installed.repo, installed.config) is recognized
