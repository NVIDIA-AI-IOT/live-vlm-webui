#!/usr/bin/env python3
"""Install or build Cosmos on this Thor from pinned source dependencies."""

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
EDGE_COMMIT = "95515c2f87fba8982db5a519f9022277667b3cc9"
SOURCE_MODEL = "nvidia/Cosmos3-Nano"
SOURCE_REVISION = "e59a53c25979a090fa8706c9acc0c254a6e89b92"
CALIBRATION_REVISION = "364f2e2eb107b36e07ff4c5a15f5947a759cef47"
CHECKPOINT_REPO = "adsahu-nv/Cosmos3-Nano-Reasoner-INT4-AWQ"
CHECKPOINT_REVISION = "80a3d4750b9d8b4674cc13342c58bb5c1d308184"
CHECKPOINT_SHA256 = "0f2e1c7756869adcac92652d0d39a0b2196e8ede3d089c837418860f8a0c0b9d"
EDGE_WHEEL = "tensorrt_edgellm-0.11.0-cp312-cp312-manylinux_2_39_aarch64.whl"
EDGE_WHEEL_URL = (
    "https://files.pythonhosted.org/packages/c2/07/"
    "32efeb227b4b4c952b051ab9b990b21146756296b5ee65450de827cc5072/" + EDGE_WHEEL
)
EDGE_WHEEL_SHA256 = "9e6722a5a250d0ba18665911a908aecfc9684722b9d56d944efe31fe6c889d9b"
TORCH_WHEELS = (
    "https://download-r2.pytorch.org/whl/cu130/torch-2.13.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=5ebd552c887e707c8e64927aceb8377ca2e81588c4e7494bcd23cb8ac0aca14d",
    "https://download-r2.pytorch.org/whl/cu130/torchvision-0.28.0%2Bcu130-cp312-cp312-manylinux_2_28_aarch64.whl#sha256=ecf72161734b0cf75aabeb2a83101ee021ba8a9cfea57b40050deed7accd4615",
)
HELPERS = "/app/scripts/cosmos"


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument(
        "action",
        choices=("runtime", "model-download", "model-build", "model-check", "check", "serve"),
    )
    result.add_argument(
        "--work-dir",
        type=Path,
        default=Path(os.environ.get("LIVE_VLM_MODELS_DIR", REPO / ".models/cosmos")),
    )
    result.add_argument(
        "--runtime-prefix",
        type=Path,
        default=Path(os.environ.get("LIVE_VLM_TRT_RUNTIME_PREFIX", REPO / ".models/thor-runtime")),
    )
    result.add_argument(
        "--cuda-dir", type=Path, help="Override CUDA directory under the runtime prefix"
    )
    result.add_argument("--model-dir", type=Path, help="Checkpoint path; default: WORK/model")
    result.add_argument("--cache-dir", type=Path, help="Engine cache; default: WORK/engine-cache")
    result.add_argument(
        "--image", default=os.environ.get("LIVE_VLM_COSMOS_IMAGE", "live-vlm-cosmos:thor")
    )
    result.add_argument("--host", default=os.environ.get("LIVE_VLM_COSMOS_HOST", "127.0.0.1"))
    result.add_argument(
        "--port", type=int, default=int(os.environ.get("LIVE_VLM_COSMOS_PORT", "8012"))
    )
    result.add_argument(
        "--name", default=os.environ.get("LIVE_VLM_COSMOS_NAME", "live-vlm-cosmos-edge")
    )
    result.add_argument(
        "--model-repo",
        default=CHECKPOINT_REPO,
        help="Download action only; default repository is private",
    )
    result.add_argument(
        "--revision", default=CHECKPOINT_REVISION, help="Pinned model-download revision"
    )
    result.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the plan without downloads, Docker access or writes",
    )
    return result


