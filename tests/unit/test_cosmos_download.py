"""Cosmos download recipes select weights and metadata with current hf CLI syntax."""

import argparse
import fnmatch
import importlib.util
import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/setup_cosmos_edge_thor.py"
spec = importlib.util.spec_from_file_location("cosmos_download_setup", SCRIPT)
cosmos = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cosmos)


def parse_hf_download(command):
    """Model hf's one-value include options and optional positional filenames."""
    parser = argparse.ArgumentParser()
    parser.add_argument("repo")
    parser.add_argument("filenames", nargs="*")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--local-dir", required=True)
    parser.add_argument("--include", action="append", default=[])
    return parser.parse_intermixed_args(command[2:])


@pytest.mark.parametrize(
    "action,repository,revision,required_files,excluded_files",
    [
        (
            "model-download",
            cosmos.CHECKPOINT_REPO,
            cosmos.CHECKPOINT_REVISION,
            [
                "model.safetensors",
                "config.json",
                "hf_quant_config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "chat_template.jinja",
                "preprocessor_config.json",
                "processor_config.json",
                "video_preprocessor_config.json",
                "LICENSE",
                "NOTICE",
                "licenses/Apache-2.0.txt",
            ],
            ["scripts/quantize_cosmos3_nano_int4.py", "evaluation/performance_comparison.png"],
        ),
        (
            "model-build",
            cosmos.SOURCE_MODEL,
            cosmos.SOURCE_REVISION,
            [
                "transformer/diffusion_pytorch_model-00001-of-00007.safetensors",
                "transformer/diffusion_pytorch_model-00007-of-00007.safetensors",
                "transformer/diffusion_pytorch_model.safetensors.index.json",
                "vision_encoder/model.safetensors",
                "vision_encoder/config.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "merges.txt",
                "vocab.json",
                "preprocessor_config.json",
                "README.md",
            ],
            ["vae/diffusion_pytorch_model.safetensors", "assets/example.mp4"],
        ),
    ],
)
def test_model_recipe_keeps_weights_and_metadata_without_executing(
    action, repository, revision, required_files, excluded_files, tmp_path, monkeypatch, capsys
):
    def forbidden(*args, **kwargs):
        pytest.fail("Preview attempted a download, Docker command, or GPU work")

    monkeypatch.setattr(cosmos.subprocess, "run", forbidden)
    monkeypatch.setattr(cosmos.subprocess, "check_output", forbidden)
    monkeypatch.setattr(cosmos, "host_check", forbidden)
    monkeypatch.setattr(cosmos, "docker_access", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), action, "--work-dir", str(tmp_path / "model with spaces"), "--dry-run"],
    )
    cosmos.main()
    commands = [
        shlex.split(line)
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("hf download ")
    ]
    assert len(commands) == 1
    parsed = parse_hf_download(commands[0])
    assert parsed.repo == repository
    assert parsed.revision == revision
    assert parsed.filenames == [], "Positional filenames make hf ignore the include filters"
    for filename in required_files:
        assert any(fnmatch.fnmatch(filename, pattern) for pattern in parsed.include), filename
    for filename in excluded_files:
        assert not any(fnmatch.fnmatch(filename, pattern) for pattern in parsed.include), filename
    assert not list(tmp_path.iterdir())


@pytest.fixture
def build_args(tmp_path):
    args = cosmos.normalize(
        cosmos.parser().parse_args(["model-build", "--work-dir", str(tmp_path / "cosmos")])
    )
    args.work_dir.mkdir()
    return args


def test_foreign_conversion_output_is_preserved_before_any_stage(build_args):
    build_args.model_dir.mkdir()
    sentinel = build_args.model_dir / "my-model"
    sentinel.write_bytes(b"keep foreign data")
    with pytest.raises(ValueError, match="ownership receipt"):
        cosmos.BuildProgress(build_args)
    assert sentinel.read_bytes() == b"keep foreign data"
    assert not (build_args.work_dir / "public-build-progress.json").exists()


def test_interrupted_owned_quantization_preserves_both_outputs_before_retry(build_args):
    outputs = [build_args.model_dir, build_args.work_dir / "quantization-evaluation"]
    progress = cosmos.BuildProgress(build_args)
    assert progress.begin("quantization", outputs, lambda: False)
    for directory in outputs:
        directory.mkdir()
        (directory / "partial").write_bytes(directory.name.encode())
    resumed = cosmos.BuildProgress(build_args)
    assert resumed.begin("quantization", outputs, lambda: False)
    assert not any(path.exists() for path in outputs)
    preserved = resumed.data["preserved_attempts"]
    assert len(preserved) == 2
    assert {Path(entry["path"]).joinpath("partial").read_bytes() for entry in preserved} == {
        b"model",
        b"quantization-evaluation",
    }
    assert resumed.data["stages"]["quantization"]["status"] == "running"


