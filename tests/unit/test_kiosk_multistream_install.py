"""Multi-stream uses upstream kiosk units; tests never install real services/models."""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
KIOSK = ROOT / "scripts/kiosk"
spec = importlib.util.spec_from_file_location(
    "kiosk_multistream", KIOSK / "configure_multistream.py"
)
kiosk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(kiosk)


@pytest.fixture
def settings():
    return {
        "ui_port": 18090,
        "vlm_api_base": "http://127.0.0.1:18012/v1",
        "vlm_model": "cosmos-nano-int4",
        "vlm_backend": "edgellm",
        "cosmos_port": 18012,
    }


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected live network, process, model, or service access")


@pytest.fixture(autouse=True)
def no_live_probes(monkeypatch):
    monkeypatch.setattr(kiosk, "local_addresses", list)
    monkeypatch.setattr(kiosk.urllib.request, "build_opener", forbidden)


@pytest.fixture
def account(tmp_path, settings):
    if os.geteuid() == 0:
        pytest.skip("The upstream kiosk installer intentionally refuses root")
    repo = tmp_path / "checkout with spaces"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copytree(KIOSK, scripts / "kiosk")
    home = tmp_path / "desktop user"
    home.mkdir()
    events = tmp_path / "events.jsonl"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    host = scripts / "prepare_thor_host.sh"
    host.write_text(
        "#!/bin/sh\n"
        'printf \'{"action":"host","browser":"%s"}\\n\' "$1" >> "$KIOSK_TEST_EVENTS"\n'
    )
    (scripts / "thor_demo.py").write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        "def read_config(path): return json.loads(path.read_text())\n"
        "if __name__ == '__main__':\n"
        "    args=sys.argv[1:]\n"
        "    with open(os.environ['KIOSK_TEST_EVENTS'], 'a') as f:\n"
        "        f.write(json.dumps({'action':'prepare','args':args})+'\\n')\n"
        "    if os.environ.get('KIOSK_TEST_PREP_FAIL'): sys.exit(7)\n"
        "    path=Path(args[args.index('--config')+1]); path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"    if not path.exists(): path.write_text({json.dumps(settings)!r})\n"
    )
    python = bin_dir / "python3.12"
    python.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args=sys.argv[1:]\n"
        "if args and args[0].endswith('configure_multistream.py') and ('check-start' in args or 'run' in args):\n"
        "    with open(os.environ['KIOSK_TEST_EVENTS'], 'a') as f:\n"
        "        f.write(json.dumps({'action':'check-start' if 'check-start' in args else 'run','args':args})+'\\n')\n"
        "    sys.exit(6 if os.environ.get('KIOSK_TEST_CONFLICT') else 0)\n"
        f"os.execv({sys.executable!r}, [{sys.executable!r}, *args])\n"
    )
    python.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['KIOSK_TEST_EVENTS'], 'a') as f:\n"
        "    f.write(json.dumps({'action':'systemctl','args':sys.argv[1:]})+'\\n')\n"
    )
    systemctl.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    for name in (
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
    ):
        env.pop(name, None)
    env["KIOSK_TEST_EVENTS"] = str(events)
    return SimpleNamespace(repo=repo, home=home, env=env, events=events)


