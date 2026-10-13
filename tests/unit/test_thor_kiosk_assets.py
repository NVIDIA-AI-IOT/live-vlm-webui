"""Asset preparation for the upstream kiosk never starts services or a browser."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/thor_demo.py"
sys.path.insert(0, str(SCRIPT.parent))
try:
    spec = importlib.util.spec_from_file_location("thor_kiosk_assets", SCRIPT)
    setup = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(setup)
finally:
    sys.path.pop(0)


def arguments(tmp_path, **overrides):
    return SimpleNamespace(
        **(
            {"config": tmp_path / "thor-demo.json", "cosmos_source": "checkpoint", "dry_run": False}
            | overrides
        )
    )


def forbidden(*args, **kwargs):
    pytest.fail("Unexpected installer, model server, browser, or network access")


def installer_at(tmp_path, monkeypatch, *, config=None, **overrides):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    args = arguments(tmp_path, **overrides)
    if config is not None:
        args.config.write_text(json.dumps(config))
    return setup.KioskAssets(args)


def complete_checkpoint(installer):
    directory = installer.cosmos_args("check").model_dir
    directory.mkdir(parents=True)
    for name in (
        "config.json",
        "hf_quant_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "preprocessor_config.json",
        "video_preprocessor_config.json",
    ):
        (directory / name).write_text("{}")
    (directory / "model.safetensors").write_bytes(b"fixture checkpoint; never loaded")
    return directory


def write_engine(path, **changes):
    """Represent exporter metadata; the fake TensorRT payload is never deserialized."""
    metadata = {
        "task": "segment",
        "head": "YOLOESegment26",
        "imgsz": [288, 480],
        "batch": 4,
        "channels": 3,
        "stride": 32,
        "end2end": False,
        "names": {"0": "person", "1": "shopping cart", "2": "shopping basket", "3": "backpack"},
        "args": {"dynamic": True, "nms": False},
    } | changes
    header = json.dumps(metadata).encode()
    content = struct.pack("<I", len(header)) + header + b"fake serialized engine"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


def mock_install_operations(installer, monkeypatch, *, runtime_ready=False, inspected=True):
    events = []
    calls = []

    def host(command, **kwargs):
        assert command == ["bash", str(setup.REPO / "scripts/prepare_thor_host.sh")]
        assert kwargs == {"check": True}
        events.append("host")
        return SimpleNamespace(returncode=0)

    def docker_access():
        events.append("docker")
        return ["fake-docker"]

    def hf():
        events.append("hf")
        return Path("/fixture/hf")

    def access(binary):
        assert binary == Path("/fixture/hf")
        events.append("access")

    def step(name, command, **kwargs):
        events.append(name)
        calls.append((name, [str(value) for value in command], kwargs))
        if name == "build-retail-segmentation-engine":
            path = Path(command[command.index("--export-engine") + 1])
            path.parent.mkdir(parents=True, exist_ok=True)
            write_engine(path)

    monkeypatch.setattr(setup.subprocess, "run", host)
    monkeypatch.setattr(setup.subprocess, "Popen", forbidden)
    monkeypatch.setattr(setup.cosmos, "docker_access", docker_access)
    monkeypatch.setattr(setup.cosmos, "checkpoint_inspected", lambda _: inspected)
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=200 * 1024**3))
    monkeypatch.setattr(installer, "ensure_hf", hf)
    monkeypatch.setattr(installer, "verify_download_access", access)
    monkeypatch.setattr(installer, "ensure_webui", lambda: events.append("webui"))
    monkeypatch.setattr(installer, "ensure_native", lambda: events.append("native"))
    monkeypatch.setattr(installer, "cosmos_runtime_ready", lambda: runtime_ready)
    monkeypatch.setattr(installer, "step", step)
    return events, calls


@pytest.mark.parametrize(
    "source,model_action", [("checkpoint", "model-download"), ("public", "model-build")]
)
def test_fresh_setup_checks_access_before_builds_and_native_runtime_before_engines(
    tmp_path, monkeypatch, source, model_action
):
    installer = installer_at(tmp_path, monkeypatch, cosmos_source=source)
    events, calls = mock_install_operations(installer, monkeypatch)
    installer.install()
    assert events == [
        "host",
        "docker",
        "hf",
        "access",
        "webui",
        "native",
        "prepare-cosmos-runtime",
        "prepare-cosmos-model",
        "check-cosmos-runtime",
        "build-retail-segmentation-engine",
        "prepare-store-videos",
        "check-webui",
    ]
    commands = {name: command for name, command, _ in calls}
    assert commands["prepare-cosmos-model"][2] == model_action
    assert commands["prepare-cosmos-runtime"][2] == "runtime"
    assert commands["check-cosmos-runtime"][2] == "check"
    assert Path(installer.config["segmentation_engine"]).is_file()
    assert (
        json.loads(installer.config_path.read_text())["segmentation_engine"]
        == ".models/retail-480.engine"
    )


@pytest.mark.parametrize("source", ["auto", "checkpoint", "public"])
def test_complete_local_checkpoint_skips_hf_and_downloads(tmp_path, monkeypatch, source):
    installer = installer_at(tmp_path, monkeypatch, cosmos_source=source)
    complete_checkpoint(installer)
    events, calls = mock_install_operations(installer, monkeypatch, runtime_ready=True)
    monkeypatch.setattr(installer, "ensure_hf", forbidden)
    monkeypatch.setattr(installer, "verify_download_access", forbidden)
    monkeypatch.setattr(setup.shutil, "disk_usage", forbidden)
    installer.install()
    assert "prepare-cosmos-runtime" not in events
    assert "prepare-cosmos-model" not in events
    assert "verify-local-cosmos-checkpoint" not in events
    assert "check-cosmos-runtime" in events
    assert all(
        "model-download" not in command and "model-build" not in command for _, command, _ in calls
    )


def test_uninspected_local_checkpoint_gets_validation_without_redownload(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch)
    complete_checkpoint(installer)
    events, calls = mock_install_operations(
        installer, monkeypatch, runtime_ready=True, inspected=False
    )
    monkeypatch.setattr(installer, "ensure_hf", forbidden)
    installer.install()
    assert "prepare-cosmos-model" not in events
    assert events.index("verify-local-cosmos-checkpoint") < events.index("check-cosmos-runtime")
    inspection = next(
        command for name, command, _ in calls if name == "verify-local-cosmos-checkpoint"
    )
    assert inspection[2] == "model-check"


def test_external_vlm_skips_all_cosmos_download_runtime_and_validation(tmp_path, monkeypatch):
    installer = installer_at(
        tmp_path,
        monkeypatch,
        config={
            "vlm_api_base": "http://192.0.2.10:8000/v1",
            "vlm_model": "external-model",
            "vlm_backend": "openai",
        },
    )
    events, calls = mock_install_operations(installer, monkeypatch)
    for name in (
        "ensure_hf",
        "verify_download_access",
        "local_checkpoint_present",
        "cosmos_runtime_ready",
        "cosmos_args",
        "cosmos_command",
    ):
        monkeypatch.setattr(installer, name, forbidden)
    monkeypatch.setattr(setup.shutil, "disk_usage", forbidden)
    installer.install()
    assert events == [
        "host",
        "docker",
        "webui",
        "native",
        "build-retail-segmentation-engine",
        "prepare-store-videos",
        "check-webui",
    ]
    assert not any("setup_cosmos" in word for _, command, _ in calls for word in command)


def test_usb_setup_also_prepares_recorded_videos_for_ui_switching(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch, source="usb", cameras=2)
    complete_checkpoint(installer)
    events, _ = mock_install_operations(installer, monkeypatch, runtime_ready=True)
    installer.install()
    assert "prepare-store-videos" in events


def test_low_disk_space_fails_before_authentication_or_builds(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch, cosmos_source="public")
    events, _ = mock_install_operations(installer, monkeypatch)
    monkeypatch.setattr(setup.shutil, "disk_usage", lambda _: SimpleNamespace(free=99 * 1024**3))
    with pytest.raises(setup.KioskSetupError, match="100 GB"):
        installer.install()
    assert events == ["host", "docker"]


def test_engine_selection_preserves_unrelated_values_and_custom_paths(tmp_path, monkeypatch):
    original = {
        "vlm_api_base": "http://192.0.2.15:8091/v1",
        "vlm_backend": "openai",
        "vlm_model": "custom",
        "ui_port": 9090,
        "segmentation_model_dir": "my models",
        "trt_runtime_prefix": "my runtime",
        "cuda_dir": "my CUDA",
        "cosmos_dir": "my Cosmos",
    }
    installer = installer_at(tmp_path, monkeypatch, config=original)
    seen = []

    def export(name, command, **kwargs):
        assert name == "build-retail-segmentation-engine"
        assert kwargs == {"native": True}
        path = Path(command[command.index("--export-engine") + 1])
        seen.append(path)
        path.parent.mkdir(parents=True)
        write_engine(path)

    monkeypatch.setattr(installer, "step", export)
    installer.select_engine()
    saved = json.loads(installer.config_path.read_text())
    assert saved == original | {"segmentation_engine": "my models/retail-480.engine"}
    assert seen == [tmp_path / "my models/retail-480.engine"]
    assert installer.config["segmentation_engine"] == str(seen[0])


def test_interrupted_export_sidecars_are_preserved_with_fresh_engine_name(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch)
    target = tmp_path / ".models/retail-480.engine"
    target.parent.mkdir()
    sidecars = {
        target.with_suffix(suffix): ("previous " + suffix).encode()
        for suffix in (".json", ".npz", ".onnx")
    }
    for path, contents in sidecars.items():
        path.write_bytes(contents)
    outputs = []

    def export(name, command, **kwargs):
        output = Path(command[command.index("--export-engine") + 1])
        outputs.append(output)
        write_engine(output)

    monkeypatch.setattr(installer, "step", export)
    installer.select_engine()
    assert len(outputs) == 1
    assert outputs[0] != target
    assert outputs[0].name.startswith("retail-480-") and outputs[0].suffix == ".engine"
    assert not target.exists()
    assert {path: path.read_bytes() for path in sidecars} == sidecars
    assert Path(installer.config["segmentation_engine"]) == outputs[0]


def test_existing_custom_engine_is_reused_without_rewriting_config(tmp_path, monkeypatch):
    engine = tmp_path / "custom.engine"
    engine_bytes = write_engine(engine)
    installer = installer_at(tmp_path, monkeypatch, config={"segmentation_engine": "custom.engine"})
    original = installer.config_path.read_bytes()
    monkeypatch.setattr(installer, "step", forbidden)
    installer.select_engine()
    assert engine.read_bytes() == engine_bytes
    assert installer.config_path.read_bytes() == original


def test_failed_engine_export_does_not_enable_it_in_config(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch)
    original = installer.config_path.read_bytes()

    def fail_export(*args, **kwargs):
        raise setup.KioskSetupError("engine export failed")

    monkeypatch.setattr(installer, "step", fail_export)
    with pytest.raises(setup.KioskSetupError, match="engine export failed"):
        installer.select_engine()
    assert installer.config_path.read_bytes() == original
    assert installer.config["segmentation_engine"] is None


@pytest.mark.parametrize("source", ["checkpoint", "public"])
def test_denied_noninteractive_hf_access_never_logs_in_or_prints_token(
    tmp_path, monkeypatch, capsys, source
):
    token = "hf_fixture_secret_do_not_print"
    monkeypatch.setenv("HF_TOKEN", token)
    installer = installer_at(tmp_path, monkeypatch, cosmos_source=source)
    capsys.readouterr()
    commands = []
    monkeypatch.setattr(installer, "probe", lambda command: commands.append(command) or False)
    monkeypatch.setattr(setup.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(setup.sys.stdout, "isatty", lambda: False)
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    with pytest.raises(setup.KioskSetupError) as error:
        installer.verify_download_access(Path("/fixture/hf"))
    assert len(commands) == 1
    assert commands[0][1] == "download" and commands[0][-1] == "--dry-run"
    assert all(token not in str(part) for part in commands[0])
    assert token not in str(error.value)
    assert token not in capsys.readouterr().out
    assert ("auth login" in str(error.value)) == (source == "checkpoint")
    if source == "checkpoint":
        assert "--cosmos-source public" in str(error.value)


def test_webui_preparation_ignores_unbuilt_engine_and_reuses_ready_image(tmp_path, monkeypatch):
    installer = installer_at(
        tmp_path,
        monkeypatch,
        config={
            "segmentation_engine": "not-yet-built.engine",
            "trt_runtime_prefix": "not-yet-installed",
            "cuda_dir": "not-yet-installed/cuda",
        },
    )
    configurations = []
    commands = []

    def probe(command, *, config):
        configurations.append(config)
        assert "--check" in command
        return False

    def step(name, command, *, config):
        configurations.append(config)
        commands.append(command)
        assert name == "prepare-webui-and-segmentation"

    monkeypatch.setattr(installer, "probe", probe)
    monkeypatch.setattr(installer, "step", step)
    monkeypatch.setattr(installer, "image_ready", lambda _: True)
    installer.ensure_webui()
    assert all(
        config["segmentation_engine"] is None
        and config["trt_runtime_prefix"] is None
        and config["cuda_dir"] is None
        for config in configurations
    )
    assert commands[0][-1] == "--skip-build"
    assert "--prepare" in commands[0]
    assert installer.config["segmentation_engine"] == str(tmp_path / "not-yet-built.engine")


@pytest.mark.parametrize(
    "content",
    [b"", b"123", struct.pack("<I", 100) + b"truncated", struct.pack("<I", 100_000_000) + b"small"],
)
def test_engine_validation_rejects_truncated_or_unbounded_headers(tmp_path, content):
    engine = tmp_path / "invalid.engine"
    engine.write_bytes(content)
    with pytest.raises(setup.KioskSetupError, match="Invalid retail engine"):
        setup.validate_engine(engine)
    assert engine.read_bytes() == content


@pytest.mark.parametrize(
    "changes",
    [
        {"task": "detect"},
        {"batch": 2},
        {"imgsz": [480, 480]},
        {"args": {"dynamic": False}},
        {"names": {"0": "person"}},
    ],
)
def test_engine_validation_rejects_incompatible_metadata_without_changing_files(tmp_path, changes):
    engine = tmp_path / "incompatible.engine"
    original = write_engine(engine, **changes)
    with pytest.raises(setup.KioskSetupError, match="Invalid retail engine"):
        setup.validate_engine(engine)
    assert engine.read_bytes() == original


@pytest.mark.parametrize(
    "mismatch",
    [
        {},
        {"engine_sha256": "0" * 64},
        {"gpu_capability": [8, 7]},
        {"tensorrt": "8.6.0"},
        {"source_sha256": "0" * 64},
    ],
)
def test_engine_receipt_is_checked_against_current_engine_and_thor_runtime(tmp_path, mismatch):
    engine = tmp_path / "retail.engine"
    content = write_engine(engine)
    report = {
        "engine_sha256": hashlib.sha256(content).hexdigest(),
        "gpu_capability": [11, 0],
        "tensorrt": "10.16.2.10",
        "source_sha256": setup.exporter.CHECKPOINT_SHA256,
    } | mismatch
    receipt = engine.with_suffix(".json")
    receipt.write_text(json.dumps(report))
    original = receipt.read_bytes()
    if mismatch:
        with pytest.raises(setup.KioskSetupError, match="export receipt does not match"):
            setup.validate_engine(engine)
    else:
        setup.validate_engine(engine)
    assert engine.read_bytes() == content
    assert receipt.read_bytes() == original


def test_corrupt_existing_engine_is_not_replaced_or_selected(tmp_path, monkeypatch):
    installer = installer_at(tmp_path, monkeypatch)
    engine = tmp_path / ".models/retail-480.engine"
    content = write_engine(engine, task="detect")
    original = installer.config_path.read_bytes()
    monkeypatch.setattr(installer, "step", forbidden)
    with pytest.raises(setup.KioskSetupError, match="Invalid retail engine"):
        installer.select_engine()
    assert engine.read_bytes() == content
    assert installer.config_path.read_bytes() == original


@pytest.mark.parametrize(
    "marker",
    [
        None,
        "invalid JSON",
        "[]",
        '{"installer": "someone-else", "version": "1.30.0"}',
        '{"installer": "live-vlm-webui", "version": "old"}',
    ],
)
def test_unowned_or_invalid_hf_environment_is_not_modified(tmp_path, monkeypatch, marker):
    installer = installer_at(tmp_path, monkeypatch)
    directory = Path(installer.config["segmentation_model_dir"]) / "demo-hf-cli"
    directory.mkdir(parents=True)
    existing = directory / "keep.txt"
    existing.write_text("user content")
    if marker is not None:
        (directory / ".demo-cli.json").write_text(marker)
    before = {path.name: path.read_bytes() for path in directory.iterdir()}
    monkeypatch.setattr(installer, "step", forbidden)
    monkeypatch.setattr(installer, "probe", forbidden)
    with pytest.raises(setup.KioskSetupError):
        installer.ensure_hf()
    assert {path.name: path.read_bytes() for path in directory.iterdir()} == before


def test_owned_hf_environment_with_missing_pip_is_repaired_before_installing_cli(
    tmp_path, monkeypatch
):
    installer = installer_at(tmp_path, monkeypatch)
    directory = Path(installer.config["segmentation_model_dir"]) / "demo-hf-cli"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin/python").write_text("fixture interpreter")
    (directory / ".demo-cli.json").write_text(
        json.dumps({"installer": "live-vlm-webui", "version": setup.HF_VERSION})
    )
    steps = []
    probes = []

    def probe(command):
        probes.append(command)
        return False

    def step(name, command):
        steps.append((name, command))
        if name == "install-model-downloader":
            (directory / "bin/hf").write_text("fixture CLI")

    monkeypatch.setattr(installer, "probe", probe)
    monkeypatch.setattr(installer, "step", step)
    cli = installer.ensure_hf()
    assert probes[0][1:] == ["-m", "pip", "--version"]
    assert [name for name, _ in steps] == [
        "create-model-download-environment",
        "install-model-downloader",
    ]
    assert steps[0][1][1:3] == ["-m", "venv"]
    assert steps[1][1][-1] == setup.HF_WHEEL
    assert cli == directory / "bin/hf"
    assert installer.env["PATH"].split(os.pathsep)[0] == str(directory / "bin")


@pytest.mark.parametrize("published_checkpoint", [False, True])
def test_owned_public_resume_keeps_source_choice_and_reenters_build_before_inspection(
    tmp_path, monkeypatch, published_checkpoint
):
    # A plain retry omits --cosmos-source public after source download or after
    # weights were published but processor repair/final inspection did not finish.
    installer = installer_at(tmp_path, monkeypatch)
    model_args = installer.cosmos_args("check")
    model_args.work_dir.mkdir(parents=True)
    progress = setup.cosmos.BuildProgress(model_args)
    if published_checkpoint:
        progress.begin(
            "quantization",
            [model_args.model_dir, model_args.work_dir / "quantization-evaluation"],
            lambda: False,
        )
        complete_checkpoint(installer)
    else:
        setup.cosmos.save_json(progress.path, progress.data)
    original_journal = progress.path.read_bytes()
    events, calls = mock_install_operations(
        installer, monkeypatch, runtime_ready=True, inspected=False
    )
    # Source/intermediate files already consumed the original disk reservation.
    monkeypatch.setattr(setup.shutil, "disk_usage", forbidden)
    installer.install()
    assert installer.args.cosmos_source == "public"
    model_command = next(command for name, command, _ in calls if name == "prepare-cosmos-model")
    assert model_command[2] == "model-build"
    assert "verify-local-cosmos-checkpoint" not in events
    assert events.index("prepare-cosmos-model") < events.index("check-cosmos-runtime")
    assert progress.path.read_bytes() == original_journal


@pytest.mark.parametrize("invalid_journal", ["malformed", "foreign-recipe"])
def test_invalid_public_resume_journal_preserves_outputs_before_any_download(
    tmp_path, monkeypatch, invalid_journal
):
    installer = installer_at(tmp_path, monkeypatch)
    model_args = installer.cosmos_args("check")
    model_args.work_dir.mkdir(parents=True)
    progress = setup.cosmos.BuildProgress(model_args)
    progress.begin(
        "quantization",
        [model_args.model_dir, model_args.work_dir / "quantization-evaluation"],
        lambda: False,
    )
    complete_checkpoint(installer)
    if invalid_journal == "malformed":
        progress.path.write_text("not valid JSON")
    else:
        data = json.loads(progress.path.read_text())
        data["recipe"]["owner"] = "another-project"
        progress.path.write_text(json.dumps(data))
    before = {
        path.relative_to(model_args.work_dir): path.read_bytes()
        for path in model_args.work_dir.rglob("*")
        if path.is_file()
    }
    config = installer.config_path.read_bytes()
    events, _ = mock_install_operations(installer, monkeypatch, runtime_ready=True, inspected=False)
    monkeypatch.setattr(installer, "ensure_hf", forbidden)
    monkeypatch.setattr(installer, "verify_download_access", forbidden)
    monkeypatch.setattr(setup.shutil, "disk_usage", forbidden)
    with pytest.raises(ValueError):
        installer.install()
    assert events == ["host", "docker"]
    assert installer.config_path.read_bytes() == config
    assert {
        path.relative_to(model_args.work_dir): path.read_bytes()
        for path in model_args.work_dir.rglob("*")
        if path.is_file()
    } == before


def test_kiosk_prepare_completes_after_assets_without_any_service_or_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    monkeypatch.setattr(setup, "SudoKeepalive", lambda: nullcontext(None))
    installs = []
    monkeypatch.setattr(
        setup, "KioskAssets", lambda args: SimpleNamespace(install=lambda: installs.append(args))
    )
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    monkeypatch.setattr(setup.subprocess, "Popen", forbidden)
    args = arguments(tmp_path)
    setup.kiosk_prepare(args)
    assert installs == [args]
    assert not hasattr(setup, "ManagedServices")
    assert not hasattr(setup, "browser_open")


def test_kiosk_prepare_dry_run_has_no_processes_network_or_files(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    monkeypatch.setattr(setup, "KioskAssets", forbidden)
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    monkeypatch.setattr(setup.subprocess, "Popen", forbidden)
    setup.kiosk_prepare(arguments(tmp_path, dry_run=True, cosmos_source="auto"))
    plan = json.loads(capsys.readouterr().out)
    assert plan["starts_services"] is False
    assert plan["opens_browser"] is False
    assert plan["local_cosmos"] is True
    assert not list(tmp_path.iterdir())


def test_external_vlm_preview_omits_cosmos_preparation(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(setup, "REPO", tmp_path)
    config = tmp_path / "thor-demo.json"
    config.write_text(
        json.dumps({"vlm_api_base": "http://192.0.2.10:8000/v1", "vlm_backend": "openai"})
    )
    original = config.read_bytes()
    monkeypatch.setattr(setup.subprocess, "run", forbidden)
    setup.kiosk_prepare(arguments(tmp_path, dry_run=True, cosmos_source="auto"))
    plan = json.loads(capsys.readouterr().out)
    assert plan["local_cosmos"] is False
    assert not any("Cosmos" in step for step in plan["steps"])
    assert config.read_bytes() == original
    assert list(tmp_path.iterdir()) == [config]


@pytest.mark.parametrize(
    "options", [["--help"], ["--dry-run"], ["--dry-run", "--cosmos-source", "public"]]
)
def test_kiosk_asset_cli_help_and_preview_create_no_config(tmp_path, options):
    config = tmp_path / "untouched.json"
    result = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "--config", str(config), "kiosk-prepare", *options],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert not list(tmp_path.iterdir())
    if "--help" in options:
        assert "--cosmos-source" in result.stdout
    else:
        report = json.loads(result.stdout)
        assert report["starts_services"] is report["opens_browser"] is False


def test_webui_kiosk_http_forwarding_preserves_model_and_port(tmp_path):
    config = setup.load_config(tmp_path / "config.json", dry_run=True)
    command = setup.launch_commands("webui", config, skip_build=True, localhost_http=True)[0]
    assert "--skip-build" in command and "--localhost-http" in command
    assert command[command.index("--port") + 1] == str(config["ui_port"])
    assert command[command.index("--model") + 1] == config["vlm_model"]
