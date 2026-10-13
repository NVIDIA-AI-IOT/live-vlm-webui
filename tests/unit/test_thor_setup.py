"""Repository-first setup works without another machine's models or runtime files."""

import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/thor_demo.py"
spec = importlib.util.spec_from_file_location("thor_setup", SCRIPT)
thor = importlib.util.module_from_spec(spec)
spec.loader.exec_module(thor)


def config_at(folder, **overrides):
    path = folder / "thor-demo.json"
    path.write_text(json.dumps(thor.DEFAULTS | overrides))
    return thor.read_config(path)


def test_baseline_configuration_has_no_external_runtime_dependency(tmp_path):
    config = config_at(tmp_path)
    env = thor.environment(config)
    assert config["segmentation_engine"] is None
    assert config["trt_runtime_prefix"] is None
    assert config["cuda_dir"] is None
    assert env["LIVE_VLM_MODEL_DIR"] == str(tmp_path / ".models")
    assert not env.get("LIVE_VLM_SEGMENTATION_ENGINE")
    assert not env.get("LIVE_VLM_TRT_RUNTIME_PREFIX")
    assert not env.get("LIVE_VLM_TRT_CUDA_ROOT")
    assert "cosmos-nano-benchmark" not in json.dumps(env)


def test_config_paths_resolve_from_configuration_directory(tmp_path, monkeypatch):
    folder = tmp_path / "Thor with spaces"
    folder.mkdir()
    monkeypatch.chdir("/tmp")
    config = config_at(folder, segmentation_model_dir="weights with 'quotes'/$model")
    assert config["segmentation_model_dir"] == str(folder / "weights with 'quotes'/$model")
    assert config["cosmos_dir"] == str(folder / ".models/cosmos")


def test_webui_uses_explicit_vlm_endpoint_and_backend(tmp_path):
    config = config_at(
        tmp_path,
        vlm_api_base="http://192.0.2.51:8000/v1",
        vlm_model="another/local-model",
        vlm_backend="openai",
        ui_port=9080,
    )
    (command,) = thor.launch_commands("webui", config, skip_build=True)
    assert "--skip-build" in command
    assert command[command.index("--api-base") + 1] == "http://192.0.2.51:8000/v1"
    assert command[command.index("--model") + 1] == "another/local-model"
    assert command[command.index("--vlm-backend") + 1] == "openai"
    assert command[command.index("--port") + 1] == "9080"
    assert not any("run_cosmos" in word for word in command)


def test_prepare_and_baseline_check_do_not_start_model_servers(tmp_path):
    config = config_at(tmp_path)
    (prepare,) = thor.launch_commands("prepare", config)
    (check,) = thor.launch_commands("check", config)
    assert Path(prepare[1]).name == "run_segmentation_thor.sh"
    assert prepare[-1] == "--prepare"
    assert Path(check[1]).name == "run_segmentation_thor.sh"
    assert check[-1] == "--check"


@pytest.mark.parametrize(
    "override",
    [
        {"ui_port": True},
        {"ui_port": -1},
        {"vlm_backend": "unknown"},
        {"vlm_api_base": "file:///tmp/model"},
        {"unknown_setting": "no"},
    ],
)
def test_invalid_configuration_fails_before_running_commands(tmp_path, override):
    with pytest.raises(ValueError):
        config_at(tmp_path, **override)


def test_dry_run_never_spawns_build_or_inference(tmp_path, monkeypatch, capsys):
    config = config_at(tmp_path)

    def forbidden(*args, **kwargs):
        pytest.fail("A dry run spawned an external command")

    monkeypatch.setattr(thor.subprocess, "run", forbidden)
    args = SimpleNamespace(action="webui", dry_run=True, skip_build=True, cosmos=False)
    thor.run_action(args, config)
    printed = capsys.readouterr().out
    assert "run_segmentation_thor.sh" in printed
    assert not (tmp_path / ".models").exists()


