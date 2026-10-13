"""Runtime setup retries preserve foreign files and never publish partial binaries."""

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load_script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cosmos = load_script("cosmos_runtime_retry", "scripts/setup_cosmos_edge_thor.py")
builder = load_script("cosmos_companion_builder", "scripts/cosmos/build_encoder_cache_compat.py")


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    args = cosmos.normalize(
        cosmos.parser().parse_args(["runtime", "--work-dir", str(tmp_path / "cosmos")])
    )
    args.work_dir.mkdir()
    (args.work_dir / "TensorRT-Edge-LLM").mkdir()
    commands = []
    probes = []
    state = SimpleNamespace(args=args, commands=commands, probes=probes, pip_status=0)

    def execute(command, *_args, **_kwargs):
        commands.append([str(value) for value in command])

    def container(_args, _docker, command, **_kwargs):
        return ["fake-container", *command]

    def probe(command, **kwargs):
        probes.append(command)
        assert command == [
            "fake-container",
            "/cosmos/edge-venv/bin/python",
            "-m",
            "pip",
            "--version",
        ]
        assert kwargs == {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "timeout": 60,
            "check": False,
        }
        return SimpleNamespace(returncode=state.pip_status)

    def source_commit(command, **kwargs):
        assert command == [
            "git",
            "-C",
            str(args.work_dir / "TensorRT-Edge-LLM"),
            "rev-parse",
            "HEAD",
        ]
        return cosmos.EDGE_COMMIT + "\n"

    monkeypatch.setattr(cosmos, "execute", execute)
    monkeypatch.setattr(cosmos, "container", container)
    monkeypatch.setattr(cosmos.subprocess, "run", probe)
    monkeypatch.setattr(cosmos.subprocess, "check_output", source_commit)
    return state


def venv_commands(runtime):
    return [command for command in runtime.commands if "venv" in command]


def legacy_venv(runtime):
    directory = runtime.args.work_dir / "edge-venv"
    (directory / "bin").mkdir(parents=True)
    (directory / "bin/python").write_text("placeholder; never executed")
    (directory / "pyvenv.cfg").write_text(
        "include-system-site-packages = true\n"
        "command = /usr/bin/python3.12 -m venv --system-site-packages /cosmos/edge-venv\n"
    )
    return directory


def test_working_container_python_and_pip_are_reused_without_recreating_venv(runtime):
    legacy_venv(runtime)
    cosmos.runtime_setup(runtime.args, ["docker"])
    assert len(runtime.probes) == 1
    assert not venv_commands(runtime)
    assert not (runtime.args.work_dir / "edge-venv-install.json").exists()


def test_legacy_installer_venv_with_python_but_no_pip_is_repaired_in_place(runtime):
    directory = legacy_venv(runtime)
    sentinel = directory / "keep-installed-packages"
    sentinel.write_text("preserve")
    runtime.pip_status = 1
    cosmos.runtime_setup(runtime.args, ["docker"])
    assert len(runtime.probes) == 1
    assert venv_commands(runtime) == [
        [
            "fake-container",
            "python3.12",
            "-m",
            "venv",
            "--system-site-packages",
            "/cosmos/edge-venv",
        ]
    ]
    assert sentinel.read_text() == "preserve"
    owner = json.loads((runtime.args.work_dir / "edge-venv-install.json").read_text())
    assert owner["directory"] == str(directory)
    assert owner["source_commit"] == cosmos.EDGE_COMMIT


def test_ownership_is_published_before_first_venv_write_and_survives_failure(runtime, monkeypatch):
    execute = cosmos.execute

    def fail_venv(command, *args, **kwargs):
        if "venv" in command:
            owner = runtime.args.work_dir / "edge-venv-install.json"
            assert json.loads(owner.read_text())["installer"] == "live-vlm-webui-edge-venv-v1"
            (runtime.args.work_dir / "edge-venv").mkdir()
            raise subprocess.CalledProcessError(1, command)
        execute(command, *args, **kwargs)

    monkeypatch.setattr(cosmos, "execute", fail_venv)
    with pytest.raises(subprocess.CalledProcessError):
        cosmos.runtime_setup(runtime.args, ["docker"])
    monkeypatch.setattr(cosmos, "execute", execute)
    runtime.pip_status = 127
    cosmos.runtime_setup(runtime.args, ["docker"])
    assert len(venv_commands(runtime)) == 1


def test_incomplete_foreign_venv_is_preserved(runtime):
    directory = runtime.args.work_dir / "edge-venv"
    directory.mkdir()
    sentinel = directory / "my-environment"
    sentinel.write_text("preserve")
    runtime.pip_status = 1
    with pytest.raises(ValueError, match="unrecognized environment"):
        cosmos.runtime_setup(runtime.args, ["docker"])
    assert sentinel.read_text() == "preserve"
    assert not venv_commands(runtime)
    assert not (runtime.args.work_dir / "edge-venv-install.json").exists()


