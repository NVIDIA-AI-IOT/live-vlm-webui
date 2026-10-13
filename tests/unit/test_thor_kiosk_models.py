"""Default setup resolves model access without sending users to manual commands."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/thor_demo.py"
sys.path.insert(0, str(SCRIPT.parent))
try:
    spec = importlib.util.spec_from_file_location("thor_kiosk_models", SCRIPT)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
finally:
    sys.path.pop(0)


def forbidden(*args, **kwargs):
    pytest.fail("Turnkey setup requested manual input or an unexpected process")


@pytest.fixture
def installer(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    monkeypatch.setattr(setup.subprocess, "Popen", forbidden)
    return setup.KioskAssets(
        SimpleNamespace(config=tmp_path / "thor-demo.json", cosmos_source="auto")
    )


def test_auto_uses_accessible_checkpoint_without_login_or_public_conversion(installer, monkeypatch):
    commands = []
    monkeypatch.setattr(installer, "probe", lambda command: commands.append(command) or True)
    monkeypatch.setattr(setup.shutil, "disk_usage", forbidden)
    installer.verify_download_access(Path("/fixture/hf"))
    assert installer.args.cosmos_source == "checkpoint"
    assert commands == [
        [
            Path("/fixture/hf"),
            "download",
            setup.cosmos.CHECKPOINT_REPO,
            "--revision",
            setup.cosmos.CHECKPOINT_REVISION,
            "model.safetensors",
            "--dry-run",
        ]
    ]


@pytest.mark.parametrize("interactive", [False, True])
def test_auto_prepares_public_source_without_login_or_technical_menu(
    installer, monkeypatch, capsys, interactive
):
    commands = []
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: interactive)
    monkeypatch.setattr(setup.sys.stdout, "isatty", lambda: interactive)
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))

    def probe(command):
        commands.append(command)
        return command[2] == setup.cosmos.SOURCE_MODEL

    monkeypatch.setattr(installer, "probe", probe)
    installer.verify_download_access(Path("/fixture/hf"))
    assert installer.args.cosmos_source == "public"
    assert [command[2] for command in commands] == [
        setup.cosmos.CHECKPOINT_REPO,
        setup.cosmos.SOURCE_MODEL,
    ]
    assert commands[-1][4:] == [
        setup.cosmos.SOURCE_REVISION,
        "transformer/diffusion_pytorch_model-00001-of-00007.safetensors",
        "--dry-run",
    ]
    output = capsys.readouterr().out
    assert f"https://huggingface.co/{setup.cosmos.SOURCE_MODEL}" in output
    assert "Choose" not in output and "Sign in" not in output


def test_auto_public_fallback_checks_disk_before_large_download_probe(installer, monkeypatch):
    commands = []
    monkeypatch.setattr(installer, "probe", lambda command: commands.append(command) or False)
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=99 * 1024**3))
    with pytest.raises(setup.KioskSetupError, match="100 GB"):
        installer.verify_download_access(Path("/fixture/hf"))
    assert installer.args.cosmos_source == "auto"
    assert len(commands) == 1 and commands[0][2] == setup.cosmos.CHECKPOINT_REPO


def test_unavailable_public_source_reports_retry_without_requiring_login(
    installer, monkeypatch, capsys
):
    secret = "test_secret_must_not_be_printed"
    installer.env["HF_TOKEN"] = secret
    monkeypatch.setattr(installer, "probe", lambda _: False)
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=200 * 1024**3))
    with pytest.raises(setup.KioskSetupError) as error:
        installer.verify_download_access(Path("/fixture/hf"))
    output = capsys.readouterr().out + str(error.value)
    assert "Check your network" in output
    assert "auth login" not in output and secret not in output


def test_kiosk_cli_defaults_to_automatic_asset_selection_without_launch_flags(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        setup.sys, "argv", [str(SCRIPT), "--config", str(tmp_path / "demo.json"), "kiosk-prepare"]
    )
    monkeypatch.setattr(setup.signal, "signal", lambda *_: None)
    preparations = []
    monkeypatch.setattr(setup, "kiosk_prepare", preparations.append)
    setup.main()
    assert len(preparations) == 1
    args = preparations[0]
    assert args.cosmos_source == "auto" and args.action == "kiosk-prepare"
    assert not hasattr(args, "choose") and not hasattr(args, "skip_setup")
    assert not list(tmp_path.iterdir())


def test_setup_progress_reports_stage_and_elapsed_time_without_command_or_secrets(
    installer, monkeypatch, capsys
):
    ticks = iter([100.0, 190.0])
    monkeypatch.setattr(setup.time, "monotonic", lambda: next(ticks))

    def prepare(command, **kwargs):
        kwargs["on_progress"]()
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(setup, "run_preparation", prepare)
    installer.step("prepare-cosmos-model", ["fixture", "secret-argument"])
    output = capsys.readouterr().out
    assert "Still working: prepare cosmos model (1m 30s)" in output
    assert "secret-argument" not in output