def test_completed_verified_stage_is_reused_without_renaming(build_args):
    output = build_args.work_dir / "mmmu128_64"
    progress = cosmos.BuildProgress(build_args)
    assert progress.begin("calibration", [output], lambda: False)
    output.mkdir()
    (output / "keep").write_text("prepared data")
    progress.finish("calibration")
    resumed = cosmos.BuildProgress(build_args)
    assert not resumed.begin("calibration", [output], lambda: True)
    assert (output / "keep").read_text() == "prepared data"
    assert resumed.data["preserved_attempts"] == []


def test_corrupt_completed_stage_fails_without_replacing_data(build_args):
    output = build_args.work_dir / "mmmu128_64"
    progress = cosmos.BuildProgress(build_args)
    progress.begin("calibration", [output], lambda: False)
    output.mkdir()
    progress.finish("calibration")
    with pytest.raises(ValueError, match="failed verification"):
        cosmos.BuildProgress(build_args).begin("calibration", [output], lambda: False)
    assert output.is_dir()
    assert not list(output.parent.glob("mmmu128_64.failed-*"))


def test_changed_recipe_does_not_claim_existing_attempt(build_args):
    progress = cosmos.BuildProgress(build_args)
    output = build_args.work_dir / "mmmu128_64"
    progress.begin("calibration", [output], lambda: False)
    data = json.loads(progress.path.read_text())
    data["recipe"]["source_revision"] = "different-source"
    progress.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="does not match"):
        cosmos.BuildProgress(build_args)


