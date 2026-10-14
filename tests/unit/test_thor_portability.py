"""Portable launchers validate/preview deployments without touching live services."""

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def launch_environment(tmp_path):
    """Replace external commands so a launcher cannot touch Docker or the network."""
    binary_dir = tmp_path / "commands"
    binary_dir.mkdir()
    log = tmp_path / "commands.jsonl"
    executable = binary_dir / "fake_command.py"
    executable.write_text(
        """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
with Path(os.environ['COMMAND_LOG']).open('a') as output:
    output.write(json.dumps([name, *sys.argv[1:]]) + '\\n')
if name == 'uname':
    print('aarch64')
    raise SystemExit(0)
if name == 'docker' and os.environ.get('ALLOW_DOCKER_CHECKS'):
    if sys.argv[1] == 'info':
        print('{"nvidia":{}}' if '.Runtimes' in ' '.join(sys.argv) else '28.0.0')
        raise SystemExit(0)
    if sys.argv[1:3] == ['image', 'inspect']:
        raise SystemExit(0)
    if sys.argv[1] == 'run' and os.environ.get('ALLOW_DOCKER_RUN'):
        raise SystemExit(0)
if name == 'openssl':
    if os.environ.get('FAIL_OPENSSL'):
        raise SystemExit(7)
    for flag in ('-out', '-keyout'):
        Path(sys.argv[sys.argv.index(flag) + 1]).write_text('test fixture, not a certificate')
    raise SystemExit(0)
raise SystemExit('Unexpected external command: ' + name)
"""
    )
    executable.chmod(0o755)
    for name in ("docker", "sudo", "curl", "wget", "openssl", "uname"):
        (binary_dir / name).symlink_to(executable)
    environment = {"PATH": os.environ["PATH"], "LANG": "C.UTF-8"}
    environment.update(
        PATH=f"{binary_dir}:{os.environ['PATH']}",
        COMMAND_LOG=str(log),
        LIVE_VLM_MODEL_DIR=str(tmp_path / "model weights"),
        LIVE_VLM_STATE_DIR=str(tmp_path / "runtime state"),
    )
    return environment, log


def run_script(name, arguments, environment, cwd):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / name), *arguments],
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )


def logged_commands(log):
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def test_certificate_default_has_no_previous_machine_address(launch_environment, tmp_path):
    environment, log = launch_environment
    result = run_script("generate_cert.sh", [], environment, tmp_path)
    assert result.returncode == 0, result.stderr
    (command,) = logged_commands(log)
    assert command[command.index("-addext") + 1] == (
        "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:::1"
    )
    assert (tmp_path / "key.pem").stat().st_mode & 0o777 == 0o600