def normalize(args):
    args.work_dir = args.work_dir.expanduser().resolve()
    args.runtime_prefix = args.runtime_prefix.expanduser().resolve()
    args.cuda_dir = (
        (args.cuda_dir or args.runtime_prefix / "usr/local/cuda-13.2").expanduser().resolve()
    )
    args.model_dir = (args.model_dir or args.work_dir / "model").expanduser().resolve()
    args.cache_dir = (args.cache_dir or args.work_dir / "engine-cache").expanduser().resolve()
    if not 1 <= args.port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    import re

    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", args.name):
        raise ValueError("Invalid Docker container name")
    for value in (
        args.work_dir,
        args.runtime_prefix,
        args.cuda_dir,
        args.model_dir,
        args.cache_dir,
        REPO,
    ):
        if any(character in str(value) for character in (":", "\n", "\r")):
            raise ValueError("Mount paths cannot contain colons, newlines or carriage returns")
    if not args.image or not args.host:
        raise ValueError("Image and host must not be empty")
    if args.cache_dir == args.model_dir or args.cache_dir.is_relative_to(args.model_dir):
        raise ValueError("Engine cache must be outside the read-only checkpoint directory")
    return args


def host_check():
    if platform.machine() != "aarch64":
        raise ValueError("This native setup requires aarch64 Jetson Thor")
    model = Path("/proc/device-tree/model").read_text().lower()
    release = Path("/etc/nv_tegra_release").read_text()
    boot = Path("/etc/nv_boot_control.conf").read_text().lower()
    if "thor" not in model or "# R39 (release), REVISION: 2.1," not in release:
        raise ValueError("Validated native target is Jetson Thor with L4T 39.2.1")
    if "t4000" in boot or not ("t5000" in boot or "jetson-agx-thor-devkit" in boot):
        raise ValueError("This recipe targets Thor T5000; another GPU requires separate validation")


