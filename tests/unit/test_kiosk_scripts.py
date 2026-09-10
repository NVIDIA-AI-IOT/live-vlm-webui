"""Kiosk install/launch behavior in an isolated desktop account directory."""

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts" / "kiosk"


@pytest.fixture
def kiosk_account(tmp_path):
    home = tmp_path / "demo user"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "systemctl.log"
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {shlex.quote(str(calls))}\n")
    systemctl.chmod(0o755)
    env = {**os.environ, "HOME": str(home), "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    return home, bin_dir, calls, env


def test_install_preserves_config_and_stages_user_services(kiosk_account):
    home, _, calls, env = kiosk_account
    if os.geteuid() == 0:
        pytest.skip("Installer intentionally refuses root")
    subprocess.run([str(SCRIPTS / "install_kiosk.sh")], env=env, check=True)
    config = home / ".config/live-vlm-webui/kiosk.env"
    assert config.stat().st_mode & 0o777 == 0o600
    config.write_text("KIOSK_MODEL=keep-my-model\n")
    subprocess.run([str(SCRIPTS / "install_kiosk.sh")], env=env, check=True)
    assert config.read_text() == "KIOSK_MODEL=keep-my-model\n"
    assert calls.read_text().splitlines() == ["--user", "daemon-reload"] * 2
    launcher = home / ".local/lib/live-vlm-webui-kiosk/start_kiosk_browser.sh"
    assert os.access(launcher, os.X_OK)
    desktop = home / ".config/autostart/live-vlm-kiosk.desktop"
    if shutil.which("desktop-file-validate"):
        subprocess.run(["desktop-file-validate", str(desktop)], check=True)


def test_session_imports_display_before_starting_browser(kiosk_account):
    _, _, calls, env = kiosk_account
    env.update(DISPLAY=":7", WAYLAND_DISPLAY="wayland-2")
    subprocess.run([str(SCRIPTS / "start_kiosk_browser.sh"), "--session"], env=env, check=True)
    args = calls.read_text().splitlines()
    assert args[:4] == ["--user", "import-environment", "DISPLAY", "WAYLAND_DISPLAY"]
    assert args[-3:] == ["--user", "start", "live-vlm-kiosk-browser.service"]


def test_launchers_use_config_and_wait_for_backend(kiosk_account, tmp_path):
    home, bin_dir, _, env = kiosk_account
    config_dir = home / ".config/live-vlm-webui"
    config_dir.mkdir(parents=True)
    args_file = tmp_path / "args"
    executable = bin_dir / "capture args"
    executable.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(args_file))}\n")
    executable.chmod(0o755)
    config = (SCRIPTS / "kiosk.env.example").read_text()
    config += (
        f"\nKIOSK_SERVER_BIN={shlex.quote(str(executable))}\n"
        f"KIOSK_BROWSER={shlex.quote(str(executable))}\n"
        "KIOSK_MODEL='demo/model:7b'\nKIOSK_PORT=18090\n"
        "KIOSK_PROMPT='Describe the display; do not execute this.'\n"
    )
    (config_dir / "kiosk.env").write_text(config)
    subprocess.run([str(SCRIPTS / "start_kiosk_server.sh")], env=env, check=True)
    args = args_file.read_text().splitlines()
    assert args[:3] == ["--localhost-http", "--port", "18090"]
    assert args[args.index("--model") + 1] == "demo/model:7b"
    assert args[args.index("--prompt") + 1] == "Describe the display; do not execute this."
    assert args[args.index("--max-tokens") + 1] == "128"

    probes = tmp_path / "probes"
    first_failure = tmp_path / "failed-once"
    curl = bin_dir / "curl"
    curl.write_text(
        f"#!/bin/sh\nprintf '%s\\n' \"$@\" >> {shlex.quote(str(probes))}\n"
        f"if [ ! -e {shlex.quote(str(first_failure))} ]; then\n"
        f"  touch {shlex.quote(str(first_failure))}\n  exit 1\nfi\n"
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    subprocess.run([str(SCRIPTS / "start_kiosk_browser.sh")], env=env, check=True, timeout=5)
    args = args_file.read_text().splitlines()
    assert args[-1] == "http://localhost:18090/?autostart=1"
    assert f"--user-data-dir={home}/live-vlm-webui-kiosk-profile" in args
    assert "--use-fake-ui-for-media-stream" in args
    assert "--use-fake-device-for-media-stream" not in args
    assert "--ignore-certificate-errors" not in args
    assert probes.read_text().count("http://localhost:18090/") == 2
    assert "http://localhost:11434/v1/models" in probes.read_text()