def install(account, *arguments):
    return subprocess.run(
        ["bash", str(account.repo / "scripts/kiosk/install_kiosk.sh"), *arguments],
        env=account.env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def events_for(account):
    return [json.loads(line) for line in account.events.read_text().splitlines()]


def test_no_start_prepares_before_staging_without_probing_or_enabling_services(account):
    result = install(account, "--multi-stream", "--no-start")
    assert result.returncode == 0, result.stderr
    events = events_for(account)
    assert [event["action"] for event in events] == ["host", "prepare", "systemctl"]
    assert events[0]["browser"] == "--with-browser"
    assert events[-1]["args"] == ["--user", "daemon-reload"]
    config = account.home / ".config/live-vlm-webui/kiosk.env"
    content = config.read_text()
    assert "KIOSK_MODE=multi-stream" in content
    assert "KIOSK_SOURCE=auto" in content
    assert "KIOSK_CAMERAS=auto" in content
    assert "KIOSK_VLM_BACKEND=edgellm" in content
    assert config.stat().st_mode & 0o777 == 0o600
    assert (account.home / ".config/systemd/user/live-vlm-kiosk-cosmos.service").is_file()
    assert (account.home / ".local/lib/live-vlm-webui-kiosk/configure_multistream.py").is_file()


def test_failed_preparation_leaves_desktop_and_user_units_unmodified(account):
    account.env["KIOSK_TEST_PREP_FAIL"] = "1"
    result = install(account, "--multi-stream")
    assert result.returncode == 7
    assert not list(account.home.iterdir())
    assert [event["action"] for event in events_for(account)] == ["host", "prepare"]


def test_occupied_incompatible_port_fails_before_enabling_or_installing(account):
    account.env["KIOSK_TEST_CONFLICT"] = "1"
    result = install(account, "--multi-stream")
    assert result.returncode == 6
    assert not list(account.home.iterdir())
    assert [event["action"] for event in events_for(account)] == ["host", "prepare", "check-start"]


def test_reinstall_preserves_browser_display_and_unknown_settings(account):
    config = account.home / ".config/live-vlm-webui/kiosk.env"
    config.parent.mkdir(parents=True)
    original = "KIOSK_BROWSER='/custom browser'\nKIOSK_DISPLAY_MODE=window\nMY_SETTING=keep\n"
    config.write_text(original)
    for source, count in (("usb", "2"), ("store", "4")):
        result = install(
            account, "--multi-stream", "--source", source, "--cameras", count, "--no-start"
        )
        assert result.returncode == 0, result.stderr
    text = config.read_text()
    assert text.startswith(original)
    assert text.count(kiosk.BEGIN) == 1
    assert "KIOSK_SOURCE=store" in text
    assert "KIOSK_CAMERAS=4" in text
    assert text.count("MY_SETTING=keep") == 1


@pytest.mark.parametrize("local", [True, False])
def test_headless_start_enables_one_server_and_only_the_configured_local_backend(
    account, settings, local
):
    if not local:
        settings["vlm_api_base"] = "https://example.invalid/v1"
        settings["vlm_backend"] = "openai"
        (account.repo / "thor-demo.json").write_text(json.dumps(settings))
    result = install(account, "--multi-stream")
    assert result.returncode == 0, result.stderr
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    expected = [["--user", "daemon-reload"]]
    if local:
        expected += [
            ["--user", "enable", "live-vlm-kiosk-cosmos.service"],
            ["--user", "restart", "live-vlm-kiosk-cosmos.service"],
        ]
    else:
        expected += [["--user", "disable", "--now", "live-vlm-kiosk-cosmos.service"]]
    expected += [
        ["--user", "enable", "live-vlm-kiosk-server.service"],
        ["--user", "restart", "live-vlm-kiosk-server.service"],
    ]
    assert commands == expected
    assert "next desktop login" in result.stdout


def test_graphical_install_uses_upstream_session_browser_handoff(account):
    account.env["DISPLAY"] = ":7"
    result = install(account, "--multi-stream", "--source", "usb", "--cameras", "2")
    assert result.returncode == 0, result.stderr
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    assert commands[-2] == ["--user", "import-environment", "DISPLAY"]
    assert commands[-1] == ["--user", "restart", "live-vlm-kiosk-browser.service"]


@pytest.mark.parametrize("option", [[], ["--backend"]])
def test_installed_server_wrapper_delegates_to_the_shared_thor_profile(account, option):
    result = install(account, "--multi-stream", "--no-start")
    assert result.returncode == 0, result.stderr
    launcher = account.home / ".local/lib/live-vlm-webui-kiosk/start_kiosk_server.sh"
    subprocess.run([str(launcher), *option], env=account.env, check=True, timeout=10)
    event = events_for(account)[-1]
    assert event["action"] == "run"
    assert event["args"][-2:] == ["--kind", "backend" if option else "server"]
    assert str(account.repo) in event["args"]


@pytest.mark.parametrize("options", [["--source", "usb"], ["--multi-stream", "--cameras", "3"]])
def test_invalid_feature_options_fail_before_preparation(account, options):
    assert install(account, *options).returncode == 2
    assert not account.events.exists()
    assert not list(account.home.iterdir())


def test_env_merge_quotes_literal_model_and_path_without_evaluating_shell(tmp_path, settings):
    env = tmp_path / "kiosk.env"
    env.write_text("KIOSK_DISPLAY_MODE=window\n")
    settings["vlm_model"] = "literal-$(touch SHOULD_NOT_EXIST); model"
    repo = tmp_path / "repo with spaces"
    config = tmp_path / "config with spaces.json"
    kiosk.configure(env, repo, config, settings, "usb", "2")
    command = 'source "$1"; printf "%s\\n" "$KIOSK_MODEL" "$KIOSK_REPO"'
    result = subprocess.run(
        ["bash", "-c", command, "bash", str(env)],
        capture_output=True,
        text=True,
        check=True,
        cwd=tmp_path,
    )
    assert result.stdout.splitlines() == [settings["vlm_model"], str(repo)]
    assert not (tmp_path / "SHOULD_NOT_EXIST").exists()


def test_configured_server_identity_required_before_reusing_occupied_port(settings, monkeypatch):
    monkeypatch.setattr(kiosk, "port_open", lambda _: True)
    monkeypatch.setattr(kiosk, "http_json", lambda _: {"configured_model": "someone-else"})
    with pytest.raises(ValueError, match="occupied by another UI"):
        kiosk.service_state(settings, "server")


def test_matching_ui_can_be_ready_or_warming_without_starting_another(settings, monkeypatch):
    monkeypatch.setattr(kiosk, "port_open", lambda _: True)
    status = {"available": False}

    def response(url):
        if url.endswith("/models"):
            return {
                "configured_model": settings["vlm_model"],
                "backend": settings["vlm_backend"],
                "api_base": settings["vlm_api_base"] + "/",
            }
        return status

    monkeypatch.setattr(kiosk, "http_json", response)
    assert kiosk.service_state(settings, "server") == "warming"
    status["available"] = True
    assert kiosk.service_state(settings, "server") == "ready"


def test_backend_reuse_requires_exact_model(settings, monkeypatch):
    monkeypatch.setattr(kiosk, "port_open", lambda _: True)
    monkeypatch.setattr(kiosk, "http_json", lambda _: {"data": [{"id": "wrong-model"}]})
    with pytest.raises(ValueError, match="configured model"):
        kiosk.service_state(settings, "backend")
    monkeypatch.setattr(kiosk, "http_json", lambda _: {"data": [{"id": settings["vlm_model"]}]})
    assert kiosk.service_state(settings, "backend") == "ready"


@pytest.mark.parametrize("kind", ["server", "backend"])
def test_service_monitors_existing_instance_and_execs_only_after_port_is_free(
    settings, monkeypatch, tmp_path, kind
):
    states = iter(["ready", "warming", "free"])
    waits = []
    starts = []
    monkeypatch.setattr(kiosk, "service_state", lambda *_: next(states))
    monkeypatch.setattr(kiosk.time, "sleep", waits.append)
    monkeypatch.setattr(kiosk.os, "execv", lambda executable, command: starts.append(command))
    kiosk.run_service(tmp_path, tmp_path / "settings.json", settings, kind)
    assert waits == [5, 5]
    assert len(starts) == 1
    assert (
        starts[0][-1:] == ["cosmos"]
        if kind == "backend"
        else starts[0][-3:] == ["webui", "--skip-build", "--localhost-http"]
    )


def test_docker_check_uses_service_context_and_never_prompts(settings, monkeypatch):
    calls = []
    monkeypatch.setattr(kiosk, "service_state", lambda *_: "ready")

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=1 if command[0] == "docker" else 0)

    monkeypatch.setattr(kiosk.subprocess, "run", probe)
    kiosk.check_start(settings)
    assert [command for command, _ in calls] == [
        ["docker", "info"],
        ["sudo", "-n", "docker", "info"],
    ]
    assert all(kwargs["start_new_session"] and kwargs["timeout"] == 20 for _, kwargs in calls)


