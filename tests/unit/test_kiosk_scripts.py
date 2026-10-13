"""Kiosk install/launch behavior in an isolated desktop account directory."""

import os
import shlex
import shutil
import subprocess
import time
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


def test_installed_desktop_entry_launches_with_gio(kiosk_account):
    """Exercise GNOME's parser; desktop-file-validate misses invalid Exec escapes."""
    home, _, calls, env = kiosk_account
    if os.geteuid() == 0:
        pytest.skip("Installer intentionally refuses root")
    python = "/usr/bin/python3"
    if (
        not Path(python).exists()
        or subprocess.run(
            [python, "-c", "from gi.repository import Gio"], capture_output=True, check=False
        ).returncode
    ):
        pytest.skip("Requires the system Python with PyGObject")
    subprocess.run([str(SCRIPTS / "install_kiosk.sh")], env=env, check=True)
    calls.write_text("")
    desktop = home / ".config/autostart/live-vlm-kiosk.desktop"
    subprocess.run(
        [
            python,
            "-c",
            (
                "from gi.repository import Gio; import sys; "
                "app = Gio.DesktopAppInfo.new_from_filename(sys.argv[1]); "
                "assert app is not None, 'Desktop entry rejected by GIO'; "
                "assert app.launch([], None)"
            ),
            str(desktop),
        ],
        env=env,
        check=True,
        timeout=10,
    )
    deadline = time.monotonic() + 5
    expected = ["--user", "start", "live-vlm-kiosk-browser.service"]
    while time.monotonic() < deadline:
        if calls.read_text().splitlines()[-3:] == expected:
            break
        time.sleep(0.05)
    assert calls.read_text().splitlines()[-3:] == expected


@pytest.mark.parametrize("mode", [None, "kiosk", "full", "window"])
def test_launchers_use_config_and_wait_for_backend(kiosk_account, tmp_path, mode):
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
    if mode is None:
        config = config.replace("KIOSK_DISPLAY_MODE=kiosk\n", "")
    else:
        config += f"KIOSK_DISPLAY_MODE={mode}\n"
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
    if mode == "window":
        assert args[-1] == "--app=http://localhost:18090/?autostart=1"
        assert "--kiosk" not in args
        assert "--window-size=960,1080" in args
    else:
        assert args[-1] == "http://localhost:18090/?autostart=1"
        assert "--kiosk" in args
    assert f"--user-data-dir={home}/live-vlm-webui-kiosk-profile" in args
    assert "--use-fake-ui-for-media-stream" in args
    assert "--use-fake-device-for-media-stream" not in args
    assert "--ignore-certificate-errors" not in args
    assert probes.read_text().count("http://localhost:18090/") == 2
    assert "http://localhost:11434/v1/models" in probes.read_text()


def test_invalid_display_mode_fails_before_starting_browser(kiosk_account):
    home, _, _, env = kiosk_account
    config_dir = home / ".config/live-vlm-webui"
    config_dir.mkdir(parents=True)
    (config_dir / "kiosk.env").write_text(
        (SCRIPTS / "kiosk.env.example").read_text() + "\nKIOSK_DISPLAY_MODE=typo\n"
    )
    result = subprocess.run(
        [str(SCRIPTS / "start_kiosk_browser.sh")],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "KIOSK_DISPLAY_MODE must be kiosk, full, or window" in result.stderr


def test_half_workarea_accounts_for_panel_and_window_decorations():
    import runpy

    geometry = runpy.run_path(str(SCRIPTS / "configure_kiosk_window.py"))["half_workarea"]
    desktops = "0  * DG: 1920x1200 VP: 0,0 WA: 72,30 1848x1170 Desktop 1\n"
    assert geometry(desktops, [1, 1, 37, 1]) == (72, 30, 922, 1132)


@pytest.mark.parametrize("source,count", [("auto", "auto"), ("usb", "2"), ("store", "4")])
def test_multi_stream_browser_waits_for_masks_and_uses_kiosk_preset(
    kiosk_account, tmp_path, source, count
):
    home, bin_dir, _, env = kiosk_account
    config_dir = home / ".config/live-vlm-webui"
    config_dir.mkdir(parents=True)
    args_file = tmp_path / "browser-args"
    browser = bin_dir / "capture-browser"
    browser.write_text(f"#!/bin/sh\nprintf '%s\\n' \"$@\" > {shlex.quote(str(args_file))}\n")
    browser.chmod(0o755)
    (config_dir / "kiosk.env").write_text(
        (SCRIPTS / "kiosk.env.example").read_text()
        + f"\nKIOSK_BROWSER={shlex.quote(str(browser))}\nKIOSK_MODE=multi-stream\n"
        + f"KIOSK_SOURCE={source}\nKIOSK_CAMERAS={count}\nKIOSK_PORT=18090\n"
    )
    checked = tmp_path / "segmentation-checked"
    curl = bin_dir / "curl"
    curl.write_text(
        "#!/bin/sh\n"
        'case "$*" in *api/segmentation/status*)\n'
        f"if [ ! -e {shlex.quote(str(checked))} ]; then\n"
        f"touch {shlex.quote(str(checked))}; printf '%s' '{{\"available\":false}}'\n"
        "else printf '%s' '{\"available\":true}'; fi;; esac\n"
    )
    curl.chmod(0o755)
    sleep = bin_dir / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    subprocess.run([str(SCRIPTS / "start_kiosk_browser.sh")], env=env, check=True, timeout=5)
    args = args_file.read_text().splitlines()
    assert checked.is_file()
    assert args[-1] == (
        "http://localhost:18090/multi-camera?kiosk=1&demo="
        + source
        + "&cameras="
        + count
        + "&autostart=1&temporal=1"
    )
    assert "--use-fake-ui-for-media-stream" in args
    assert "--use-fake-device-for-media-stream" not in args
    assert "--no-sandbox" not in args


@pytest.mark.parametrize("setting", ["KIOSK_MODE=bad", "KIOSK_CAMERAS=3", "KIOSK_SOURCE=rtsp"])
def test_invalid_multistream_preset_starts_no_browser(kiosk_account, setting):
    home, bin_dir, _, env = kiosk_account
    config_dir = home / ".config/live-vlm-webui"
    config_dir.mkdir(parents=True)
    browser = bin_dir / "browser"
    browser.write_text("#!/bin/sh\nexit 99\n")
    browser.chmod(0o755)
    (config_dir / "kiosk.env").write_text(
        (SCRIPTS / "kiosk.env.example").read_text()
        + f"\nKIOSK_BROWSER={shlex.quote(str(browser))}\nKIOSK_MODE=multi-stream\n{setting}\n"
    )
    result = subprocess.run(
        [str(SCRIPTS / "start_kiosk_browser.sh")],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert result.returncode == 1
    assert setting.split("=")[0] in result.stderr