def docker_access():
    for command in (["docker"], ["sudo", "-n", "docker"]):
        if (
            shutil.which(command[0])
            and subprocess.run(
                command + ["info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            == 0
        ):
            runtimes = subprocess.check_output(
                command + ["info", "--format", "{{json .Runtimes}}"], text=True
            )
            if "nvidia" not in json.loads(runtimes):
                raise ValueError("Docker NVIDIA runtime is not configured")
            return command
    raise ValueError("Docker is unavailable or permission was denied")


def execute(command, args, *, cwd=None, env=None):
    print(shlex.join([str(x) for x in command]), flush=True)
    if not args.dry_run:
        subprocess.run([str(x) for x in command], check=True, cwd=cwd, env=env)


def container(args, docker, command, *, gpu=False, serving=False, mounts=()):
    result = docker + [
        "run",
        "--rm",
        "--init",
        "--pull",
        "never",
        "--network",
        "host",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--shm-size",
        "8g",
    ]
    if gpu:
        result += ["--runtime=nvidia", "--gpus", "all"]
    if serving:
        result += ["--name", args.name]
        if os.environ.get("LIVE_VLM_CONTAINER_CIDFILE"):
            result += ["--cidfile", os.environ["LIVE_VLM_CONTAINER_CIDFILE"]]
    result += [
        "--volume",
        f"{REPO}:/app:ro",
        "--volume",
        f"{args.work_dir}:/cosmos",
        "--volume",
        f"{args.runtime_prefix}:/runtime:ro",
        "--volume",
        f"{args.cuda_dir}:/runtime-cuda:ro",
        "--volume",
        "/etc/nv_tegra_release:/etc/nv_tegra_release:ro",
        "--env",
        "PYTHONPATH=/runtime/usr/lib/python3.12/dist-packages:/runtime/usr/lib/python3/dist-packages",
        "--env",
        "LD_LIBRARY_PATH=/runtime/usr/lib/aarch64-linux-gnu:/runtime-cuda/lib64:/usr/local/cuda/lib64:/usr/local/nvidia/lib:/usr/local/nvidia/lib64",
        "--env",
        "PYTHONDONTWRITEBYTECODE=1",
        "--env",
        "HF_HOME=/cosmos/hf-cache",
        "--env",
        "XDG_CACHE_HOME=/cosmos/cache",
        "--env",
        "TORCH_EXTENSIONS_DIR=/cosmos/torch-extensions",
    ]
    for source, target, readonly in mounts:
        result += ["--volume", f"{source}:{target}" + (":ro" if readonly else "")]
    # The venv entrypoint is explicit; no shell evaluation of user paths.
    return result + ["--entrypoint", command[0], args.image, *command[1:]]


def ensure_image(args, docker):
    if args.dry_run:
        return
    image = subprocess.check_output(
        docker + ["image", "inspect", args.image, "--format", "{{.Architecture}}"], text=True
    ).strip()
    if image != "arm64":
        raise ValueError("Prepared Cosmos image must be ARM64; run the runtime setup first")


def runtime_setup(args, docker):
    execute(
        [
            sys.executable,
            REPO / "scripts/prepare_segmentation_runtime.py",
            "--prefix",
            args.runtime_prefix,
        ],
        args,
    )
    execute(
        docker
        + [
            "build",
            "--file",
            str(REPO / "docker/Dockerfile.cosmos-thor"),
            "--tag",
            args.image,
            str(REPO),
        ],
        args,
    )
    source = args.work_dir / "TensorRT-Edge-LLM"
    if args.dry_run or not source.exists():
        execute(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                "v0.11.0",
                "https://github.com/NVIDIA/TensorRT-Edge-LLM.git",
                source,
            ],
            args,
        )
    if not args.dry_run:
        commit = subprocess.check_output(
            ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
        ).strip()
        if commit != EDGE_COMMIT:
            raise ValueError("Edge source commit differs from the pinned runtime")
    venv_python = "/cosmos/edge-venv/bin/python"
    venv_dir = args.work_dir / "edge-venv"
    venv_ready = False
    if not args.dry_run and venv_dir.exists():
        # Host-side symlinks alone cannot prove the container's Python/pip work.
        venv_ready = (
            subprocess.run(
                container(args, docker, [venv_python, "-m", "pip", "--version"]),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=60,
                check=False,
            ).returncode
            == 0
        )
    if args.dry_run or not venv_ready:
        if not args.dry_run:
            owner = args.work_dir / "edge-venv-install.json"
            recipe = {
                "installer": "live-vlm-webui-edge-venv-v1",
                "directory": str(venv_dir),
                "source_commit": EDGE_COMMIT,
            }
            owned = owner.is_file() and json.loads(owner.read_text()) == recipe
            # Recognize the exact command written by earlier versions of this
            # installer, which did not yet publish an ownership receipt.
            legacy_owned = False
            config = venv_dir / "pyvenv.cfg"
            if config.is_file():
                fields = dict(
                    line.split(" = ", 1)
                    for line in config.read_text().splitlines()
                    if " = " in line
                )
                command = shlex.split(fields.get("command", ""))
                legacy_owned = (
                    not owner.exists()
                    and command
                    == [
                        "/usr/bin/python3.12",
                        "-m",
                        "venv",
                        "--system-site-packages",
                        "/cosmos/edge-venv",
                    ]
                    and fields.get("include-system-site-packages") == "true"
                )
            if venv_dir.is_symlink() or (
                (venv_dir.exists() or owner.exists()) and not (owned or legacy_owned)
            ):
                raise ValueError(
                    f"Incomplete unrecognized environment at {venv_dir}; files were preserved. "
                    "Choose another Cosmos work directory or repair that environment separately."
                )
            # Record ownership before venv writes its first file, so interruption
            # before ensurepip completes is repairable by the same command.
            save_json(owner, recipe)
        execute(
            container(
                args,
                docker,
                ["python3.12", "-m", "venv", "--system-site-packages", "/cosmos/edge-venv"],
            ),
            args,
        )
    # Direct wheel URL and hash pin prevent silent substitution of another payload.
    execute(
        container(
            args,
            docker,
            [venv_python, "-m", "pip", "install", EDGE_WHEEL_URL + "#sha256=" + EDGE_WHEEL_SHA256],
        ),
        args,
    )
    execute(
        container(
            args,
            docker,
            [venv_python, "-m", "pip", "install", "-r", HELPERS + "/requirements-runtime.txt"],
        ),
        args,
    )
    module = (
        args.work_dir
        / "native-compat/_edgellm_cache_config_compat.cpython-312-aarch64-linux-gnu.so"
    )
    if args.dry_run or not module.exists():
        execute(
            container(
                args,
                docker,
                [
                    venv_python,
                    HELPERS + "/build_encoder_cache_compat.py",
                    "--edge-source",
                    "/cosmos/TensorRT-Edge-LLM",
                    "--output-dir",
                    "/cosmos/native-compat",
                ],
            ),
            args,
        )
    execute(
        container(
            args,
            docker,
            [
                venv_python,
                HELPERS + "/check_encoder_cache_compat.py",
                "--compat-dir",
                "/cosmos/native-compat",
                "--report",
                "/cosmos/encoder-cache-config-check.json",
            ],
            gpu=True,
        ),
        args,
    )


def hf_download_command(repo_id, revision, destination, patterns):
    # hf accepts one pattern per --include. Bare extra patterns are positional
    # filenames and make the CLI ignore the include filter (dropping weights).
    return [
        "hf",
        "download",
        repo_id,
        "--revision",
        revision,
        "--local-dir",
        destination,
        *(value for pattern in patterns for value in ("--include", pattern)),
    ]


def file_digest(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def save_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    try:
        temporary.write_text(json.dumps(data, indent=2) + "\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def checkpoint_files(directory):
    metadata = (
        "config.json",
        "hf_quant_config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "preprocessor_config.json",
        "video_preprocessor_config.json",
    )
    files = [directory / name for name in metadata]
    files.extend(sorted(directory.glob("*.safetensors")))
    if len(files) == len(metadata) or not all(path.is_file() for path in files):
        raise ValueError("Checkpoint weights or required tokenizer/processor metadata are missing")
    # Track optional generation/chat-template metadata too when the model supplies it.
    return sorted(set(files) | set(directory.glob("*.json")) | set(directory.glob("*.jinja")))


def checkpoint_identity(directory):
    return {
        path.name: {
            "size": path.stat().st_size,
            "mtime_ns": path.stat().st_mtime_ns,
            **({"sha256": file_digest(path)} if path.suffix != ".safetensors" else {}),
        }
        for path in checkpoint_files(directory)
    }


def checkpoint_inspected(args):
    """Cheap completed-setup check; serving still validates engines on this GPU."""
    try:
        receipt = json.loads((args.work_dir / "checkpoint-inspection.json").read_text())
        return (
            receipt.get("structural_inspection") == "passed"
            and receipt.get("local_checkpoint") == str(args.model_dir)
            and receipt.get("local_files") == checkpoint_identity(args.model_dir)
        )
    except (OSError, ValueError, TypeError):
        return False


def inspect_checkpoint(args, docker, python="/cosmos/edge-venv/bin/python"):
    # Inspection deliberately refuses overwrite, so publish a fresh successful report.
    if not args.dry_run:
        checkpoint_files(args.model_dir)
    name = ".checkpoint-inspection-" + uuid.uuid4().hex + ".json"
    execute(
        container(
            args,
            docker,
            [
                python,
                HELPERS + "/inspect_cosmos3_nano_int4.py",
                "--checkpoint",
                "/checkpoint",
                "--report",
                "/cosmos/" + name,
            ],
            mounts=[(args.model_dir, "/checkpoint", True)],
        ),
        args,
    )
    if not args.dry_run:
        report = json.loads((args.work_dir / name).read_text())
        if report.get("structural_inspection") != "passed":
            raise ValueError("Checkpoint structural inspection did not pass")
        report.update(
            local_checkpoint=str(args.model_dir), local_files=checkpoint_identity(args.model_dir)
        )
        save_json(args.work_dir / "checkpoint-inspection.json", report)


def extraction_valid(directory, source):
    """Verify previously extracted tensors against the completed extraction manifest."""
    helper = REPO / "scripts/cosmos/extract_cosmos3_nano_reasoner.py"
    spec = importlib.util.spec_from_file_location("cosmos_resume_extraction", helper)
    extraction = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(extraction)
    try:
        manifest = extraction.read_json(directory / "extraction_manifest.json")
        if (directory / "EXTRACTION_INCOMPLETE.json").exists():
            return False
        if manifest.get("source_model") != SOURCE_MODEL:
            return False
        if any(
            manifest.get("validation", {}).get(key) != "passed"
            for key in ("canonical_key_and_shape_coverage", "tensor_bytes_sha256")
        ):
            return False
        for filename, key in (
            ("config.json", "source_config_sha256"),
            ("model.safetensors.index.json", "source_index_sha256"),
        ):
            if file_digest(source / filename) != manifest.get(key):
                return False
        reference = extraction.read_json(extraction.REFERENCE)
        source_config = extraction.read_json(source / "config.json")
        expected_config = {
            key: source_config[key] for key in reference["qwen_config"] if key in source_config
        }
        expected_config.update(
            architectures=["Qwen3VLForConditionalGeneration"], model_type="qwen3_vl"
        )
        if extraction.read_json(directory / "config.json") != expected_config:
            return False
        mapping = extraction.read_json(directory / "model.safetensors.index.json")["weight_map"]
        tensors = manifest["tensors"]
        if (
            not mapping
            or set(mapping) != set(tensors)
            or set(mapping) != set(reference["canonical_tensor_names"])
            or manifest.get("tensor_count") != len(mapping)
            or not set(extraction.METADATA_FILES).issubset(manifest["metadata_sha256"])
        ):
            return False
        for filename in sorted(set(mapping.values())):
            path = extraction.contained_file(directory, filename)
            header, offset = extraction.read_header(path)
            if set(header) != {name for name, shard in mapping.items() if shard == filename}:
                return False
            with path.open("rb") as weights:
                for name, tensor in header.items():
                    record = tensors[name]
                    size = tensor["data_offsets"][1] - tensor["data_offsets"][0]
                    if (
                        tensor["shape"] != record["shape"]
                        or tensor["dtype"] != record["dtype"]
                        or size != record["nbytes"]
                    ):
                        return False
                    weights.seek(offset + tensor["data_offsets"][0])
                    if extraction.copy_tensor(weights, None, size) != record["sha256"]:
                        return False
        for filename, expected in manifest["metadata_sha256"].items():
            if file_digest(extraction.contained_file(directory, filename)) != expected:
                return False
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def calibration_valid(directory):
    try:
        manifest = json.loads((directory / "data_manifest.json").read_text())
        if (directory / "PREPARATION_INCOMPLETE").exists() or manifest.get(
            "revision"
        ) != CALIBRATION_REVISION:
            return False
        for split, count in (("calibration", 128), ("heldout", 64)):
            path = directory / (split + ".jsonl")
            if file_digest(path) != manifest.get(split + "_jsonl_sha256"):
                return False
            rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            if len(rows) != count:
                return False
            for row in rows:
                image = directory / row["image"]
                if not image.resolve().is_relative_to(directory.resolve()) or not image.is_file():
                    return False
        # The quantizer separately verifies decoded image hashes before calibration.
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def quantization_written(args):
    try:
        report = json.loads(
            (args.work_dir / "quantization-evaluation/quantization_run.json").read_text()
        )
        status = json.loads((args.model_dir / "quantization_validation_status.json").read_text())
        checkpoint_files(args.model_dir)
        return (
            report.get("status") == status.get("status") == "weights_written_accuracy_not_certified"
        )
    except (OSError, ValueError, TypeError):
        return False


class BuildProgress:
    """Record ownership before a stage creates output; never move foreign files."""

    def __init__(self, args):
        self.path = args.work_dir / "public-build-progress.json"
        self.recipe = {
            "owner": "live-vlm-webui-public-cosmos-v1",
            "source_revision": SOURCE_REVISION,
            "calibration_revision": CALIBRATION_REVISION,
            "edge_commit": EDGE_COMMIT,
            "work_dir": str(args.work_dir),
            "model_dir": str(args.model_dir),
        }
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
            if not isinstance(self.data, dict):
                raise ValueError("Invalid public build receipt; existing files were preserved")
            if self.data.get("recipe") != self.recipe:
                raise ValueError(
                    "Public build receipt does not match these paths/recipe; existing files were preserved"
                )
        else:
            self.data = {"recipe": self.recipe, "stages": {}, "preserved_attempts": []}
        if not isinstance(self.data.get("stages"), dict) or not isinstance(
            self.data.get("preserved_attempts"), list
        ):
            raise ValueError(  # noqa: TRY004 - invalid external receipt, not a caller type error
                "Invalid public build progress receipt; existing files were preserved"
            )
        for record in self.data["stages"].values():
            if not isinstance(record, dict) or record.get("status") not in ("running", "complete"):
                raise ValueError(
                    "Invalid public build stage receipt; existing files were preserved"
                )
        expected = {
            "extraction": [
                args.work_dir / "reasoner-bf16",
                args.work_dir / "reasoner-bf16.extracting",
            ],
            "calibration": [args.work_dir / "mmmu128_64"],
            "quantization": [args.model_dir, args.work_dir / "quantization-evaluation"],
        }
        for stage, outputs in expected.items():
            existing = [path for path in outputs if path.exists() or path.is_symlink()]
            record = self.data["stages"].get(stage)
            if existing and (
                not isinstance(record, dict)
                or record.get("paths") != [str(path) for path in outputs]
            ):
                raise ValueError(
                    f"Existing {stage} outputs lack this installer's ownership receipt; existing files were preserved: {existing}"
                )

    def begin(self, stage, outputs, valid):
        paths = [str(path) for path in outputs]
        record = self.data["stages"].get(stage)
        existing = [path for path in outputs if path.exists() or path.is_symlink()]
        if record is not None and record.get("paths") != paths:
            raise ValueError("Stage paths changed; existing conversion outputs were preserved")
        if existing:
            if record is None:
                raise ValueError(
                    f"Refusing to alter existing {stage} outputs without an installer ownership receipt: {existing}"
                )
            if any(path.is_symlink() or path.resolve() != path for path in existing):
                raise ValueError("Refusing to move symlinked conversion output")
            if valid():
                self.finish(stage)
                print(f"Reusing verified {stage} output.", flush=True)
                return False
            if record.get("status") == "complete":
                raise ValueError(
                    f"Completed {stage} output failed verification; existing files were preserved"
                )
            for path in existing:
                backup = path.with_name(path.name + ".failed-" + uuid.uuid4().hex)
                path.rename(backup)
                self.data["preserved_attempts"].append({"stage": stage, "path": str(backup)})
                save_json(self.path, self.data)
                print(f"Preserved incomplete {stage} output at {backup}", flush=True)
        self.data["stages"][stage] = {"paths": paths, "status": "running"}
        save_json(self.path, self.data)
        return True

    def finish(self, stage):
        self.data["stages"][stage]["status"] = "complete"
        save_json(self.path, self.data)


def model_download(args, docker):
    print("The default INT4 repository is private; download requires an HF account granted access.")
    execute(
        hf_download_command(
            args.model_repo,
            args.revision,
            args.model_dir,
            ("*.safetensors", "*.json", "*.jinja", "LICENSE", "NOTICE", "licenses/*"),
        ),
        args,
    )
    if (
        not args.dry_run
        and args.model_repo == CHECKPOINT_REPO
        and args.revision == CHECKPOINT_REVISION
    ):
        with (args.model_dir / "model.safetensors").open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != CHECKPOINT_SHA256:
                raise ValueError("Downloaded INT4 checkpoint checksum mismatch")
    inspect_checkpoint(args, docker)


def model_build(args, docker):
    if args.dry_run:
        return resume_model_build(args, docker)
    with (args.work_dir / ".public-build.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(
                "Another public model build is active in this work directory"
            ) from error
        return resume_model_build(args, docker)


def resume_model_build(args, docker):
    print(
        "Explicit rebuild: downloads about 31.5 GB of source weights, extracts 17.5 GB, then runs GPU AWQ calibration. Allow at least 100 GB free disk."
    )
    if not args.dry_run and checkpoint_inspected(args):
        print("Reusing the inspected local Cosmos checkpoint.", flush=True)
        return
    progress = None if args.dry_run else BuildProgress(args)
    if progress is not None and not progress.path.exists():
        # Keep the selected public recipe across interruption during source download.
        save_json(progress.path, progress.data)
    execute(
        hf_download_command(
            SOURCE_MODEL,
            SOURCE_REVISION,
            args.work_dir / "source-nano",
            (
                "transformer/*",
                "vision_encoder/*",
                "config.json",
                "model.safetensors.index.json",
                "tokenizer.json",
                "tokenizer_config.json",
                "merges.txt",
                "vocab.json",
                "special_tokens_map.json",
                "added_tokens.json",
                "chat_template.json",
                "chat_template.jinja",
                "generation_config.json",
                "preprocessor_config.json",
                "video_preprocessor_config.json",
                "processor_config.json",
                "README.md",
                "LICENSE",
                "LICENSE.txt",
                "NOTICE",
                "licenses/*",
            ),
        ),
        args,
    )
    quant = "/cosmos/quant-venv/bin/python"
    execute(
        container(
            args,
            docker,
            ["python3.12", "-m", "venv", "--system-site-packages", "/cosmos/quant-venv"],
        ),
        args,
    )
    execute(container(args, docker, [quant, "-m", "pip", "install", *TORCH_WHEELS]), args)
    execute(
        container(
            args,
            docker,
            [quant, "-m", "pip", "install", "-r", HELPERS + "/requirements-quantization.txt"],
        ),
        args,
    )
    extracted = args.work_dir / "reasoner-bf16"
    if args.dry_run or progress.begin(
        "extraction",
        [extracted, extracted.with_name(extracted.name + ".extracting")],
        lambda: extraction_valid(extracted, args.work_dir / "source-nano"),
    ):
        execute(
            container(
                args,
                docker,
                [
                    quant,
                    HELPERS + "/extract_cosmos3_nano_reasoner.py",
                    "--source",
                    "/cosmos/source-nano",
                    "--output",
                    "/cosmos/reasoner-bf16",
                ],
            ),
            args,
        )
        if not args.dry_run:
            progress.finish("extraction")
    data_directory = args.work_dir / "mmmu128_64"
    if args.dry_run or progress.begin(
        "calibration", [data_directory], lambda: calibration_valid(data_directory)
    ):
        execute(
            container(
                args,
                docker,
                [
                    quant,
                    HELPERS + "/evaluate_cosmos3_nano_int4.py",
                    "prepare-data",
                    "--output",
                    "/cosmos/mmmu128_64",
                    "--calibration-count",
                    "128",
                    "--heldout-count",
                    "64",
                    "--revision",
                    CALIBRATION_REVISION,
                ],
            ),
            args,
        )
        if not args.dry_run:
            progress.finish("calibration")
    # Mount the output parent so the exporter can create, then atomically publish, the checkpoint directory.
    if not args.dry_run:
        args.model_dir.parent.mkdir(parents=True, exist_ok=True)
    output = "/output/" + args.model_dir.name
    if args.dry_run or progress.begin(
        "quantization",
        [args.model_dir, args.work_dir / "quantization-evaluation"],
        lambda: quantization_written(args),
    ):
        execute(
            container(
                args,
                docker,
                [
                    quant,
                    HELPERS + "/quantize_cosmos3_nano_int4.py",
                    "--repo",
                    "/cosmos/TensorRT-Edge-LLM",
                    "--source",
                    "/cosmos/reasoner-bf16",
                    "--output",
                    output,
                    "--data-dir",
                    "/cosmos/mmmu128_64",
                    "--report-dir",
                    "/cosmos/quantization-evaluation",
                    "--num-samples",
                    "128",
                    "--evaluation-limit",
                    "64",
                    "--max-new-tokens",
                    "256",
                    "--min-pixels",
                    "65536",
                    "--max-pixels",
                    "1048576",
                    "--seed",
                    "20261003",
                ],
                gpu=True,
                mounts=[(args.model_dir.parent, "/output", False)],
            ),
            args,
        )
    execute(
        container(
            args,
            docker,
            [quant, HELPERS + "/fix_processor_bounds.py", "--checkpoint", "/checkpoint"],
            mounts=[(args.model_dir, "/checkpoint", False)],
        ),
        args,
    )
    inspect_checkpoint(args, docker, quant)
    if not args.dry_run:
        progress.finish("quantization")


def serve_or_check(args, docker):
    python = "/cosmos/edge-venv/bin/python"
    if not args.dry_run:
        for path in (
            args.work_dir / "edge-venv/bin/python",
            args.work_dir
            / "native-compat/_edgellm_cache_config_compat.cpython-312-aarch64-linux-gnu.so",
            args.model_dir / "model.safetensors",
            args.runtime_prefix / "usr/lib/aarch64-linux-gnu/libnvinfer.so.10",
            args.cuda_dir / "lib64/libcudart.so.13",
        ):
            if not path.exists():
                raise ValueError(
                    f"Missing {path}; complete runtime setup and model-download or model-build first"
                )
    mounts = [(args.model_dir, "/checkpoint", True)]
    command = [
        python,
        HELPERS + "/serve_cosmos3_nano_edgellm.py",
        "--model-dir",
        "/checkpoint",
        "--cache-dir",
        "/engine-cache",
        "--encoder-cache-compat-dir",
        "/cosmos/native-compat",
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--served-model-name",
        "cosmos-nano-int4",
    ]
    if args.action == "check":
        execute(
            [
                sys.executable,
                REPO / "scripts/prepare_segmentation_runtime.py",
                "--prefix",
                args.runtime_prefix,
                "--check",
            ],
            args,
        )
        execute(
            container(
                args,
                docker,
                [
                    python,
                    "-c",
                    "import tensorrt as trt; assert trt.__version__ == '10.16.2.10', trt.__version__; print('TensorRT version verified')",
                ],
            ),
            args,
        )
        execute(
            container(
                args,
                docker,
                [
                    python,
                    HELPERS + "/check_encoder_cache_compat.py",
                    "--compat-dir",
                    "/cosmos/native-compat",
                ],
                gpu=True,
            ),
            args,
        )
        execute(container(args, docker, command + ["--dry-run"], mounts=mounts), args)
        print(
            "Checks cover platform, runtime ABI and model metadata; target engine build and inference still require a smoke test."
        )
    else:
        if not args.dry_run:
            if (
                subprocess.run(
                    docker + ["container", "inspect", args.name],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                ).returncode
                == 0
            ):
                raise ValueError(
                    f"Container {args.name} exists; keep it running or stop it explicitly"
                )
            args.cache_dir.mkdir(parents=True, exist_ok=True)
        print(
            "First startup builds fresh TensorRT engines for this target; later starts reuse matching local engines."
        )
        execute(
            container(
                args,
                docker,
                command,
                gpu=True,
                serving=True,
                mounts=mounts + [(args.cache_dir, "/engine-cache", False)],
            ),
            args,
        )


def main():
    args = normalize(parser().parse_args())
    if args.dry_run:
        docker = ["docker"]
    else:
        host_check()
        docker = docker_access()
        if args.action != "runtime":
            ensure_image(args, docker)
        if args.action in ("runtime", "model-download", "model-build"):
            args.work_dir.mkdir(parents=True, exist_ok=True)
    if args.action == "runtime":
        runtime_setup(args, docker)
    elif args.action == "model-download":
        model_download(args, docker)
    elif args.action == "model-build":
        model_build(args, docker)
    elif args.action == "model-check":
        inspect_checkpoint(args, docker)
    else:
        serve_or_check(args, docker)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        print(f"Cosmos setup: {error}", file=sys.stderr)
        sys.exit(1)