def test_certificate_accepts_target_hostname_and_ip(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["LIVE_VLM_CERT_HOST"] = "192.0.2.27"
    result = run_script(
        "generate_cert.sh", ["demo-thor.local", "2001:db8::27"], environment, tmp_path
    )
    assert result.returncode == 0, result.stderr
    (command,) = logged_commands(log)
    names = command[command.index("-addext") + 1]
    assert "DNS:demo-thor.local" in names
    assert "IP:192.0.2.27" in names
    assert "IP:2001:db8::27" in names


def test_invalid_certificate_host_fails_before_writing(launch_environment, tmp_path):
    environment, log = launch_environment
    result = run_script(
        "generate_cert.sh", ["good.local,DNS:injected.local"], environment, tmp_path
    )
    assert result.returncode != 0
    assert "Invalid certificate hostname" in result.stderr
    assert logged_commands(log) == []
    assert not (tmp_path / "key.pem").exists()


def test_failed_certificate_generation_is_not_reported_as_success(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["FAIL_OPENSSL"] = "1"
    result = run_script("generate_cert.sh", [], environment, tmp_path)
    assert result.returncode == 7
    assert "Certificate generated:" not in result.stdout
    assert len(logged_commands(log)) == 1


def docker_plan(result):
    assert result.returncode == 0, result.stderr
    return [shlex.split(line) for line in result.stdout.splitlines() if line.startswith("docker ")]


def test_segmentation_preview_preserves_arguments_without_side_effects(
    launch_environment, tmp_path
):
    environment, log = launch_environment
    environment["LIVE_VLM_MODEL_DIR"] = str(tmp_path / "Sam's model weights $(touch INJECTED)")
    result = run_script(
        "run_segmentation_thor.sh",
        ["--dry-run", "--host", "192.0.2.42", "--port", "9080", "--model", "my local model"],
        environment,
        tmp_path,
    )
    build, run = docker_plan(result)
    assert build[:2] == ["docker", "build"]
    assert run[:2] == ["docker", "run"]
    assert f"{environment['LIVE_VLM_MODEL_DIR']}:/app/.models:ro" in run
    assert run[-6:] == ["--host", "192.0.2.42", "--port", "9080", "--model", "my local model"]
    assert logged_commands(log) == []
    assert not Path(environment["LIVE_VLM_STATE_DIR"]).exists()
    assert not Path(environment["LIVE_VLM_MODEL_DIR"]).exists()
    assert not (tmp_path / "INJECTED").exists()


def test_prepare_preview_does_not_start_application(launch_environment, tmp_path):
    environment, log = launch_environment
    result = run_script(
        "run_segmentation_thor.sh", ["--prepare", "--dry-run"], environment, tmp_path
    )
    (build,) = docker_plan(result)
    assert build[:2] == ["docker", "build"]
    assert logged_commands(log) == []


def usb_device_arguments(sysfs, devices):
    """Source only the discovery function, without invoking the launcher's main flow."""
    script = (ROOT / "scripts/run_segmentation_thor.sh").read_text()
    definitions = script.split("\nscript_dir=", 1)[0]
    result = subprocess.run(
        [
            "bash",
            "-c",
            definitions + '\nusb_camera_arguments "$1" "$2"',
            "discovery",
            str(sysfs),
            str(devices),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    return result.stdout.rstrip("\0").split("\0") if result.stdout else []


def test_usb_container_access_is_scoped_and_adds_device_group(tmp_path):
    sysfs = tmp_path / "video class"
    devices = tmp_path / "device nodes"
    sysfs.mkdir()
    devices.mkdir()
    usb = tmp_path / "usb hardware"
    interface = usb / "interface"
    interface.mkdir(parents=True)
    (usb / "idVendor").write_text("046d\n")
    (usb / "idProduct").write_text("082d\n")
    for name in ("video0", "video1"):
        (sysfs / name).mkdir()
        (sysfs / name / "device").symlink_to(interface)
        (devices / name).symlink_to("/dev/null")
    # A non-USB accelerator video node must not be exposed to the app.
    platform = tmp_path / "platform accelerator"
    platform.mkdir()
    (sysfs / "video2").mkdir()
    (sysfs / "video2" / "device").symlink_to(platform)
    (devices / "video2").symlink_to("/dev/null")
    # A stale sysfs node without a character device is ignored.
    (sysfs / "video3").mkdir()
    (sysfs / "video3" / "device").symlink_to(interface)
    (devices / "video3").touch()
    assert usb_device_arguments(sysfs, devices) == [
        "--device",
        f"{devices}/video0:/dev/video0:rw",
        "--group-add",
        str(Path("/dev/null").stat().st_gid),
        "--device",
        f"{devices}/video1:/dev/video1:rw",
    ]


def test_usb_container_discovery_handles_no_camera_devices(tmp_path):
    assert usb_device_arguments(tmp_path / "missing sysfs", tmp_path / "missing devices") == []


def test_segmentation_engine_preview_uses_repository_runtime(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["LIVE_VLM_SEGMENTATION_ENGINE"] = str(tmp_path / "retail.engine")
    result = run_script(
        "run_segmentation_thor.sh", ["--dry-run", "--skip-build"], environment, tmp_path
    )
    (run,) = docker_plan(result)
    assert f"{ROOT}/.models/thor-runtime:/runtime:ro" in run
    assert f"{ROOT}/.models/thor-runtime/usr/local/cuda-13.2:/runtime-cuda:ro" in run
    assert logged_commands(log) == []
    assert not Path(environment["LIVE_VLM_STATE_DIR"]).exists()


def test_segmentation_check_missing_weights_does_not_download(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["ALLOW_DOCKER_CHECKS"] = "1"
    result = run_script("run_segmentation_thor.sh", ["--check"], environment, tmp_path)
    assert result.returncode != 0
    assert "Missing model asset:" in result.stderr
    assert not Path(environment["LIVE_VLM_MODEL_DIR"]).exists()
    assert not Path(environment["LIVE_VLM_STATE_DIR"]).exists()
    assert all(command[0] in {"docker", "uname"} for command in logged_commands(log))
    assert all(command[1] not in {"run", "build"} for command in logged_commands(log))


def test_segmentation_check_rejects_bad_weights_without_replacing(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["ALLOW_DOCKER_CHECKS"] = "1"
    directory = Path(environment["LIVE_VLM_MODEL_DIR"])
    directory.mkdir()
    checkpoint = directory / "yoloe-26s-seg.pt"
    checkpoint.write_bytes(b"incorrect weights remain unchanged")
    result = run_script("run_segmentation_thor.sh", ["--check"], environment, tmp_path)
    assert result.returncode != 0
    assert "Checksum mismatch" in result.stderr
    assert checkpoint.read_bytes() == b"incorrect weights remain unchanged"
    assert all(command[0] in {"docker", "uname"} for command in logged_commands(log))
    assert not Path(environment["LIVE_VLM_STATE_DIR"]).exists()


def test_prepared_engine_launch_skips_build_and_weight_download(launch_environment, tmp_path):
    environment, log = launch_environment
    environment.update(ALLOW_DOCKER_CHECKS="1", ALLOW_DOCKER_RUN="1", LIVE_VLM_TRT_IN_IMAGE="1")
    engine = tmp_path / "retail target.engine"
    engine.write_bytes(b"fixture: Docker execution is mocked")
    environment["LIVE_VLM_SEGMENTATION_ENGINE"] = str(engine)
    result = run_script(
        "run_segmentation_thor.sh", ["--skip-build", "--host", "127.0.0.2"], environment, tmp_path
    )
    assert result.returncode == 0, result.stderr
    commands = logged_commands(log)
    (run,) = [command for command in commands if command[:2] == ["docker", "run"]]
    assert f"{engine}:/app/.models/retail-480.engine:ro" in run
    assert run[-2:] == ["--host", "127.0.0.2"]
    assert not any(command[:2] == ["docker", "build"] for command in commands)
    assert not any(command[0] in {"curl", "wget", "sudo"} for command in commands)


def test_export_preview_uses_target_paths_and_requested_output(launch_environment, tmp_path):
    environment, log = launch_environment
    environment["LIVE_VLM_TRT_RUNTIME_PREFIX"] = str(tmp_path / "native prefix")
    environment["LIVE_VLM_TRT_CUDA_ROOT"] = str(tmp_path / "cuda libs")
    output = tmp_path / "exports" / "retail local.engine"
    result = run_script(
        "run_segmentation_thor.sh",
        ["--export-engine", str(output), "--dry-run", "--skip-build"],
        environment,
        tmp_path,
    )
    (run,) = docker_plan(result)
    assert f"{output.parent}:/export" in run
    assert "/export/retail local.engine" in run
    assert run[run.index("--entrypoint") + 1] == "python"
    assert f"{environment['LIVE_VLM_TRT_RUNTIME_PREFIX']}:/runtime:ro" in run
    assert f"{environment['LIVE_VLM_TRT_CUDA_ROOT']}:/runtime-cuda:ro" in run
    assert "--device" not in run
    assert "--group-add" not in run
    assert not output.parent.exists()
    assert logged_commands(log) == []


def test_cosmos_preview_is_relocatable_and_does_not_touch_runtime(launch_environment, tmp_path):
    environment, log = launch_environment
    models = tmp_path / "Taylor's model files"
    cache = tmp_path / "writable cache"
    cuda = tmp_path / "CUDA payload"
    result = run_script(
        "run_cosmos_edge_thor.sh",
        [
            "--models-dir",
            str(models),
            "--cache-dir",
            str(cache),
            "--cuda-dir",
            str(cuda),
            "--image",
            "local-cosmos:validated",
            "--host",
            "127.0.0.2",
            "--port",
            "9032",
            "--name",
            "another-thor-cosmos",
            "--dry-run",
        ],
        environment,
        tmp_path,
    )
    (run,) = docker_plan(result)
    assert run[run.index("--name") + 1] == "another-thor-cosmos"
    assert f"{models}:/cosmos" in run
    assert f"{models}/model:/checkpoint:ro" in run
    assert f"{cache}:/engine-cache" in run
    assert f"{cuda}:/runtime-cuda:ro" in run
    assert "local-cosmos:validated" in run
    assert run[run.index("--host") + 1] == "127.0.0.2"
    assert run[run.index("--port") + 1] == "9032"
    assert run[run.index("--pull") + 1] == "never"
    assert logged_commands(log) == []
    assert not any(path.exists() for path in (models, cache, cuda))


@pytest.mark.parametrize(
    "arguments, error",
    [
        (["--port", "0"], "Port must be between"),
        (["--port", "70000"], "Port must be between"),
        (["--port", "--dry-run"], "expected one argument"),
        (["--name", "invalid/container"], "Invalid Docker container name"),
        (["--models-dir", "bad:path"], "Mount paths cannot contain colons"),
        (["--models-dir", "models", "--cache-dir", "models/model/cache"], "cache must be outside"),
    ],
)
def test_cosmos_bad_configuration_fails_before_external_calls(
    launch_environment, tmp_path, arguments, error
):
    environment, log = launch_environment
    result = run_script("run_cosmos_edge_thor.sh", ["--dry-run", *arguments], environment, tmp_path)
    assert result.returncode != 0
    assert error in result.stderr
    assert logged_commands(log) == []


@pytest.mark.parametrize("value", ["invalid:mount", "invalid\nmount", "invalid\rmount"])
def test_segmentation_mount_delimiters_fail_before_external_calls(
    launch_environment, tmp_path, value
):
    environment, log = launch_environment
    environment["LIVE_VLM_MODEL_DIR"] = str(tmp_path / value)
    result = run_script("run_segmentation_thor.sh", ["--dry-run"], environment, tmp_path)
    assert result.returncode != 0
    assert "bind paths" in result.stderr.lower()
    assert logged_commands(log) == []