def test_owned_path_replaced_with_symlink_is_not_archived(build_args, tmp_path):
    output = build_args.work_dir / "mmmu128_64"
    progress = cosmos.BuildProgress(build_args)
    progress.begin("calibration", [output], lambda: False)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    output.symlink_to(foreign, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinked"):
        cosmos.BuildProgress(build_args).begin("calibration", [output], lambda: False)
    assert output.is_symlink()
    assert foreign.is_dir()


@pytest.fixture
def tiny_extraction(tmp_path, monkeypatch):
    import hashlib
    import shutil
    import struct

    fake_repo = tmp_path / "fixture-repo"
    helper_dir = fake_repo / "scripts/cosmos"
    helper_dir.mkdir(parents=True)
    shutil.copyfile(
        cosmos.REPO / "scripts/cosmos/extract_cosmos3_nano_reasoner.py",
        helper_dir / "extract_cosmos3_nano_reasoner.py",
    )
    (helper_dir / "cosmos3_nano_extraction_reference.json").write_text(
        json.dumps({"qwen_config": {"text_config": {}}, "canonical_tensor_names": ["weight"]})
    )
    monkeypatch.setattr(cosmos, "REPO", fake_repo)
    source = tmp_path / "source"
    output = tmp_path / "extracted"
    source.mkdir()
    output.mkdir()
    (source / "config.json").write_text(json.dumps({"text_config": {}}))
    (source / "model.safetensors.index.json").write_text("{}")
    (output / "config.json").write_text(
        json.dumps(
            {
                "text_config": {},
                "architectures": ["Qwen3VLForConditionalGeneration"],
                "model_type": "qwen3_vl",
            }
        )
    )
    header = json.dumps({"weight": {"dtype": "BF16", "shape": [1], "data_offsets": [0, 2]}})
    weight_bytes = b"\x00\x3f"
    shard = output / "model-00001-of-00001.safetensors"
    shard.write_bytes(struct.pack("<Q", len(header)) + header.encode() + weight_bytes)
    (output / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": {"weight": shard.name}})
    )
    metadata = {}
    for name in (
        "tokenizer.json",
        "tokenizer_config.json",
        "merges.txt",
        "chat_template.json",
        "generation_config.json",
        "preprocessor_config.json",
    ):
        (output / name).write_text("{}")
        metadata[name] = cosmos.file_digest(output / name)
    (output / "extraction_manifest.json").write_text(
        json.dumps(
            {
                "source_model": cosmos.SOURCE_MODEL,
                "source_config_sha256": cosmos.file_digest(source / "config.json"),
                "source_index_sha256": cosmos.file_digest(source / "model.safetensors.index.json"),
                "tensor_count": 1,
                "tensors": {
                    "weight": {
                        "dtype": "BF16",
                        "shape": [1],
                        "nbytes": 2,
                        "sha256": hashlib.sha256(weight_bytes).hexdigest(),
                    }
                },
                "metadata_sha256": metadata,
                "validation": {
                    "canonical_key_and_shape_coverage": "passed",
                    "tensor_bytes_sha256": "passed",
                },
            }
        )
    )
    return source, output, shard


def test_completed_extraction_reuse_verifies_model_bytes(tiny_extraction):
    source, output, shard = tiny_extraction
    assert cosmos.extraction_valid(output, source)
    data = shard.read_bytes()
    shard.write_bytes(data[:-1] + b"\x00")
    assert not cosmos.extraction_valid(output, source)


def test_completed_extraction_reuse_verifies_tokenizer_metadata(tiny_extraction):
    source, output, _ = tiny_extraction
    assert cosmos.extraction_valid(output, source)
    (output / "tokenizer.json").write_text('{"modified": true}')
    assert not cosmos.extraction_valid(output, source)


@pytest.fixture
def checkpoint_fixture(build_args):
    build_args.model_dir.mkdir()
    for name in (
        "config.json",
        "hf_quant_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "preprocessor_config.json",
        "video_preprocessor_config.json",
    ):
        (build_args.model_dir / name).write_text("{}")
    (build_args.model_dir / "model.safetensors").write_bytes(b"fixture weights")
    return build_args


def test_checkpoint_inspection_publishes_fresh_receipt_and_detects_changes(
    checkpoint_fixture, monkeypatch
):
    args = checkpoint_fixture
    previous = args.work_dir / "checkpoint-inspection.json"
    previous.write_text('{"structural_inspection":"failed"}')
    commands = []

    def inspect(command, _args):
        commands.append(command)
        report = args.work_dir / Path(command[command.index("--report") + 1]).name
        assert report != previous
        assert not report.exists()
        report.write_text('{"structural_inspection":"passed"}')

    monkeypatch.setattr(cosmos, "execute", inspect)
    cosmos.inspect_checkpoint(args, ["docker"])
    assert cosmos.checkpoint_inspected(args)
    assert len(commands) == 1
    (args.model_dir / "tokenizer.json").write_text('{"new":1}')
    assert not cosmos.checkpoint_inspected(args)


def test_failed_inspection_keeps_previous_complete_receipt(checkpoint_fixture, monkeypatch):
    args = checkpoint_fixture
    previous = args.work_dir / "checkpoint-inspection.json"
    previous.write_text('{"structural_inspection":"passed","retain":"old receipt"}')
    original = previous.read_bytes()

    def failed(command, _args):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(cosmos, "execute", failed)
    with pytest.raises(subprocess.CalledProcessError):
        cosmos.inspect_checkpoint(args, ["docker"])
    assert previous.read_bytes() == original


def test_completed_checkpoint_does_not_repeat_download_or_conversion(
    checkpoint_fixture, monkeypatch
):
    args = checkpoint_fixture
    cosmos.save_json(
        args.work_dir / "checkpoint-inspection.json",
        {
            "structural_inspection": "passed",
            "local_checkpoint": str(args.model_dir),
            "local_files": cosmos.checkpoint_identity(args.model_dir),
        },
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Completed model unexpectedly requested downloads or conversion")

    monkeypatch.setattr(cosmos, "execute", forbidden)
    cosmos.model_build(args, ["docker"])


def test_model_check_preview_uses_inspector_without_download_or_gpu(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        [str(SCRIPT), "model-check", "--work-dir", str(tmp_path / "cosmos"), "--dry-run"],
    )
    cosmos.main()
    output = capsys.readouterr().out
    assert "inspect_cosmos3_nano_int4.py" in output
    assert "hf download" not in output
    assert "--gpus" not in output
    assert not list(tmp_path.iterdir())


def test_invalid_ownership_receipt_type_fails_without_touching_outputs(build_args):
    receipt = build_args.work_dir / "public-build-progress.json"
    receipt.write_text("[]")
    with pytest.raises(ValueError, match="Invalid public build receipt"):
        cosmos.BuildProgress(build_args)
    assert receipt.read_text() == "[]"


def test_interrupted_source_download_leaves_public_resume_identity(build_args, monkeypatch):
    attempted = []

    def interrupted_download(command, _args):
        attempted.append(command)
        assert command[:3] == ["hf", "download", cosmos.SOURCE_MODEL]
        receipt = build_args.work_dir / "public-build-progress.json"
        assert receipt.is_file(), "Public ownership must be recorded before source download"
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(cosmos, "execute", interrupted_download)
    with pytest.raises(subprocess.CalledProcessError):
        cosmos.model_build(build_args, ["docker"])
    assert len(attempted) == 1
    resumed = cosmos.BuildProgress(build_args)
    assert resumed.path.is_file()
    assert resumed.data["recipe"]["source_revision"] == cosmos.SOURCE_REVISION
    assert resumed.data["recipe"]["model_dir"] == str(build_args.model_dir)
    assert resumed.data["stages"] == {}