@pytest.mark.parametrize("action", ["bundle", "verify"])
def test_transfer_commands_are_removed(action, tmp_path):
    result = subprocess.run(
        [os.sys.executable, str(SCRIPT), action, "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "invalid choice" in result.stderr


RUNTIME_SCRIPT = SCRIPT.with_name("prepare_segmentation_runtime.py")
runtime_spec = importlib.util.spec_from_file_location("thor_runtime_setup", RUNTIME_SCRIPT)
runtime = importlib.util.module_from_spec(runtime_spec)
runtime_spec.loader.exec_module(runtime)


@pytest.fixture
def runtime_recipe(tmp_path, monkeypatch):
    import hashlib

    contents = b"verified fixture package"
    recipe = {
        "packages": [
            {
                "name": "fixture-runtime",
                "version": "1",
                "url": "https://example.invalid/runtime.deb",
                "size": len(contents),
                "sha256": hashlib.sha256(contents).hexdigest(),
            }
        ]
    }
    prefix = tmp_path / "native runtime"
    cache = tmp_path / "download cache"
    calls = []
    controls = {"bad_download": False, "bad_extraction": False}
    monkeypatch.setattr(runtime, "REQUIRED_PATHS", ("usr/local/cuda-13.2/lib64/fixture",))
    monkeypatch.setattr(runtime.shutil, "which", lambda command: "/fixture/" + command)

    def external(command, **kwargs):
        calls.append(command)
        assert not prefix.exists(), "Runtime prefix was published before extraction completed"
        if command[0] == "curl":
            Path(command[command.index("--output") + 1]).write_bytes(
                b"incorrect" if controls["bad_download"] else contents
            )
        elif command[0] == "dpkg-deb":
            directory = Path(command[-1]) / runtime.CUDA_RELATIVE / "targets/sbsa-linux/lib"
            directory.mkdir(parents=True)
            (directory / "fixture").write_bytes(b"extracted runtime library")
            if controls["bad_extraction"]:
                raise subprocess.CalledProcessError(2, command)
        else:
            pytest.fail(f"Unexpected external command: {command}")

    monkeypatch.setattr(runtime.subprocess, "run", external)
    return prefix, cache, recipe, calls, controls


def test_runtime_preview_has_no_downloads_or_writes(runtime_recipe):
    prefix, cache, recipe, calls, _ = runtime_recipe
    runtime.prepare(prefix, cache, manifest=recipe, dry_run=True)
    assert calls == []
    assert not prefix.exists()
    assert not cache.exists()


def test_runtime_check_does_not_try_to_install_missing_dependencies(runtime_recipe):
    prefix, cache, recipe, calls, _ = runtime_recipe
    with pytest.raises(ValueError, match="No prepared runtime receipt"):
        runtime.prepare(prefix, cache, manifest=recipe, check_only=True)
    assert calls == []
    assert not prefix.exists()
    assert not cache.exists()


def test_runtime_is_published_after_verification_and_reused_without_download(runtime_recipe):
    prefix, cache, recipe, calls, _ = runtime_recipe
    runtime.prepare(prefix, cache, manifest=recipe)
    assert [command[0] for command in calls] == ["curl", "dpkg-deb"]
    assert (prefix / runtime.CUDA_RELATIVE / "lib64").is_symlink()
    assert (
        prefix / runtime.CUDA_RELATIVE / "lib64/fixture"
    ).read_bytes() == b"extracted runtime library"
    calls.clear()
    receipt = runtime.prepare(prefix, cache, manifest=recipe)
    assert calls == []
    assert receipt["recipe_sha256"] == runtime.recipe_digest(recipe)


def test_runtime_download_hash_failure_does_not_publish_or_extract(runtime_recipe):
    prefix, cache, recipe, calls, controls = runtime_recipe
    controls["bad_download"] = True
    with pytest.raises(ValueError, match="checksum mismatch"):
        runtime.prepare(prefix, cache, manifest=recipe)
    assert not prefix.exists()
    assert list(cache.iterdir()) == []
    assert [command[0] for command in calls] == ["curl"]


def test_runtime_extraction_failure_does_not_leave_partial_install(runtime_recipe):
    prefix, cache, recipe, _, controls = runtime_recipe
    controls["bad_extraction"] = True
    with pytest.raises(subprocess.CalledProcessError):
        runtime.prepare(prefix, cache, manifest=recipe)
    assert not prefix.exists()
    assert not list(prefix.parent.glob(f".{prefix.name}.prepare-*"))
    assert (cache / "runtime.deb").is_file()


def test_runtime_preserves_bad_existing_download_for_user_inspection(runtime_recipe):
    prefix, cache, recipe, calls, _ = runtime_recipe
    cache.mkdir()
    archive = cache / "runtime.deb"
    archive.write_bytes(b"corrupt cached download")
    with pytest.raises(ValueError, match="existing file left unchanged"):
        runtime.prepare(prefix, cache, manifest=recipe)
    assert archive.read_bytes() == b"corrupt cached download"
    assert calls == []
    assert not prefix.exists()


def test_runtime_rejects_cache_inside_installation_before_writing(runtime_recipe):
    prefix, _, recipe, calls, _ = runtime_recipe
    with pytest.raises(ValueError, match="cache|Cache"):
        runtime.prepare(prefix, prefix / "downloads", manifest=recipe)
    assert not prefix.exists()
    assert calls == []


def test_init_creates_only_config_and_never_overwrites_user_settings(tmp_path):
    path = tmp_path / "new setup" / "thor-demo.json"
    thor.init_config(path)
    assert json.loads(path.read_text()) == thor.DEFAULTS
    path.write_text('{"vlm_model": "keep-my-model"}')
    with pytest.raises(FileExistsError):
        thor.init_config(path)
    assert path.read_text() == '{"vlm_model": "keep-my-model"}'
    assert list(path.parent.iterdir()) == [path]


def test_optional_cosmos_check_is_explicit(tmp_path):
    config = config_at(tmp_path)
    ordinary = thor.launch_commands("check", config)
    complete = thor.launch_commands("check", config, check_cosmos=True)
    assert len(ordinary) == 1
    assert complete[0] == ordinary[0]
    assert Path(complete[1][1]).name == "run_cosmos_edge_thor.sh"
    assert complete[1][-1] == "--check"
    assert complete[1][complete[1].index("--models-dir") + 1] == str(tmp_path / ".models/cosmos")


def test_baseline_launch_clears_inherited_engine_selection(tmp_path, monkeypatch):
    config = config_at(tmp_path)
    monkeypatch.setenv("LIVE_VLM_SEGMENTATION_ENGINE", "/old-machine/retail.engine")
    monkeypatch.setenv("LIVE_VLM_TRT_RUNTIME_PREFIX", "/old-machine/tensorrt")
    calls = []
    monkeypatch.setattr(
        thor.subprocess, "run", lambda command, **kwargs: calls.append((command, kwargs))
    )
    thor.run_action(
        SimpleNamespace(action="webui", dry_run=False, skip_build=True), config, repo=tmp_path
    )
    _, options = calls[0]
    assert options["env"]["LIVE_VLM_SEGMENTATION_ENGINE"] == ""
    assert options["env"]["LIVE_VLM_TRT_RUNTIME_PREFIX"] == ""
    assert options["cwd"] == tmp_path


def test_runtime_and_engine_export_use_same_configured_native_paths(tmp_path):
    config = config_at(tmp_path, trt_runtime_prefix="custom runtime", cuda_dir="custom cuda")
    (command,) = thor.launch_commands("runtime", config)
    native = thor.environment(config, native=True)
    assert command[command.index("--prefix") + 1] == native["LIVE_VLM_TRT_RUNTIME_PREFIX"]
    assert native["LIVE_VLM_TRT_CUDA_ROOT"] == str(tmp_path / "custom cuda")
    (export,) = thor.launch_commands(
        "export-engine", config, skip_build=True, output=tmp_path / "new.engine"
    )
    assert export[export.index("--export-engine") + 1] == str(tmp_path / "new.engine")