def test_password_only_docker_access_is_actionable_without_policy_changes(settings, monkeypatch):
    monkeypatch.setattr(kiosk, "service_state", lambda *_: "free")
    monkeypatch.setattr(
        kiosk.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1)
    )
    with pytest.raises(ValueError, match="without an interactive password"):
        kiosk.check_start(settings)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.0.2.10"])
def test_lan_only_https_server_blocks_second_segmentation_worker(
    settings, monkeypatch, tmp_path, host
):
    settings["ui_host"] = host
    monkeypatch.setattr(kiosk, "local_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setattr(kiosk, "port_open", lambda url: "192.0.2.10" in url)
    monkeypatch.setattr(kiosk, "http_json", lambda _url: None)
    monkeypatch.setattr(kiosk.os, "execv", forbidden)
    with pytest.raises(ValueError, match="occupied by another UI or an HTTPS server"):
        kiosk.run_service(tmp_path, tmp_path / "settings.json", settings, "server")


def test_compatible_lan_only_ui_is_preserved_but_cannot_serve_localhost_kiosk(
    settings, monkeypatch
):
    monkeypatch.setattr(kiosk, "local_addresses", lambda: ["192.0.2.10"])
    monkeypatch.setattr(kiosk, "port_open", lambda url: "192.0.2.10" in url)
    monkeypatch.setattr(
        kiosk,
        "http_json",
        lambda _: {
            "configured_model": settings["vlm_model"],
            "backend": settings["vlm_backend"],
            "api_base": settings["vlm_api_base"],
        },
    )
    with pytest.raises(ValueError, match="only on a network address"):
        kiosk.service_state(settings, "server")


def test_graphical_reconfigure_restarts_only_when_managed_preset_changes(account):
    account.env["DISPLAY"] = ":7"
    choices = [("usb", "2", "restart"), ("usb", "2", "start"), ("store", "4", "restart")]
    for source, cameras, operation in choices:
        result = install(account, "--multi-stream", "--source", source, "--cameras", cameras)
        assert result.returncode == 0, result.stderr
        commands = [
            event["args"] for event in events_for(account) if event["action"] == "systemctl"
        ]
        assert commands[-1] == ["--user", operation, "live-vlm-kiosk-browser.service"]
    assert not (account.home / ".config/live-vlm-webui/kiosk-profile.pending").exists()


def test_no_start_profile_change_defers_browser_restart_until_next_normal_install(account):
    account.env["DISPLAY"] = ":7"
    assert install(account, "--multi-stream", "--source", "usb", "--cameras", "2").returncode == 0
    account.events.write_text("")
    assert (
        install(
            account, "--multi-stream", "--source", "store", "--cameras", "4", "--no-start"
        ).returncode
        == 0
    )
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    assert commands == [["--user", "daemon-reload"]]
    assert (account.home / ".config/live-vlm-webui/kiosk-profile.pending").exists()
    assert install(account, "--multi-stream", "--source", "store", "--cameras", "4").returncode == 0
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    assert commands[-1] == ["--user", "restart", "live-vlm-kiosk-browser.service"]


def test_external_profile_no_start_never_disables_running_backend(account, settings):
    settings["vlm_backend"] = "openai"
    settings["vlm_api_base"] = "https://example.invalid/v1"
    (account.repo / "thor-demo.json").write_text(json.dumps(settings))
    assert install(account, "--multi-stream", "--no-start").returncode == 0
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    assert commands == [["--user", "daemon-reload"]]


def test_camera_change_refreshes_browser_without_restarting_model_or_ui(account):
    account.env["DISPLAY"] = ":7"
    assert install(account, "--multi-stream", "--source", "usb", "--cameras", "2").returncode == 0
    account.events.write_text("")
    assert install(account, "--multi-stream", "--source", "store", "--cameras", "4").returncode == 0
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    restarts = [command for command in commands if "restart" in command]
    assert restarts == [["--user", "restart", "live-vlm-kiosk-browser.service"]]


def test_free_new_ui_port_restarts_server_and_browser_but_reuses_backend(account):
    account.env["DISPLAY"] = ":7"
    assert install(account, "--multi-stream").returncode == 0
    account.events.write_text("")
    config_path = account.repo / "thor-demo.json"
    changed = json.loads(config_path.read_text())
    changed["ui_port"] = 18091
    config_path.write_text(json.dumps(changed))
    assert install(account, "--multi-stream").returncode == 0
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    restarts = [command for command in commands if "restart" in command]
    assert restarts == [
        ["--user", "restart", "live-vlm-kiosk-server.service"],
        ["--user", "restart", "live-vlm-kiosk-browser.service"],
    ]


def test_new_backend_port_restarts_only_the_named_kiosk_units(account):
    assert install(account, "--multi-stream").returncode == 0
    account.events.write_text("")
    config_path = account.repo / "thor-demo.json"
    changed = json.loads(config_path.read_text())
    changed["cosmos_port"] = 18013
    changed["vlm_api_base"] = "http://127.0.0.1:18013/v1"
    config_path.write_text(json.dumps(changed))
    assert install(account, "--multi-stream").returncode == 0
    commands = [event["args"] for event in events_for(account) if event["action"] == "systemctl"]
    restarts = [command for command in commands if "restart" in command]
    assert restarts == [
        ["--user", "restart", "live-vlm-kiosk-cosmos.service"],
        ["--user", "restart", "live-vlm-kiosk-server.service"],
    ]


@pytest.mark.parametrize("preparations", [1, 3])
def test_no_start_keeps_original_identity_until_normal_restart(account, preparations):
    assert install(account, "--multi-stream").returncode == 0
    config = account.repo / "thor-demo.json"
    original = json.loads(config.read_text())
    pending = account.home / ".config/live-vlm-webui/kiosk-server.previous.json"
    for number in range(preparations):
        config.write_text(json.dumps(original | {"vlm_model": f"replacement-{number}"}))
        assert install(account, "--multi-stream", "--no-start").returncode == 0
        assert json.loads(pending.read_text())["previous"]["KIOSK_MODEL"] == original["vlm_model"]
    assert install(account, "--multi-stream").returncode == 0
    assert not pending.exists()


def test_failed_managed_restart_keeps_pending_identity(account):
    assert install(account, "--multi-stream").returncode == 0
    config = account.repo / "thor-demo.json"
    original = json.loads(config.read_text())
    config.write_text(json.dumps(original | {"vlm_model": "replacement"}))
    assert install(account, "--multi-stream", "--no-start").returncode == 0
    pending = account.home / ".config/live-vlm-webui/kiosk-server.previous.json"
    previous = pending.read_bytes()
    systemctl = Path(account.env["PATH"].split(os.pathsep)[0]) / "systemctl"
    systemctl.write_text(
        systemctl.read_text()
        + "\nif sys.argv[1:] == ['--user', 'restart', 'live-vlm-kiosk-server.service']: sys.exit(5)\n"
    )
    assert install(account, "--multi-stream").returncode == 5
    assert pending.read_bytes() == previous