def test_mismatched_ownership_is_not_overridden_by_legacy_config(runtime):
    legacy_venv(runtime)
    marker = runtime.args.work_dir / "edge-venv-install.json"
    marker.write_text('{"installer": "different"}')
    runtime.pip_status = 1
    with pytest.raises(ValueError, match="unrecognized environment"):
        cosmos.runtime_setup(runtime.args, ["docker"])
    assert marker.read_text() == '{"installer": "different"}'
    assert not venv_commands(runtime)


def test_dry_run_has_no_probes_or_ownership_writes(runtime):
    legacy_venv(runtime)
    runtime.args.dry_run = True
    cosmos.runtime_setup(runtime.args, ["docker"])
    assert not runtime.probes
    assert len(venv_commands(runtime)) == 1
    assert not (runtime.args.work_dir / "edge-venv-install.json").exists()


@pytest.fixture
def companion(tmp_path, monkeypatch):
    output = tmp_path / "native-compat"
    arguments = {
        "source": tmp_path / "edge-source",
        "include": tmp_path / "pybind-include",
        "python_include": tmp_path / "python-include",
        "output_dir": output,
        "cxx": "fake-cxx",
    }
    suffix = ".cpython-312-aarch64-linux-gnu.so"
    state = SimpleNamespace(
        arguments=arguments,
        output=output / ("_edgellm_cache_config_compat" + suffix),
        receipt=output / "build_receipt.json",
        status=0,
        abi=builder.ABI,
        interrupt=False,
        compile_calls=0,
        validated=False,
    )

    def compile_module(command, **kwargs):
        state.compile_calls += 1
        assert command[-2] == "-o"
        target = Path(command[-1])
        assert target != state.output
        assert target.parent.parent == output
        assert not state.output.exists()
        target.write_bytes(b"fixture compiled module; never imported")
        if state.interrupt:
            raise KeyboardInterrupt
        return SimpleNamespace(returncode=state.status, stdout="compiler output", stderr="")

    def spec(_name, filename):
        assert Path(filename).read_bytes() == b"fixture compiled module; never imported"
        assert not state.output.exists()

        def validate(module):
            module.pybind_internals_id = state.abi
            module.edgellm_source_commit = builder.COMMIT
            state.validated = True

        return SimpleNamespace(loader=SimpleNamespace(exec_module=validate))

    monkeypatch.setattr(builder.sysconfig, "get_config_var", lambda _name: suffix)
    monkeypatch.setattr(builder.subprocess, "run", compile_module)
    monkeypatch.setattr(
        builder.subprocess, "check_output", lambda *_args, **_kwargs: "compiler 1\n"
    )
    monkeypatch.setattr(builder.importlib.util, "spec_from_file_location", spec)
    monkeypatch.setattr(builder.importlib.util, "module_from_spec", lambda _spec: SimpleNamespace())
    return state


def test_native_companion_is_published_only_after_validation(companion):
    receipt = builder.build_companion(**companion.arguments)
    assert companion.validated
    assert companion.output.read_bytes() == b"fixture compiled module; never imported"
    assert receipt == json.loads(companion.receipt.read_text())
    assert receipt["module_sha256"] == builder.sha(companion.output)
    assert receipt["source_commit"] == builder.COMMIT
    assert receipt["pybind_internals_id"] == builder.ABI
    assert not list(companion.output.parent.glob(".companion-build-*"))


@pytest.mark.parametrize("failure", ["compile", "abi", "interrupt"])
def test_failed_native_attempt_does_not_block_retry(companion, failure):
    if failure == "compile":
        companion.status = 1
    elif failure == "abi":
        companion.abi = "wrong ABI"
    else:
        companion.interrupt = True
    exception = KeyboardInterrupt if failure == "interrupt" else RuntimeError
    with pytest.raises(exception):
        builder.build_companion(**companion.arguments)
    assert not companion.output.exists()
    assert not companion.receipt.exists()
    companion.status = 0
    companion.abi = builder.ABI
    companion.interrupt = False
    builder.build_companion(**companion.arguments)
    assert companion.output.is_file()
    assert companion.compile_calls == 2


@pytest.mark.parametrize("existing", ["module", "receipt"])
def test_existing_native_files_are_never_overwritten(companion, existing):
    path = companion.output if existing == "module" else companion.receipt
    path.parent.mkdir()
    path.write_bytes(b"foreign existing file")
    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        builder.build_companion(**companion.arguments)
    assert path.read_bytes() == b"foreign existing file"
    assert companion.compile_calls == 0


def test_concurrent_output_is_preserved_during_atomic_publication(companion, monkeypatch):
    link = builder.os.link

    def collision(source, destination):
        if destination == companion.output:
            companion.output.write_bytes(b"concurrent existing module")
        return link(source, destination)

    monkeypatch.setattr(builder.os, "link", collision)
    with pytest.raises(FileExistsError):
        builder.build_companion(**companion.arguments)
    assert companion.output.read_bytes() == b"concurrent existing module"
    assert not companion.receipt.exists()
