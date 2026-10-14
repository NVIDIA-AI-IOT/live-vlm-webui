#!/usr/bin/env python3
"""Install, check and run the Thor demo from this repository checkout."""

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import fcntl
import importlib.util
import json
import os
import shlex
import shutil
import signal
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]


def _asset_helper(name):
    """Load repository helpers for CLI and importlib callers without a PYTHONPATH dependency."""
    spec = importlib.util.spec_from_file_location(
        "_thor_asset_" + name, Path(__file__).with_name(name + ".py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


exporter = _asset_helper("export_retail_segmentation")
native_runtime = _asset_helper("prepare_segmentation_runtime")
cosmos = _asset_helper("setup_cosmos_edge_thor")
SudoKeepalive = _asset_helper("sudo_session").SudoKeepalive


class KioskSetupError(RuntimeError):
    """An actionable failure while preparing kiosk assets."""


PATH_FIELDS = (
    "segmentation_model_dir",
    "segmentation_engine",
    "trt_runtime_prefix",
    "cuda_dir",
    "cosmos_dir",
)
OPTIONAL_PATHS = {"segmentation_engine", "trt_runtime_prefix", "cuda_dir"}
DEFAULTS = {
    "vlm_api_base": "http://127.0.0.1:8012/v1",
    "vlm_model": "cosmos-nano-int4",
    "vlm_backend": "edgellm",
    "segmentation_model_dir": ".models",
    "segmentation_engine": None,
    "trt_runtime_prefix": None,
    "cuda_dir": None,
    "cosmos_dir": ".models/cosmos",
    "ui_host": "0.0.0.0",
    "kiosk_lan_access": False,
    "ui_port": 8090,
    "cosmos_port": 8012,
    "segmentation_image": "live-vlm-webui:segmentation-thor",
    "demo_max_size": "640x360",
}


def read_config(path: Path) -> dict:
    path = path.resolve()
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or set(data) - set(DEFAULTS):
        raise ValueError("Config must use the keys in config/thor-demo.example.json")
    config = DEFAULTS | data
    if type(config["kiosk_lan_access"]) is not bool:
        raise ValueError("kiosk_lan_access must be a boolean")
    for name in ("ui_port", "cosmos_port"):
        if type(config[name]) is not int or not 1 <= config[name] <= 65535:
            raise ValueError(f"{name} must be an integer between 1 and 65535")
    if config["ui_port"] == config["cosmos_port"]:
        raise ValueError("UI and Cosmos must use different ports")
    for name in ("ui_host", "segmentation_image", "demo_max_size", "vlm_api_base", "vlm_model"):
        if not isinstance(config[name], str) or not config[name].strip() or "\n" in config[name]:
            raise ValueError(f"{name} must be a nonempty single-line string")
    url = urlsplit(config["vlm_api_base"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.fragment:
        raise ValueError("vlm_api_base must be an HTTP(S) URL without credentials or a fragment")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError("vlm_api_base has an invalid port")
    if config["vlm_backend"] not in ("openai", "edgellm"):
        raise ValueError("vlm_backend must be openai or edgellm")
    if config["demo_max_size"] not in ("640x360", "960x540"):
        raise ValueError("demo_max_size must be 640x360 or 960x540")
    if (config["trt_runtime_prefix"] is None) != (config["cuda_dir"] is None):
        raise ValueError("Set trt_runtime_prefix and cuda_dir together, or leave both null")
    for name in PATH_FIELDS:
        value = config[name]
        if value is None and name in OPTIONAL_PATHS:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a path")
        candidate = Path(value).expanduser()
        config[name] = str((path.parent / candidate).resolve())
    return config


def runtime_paths(config):
    prefix = config["trt_runtime_prefix"] or str(
        Path(config["segmentation_model_dir"]) / "thor-runtime"
    )
    cuda = config["cuda_dir"] or str(Path(prefix) / "usr/local/cuda-13.2")
    return prefix, cuda


def environment(config: dict, *, native=False) -> dict:
    # Empty optional values prevent a different shell profile silently selecting an engine.
    settings = {
        "LIVE_VLM_MODEL_DIR": config["segmentation_model_dir"],
        "LIVE_VLM_SEGMENTATION_ENGINE": config["segmentation_engine"] or "",
        "LIVE_VLM_SEGMENTATION_IMAGE": config["segmentation_image"],
        "LIVE_VLM_DEMO_MAX_SIZE": config["demo_max_size"],
        "LIVE_VLM_TRT_RUNTIME_PREFIX": "",
        "LIVE_VLM_TRT_CUDA_ROOT": "",
        "LIVE_VLM_TRT_IN_IMAGE": "0",
    }
    if native or config["segmentation_engine"]:
        prefix, cuda = runtime_paths(config)
        settings.update(LIVE_VLM_TRT_RUNTIME_PREFIX=prefix, LIVE_VLM_TRT_CUDA_ROOT=cuda)
    return settings


def launch_commands(
    action,
    config,
    repo=REPO,
    *,
    skip_build=False,
    check_cosmos=False,
    output=None,
    localhost_http=False,
    no_ssl=False,
):
    if localhost_http and no_ssl:
        raise ValueError("Choose only one of --localhost-http and --no-ssl")
    segmentation = ["bash", str(repo / "scripts/run_segmentation_thor.sh")]
    prefix, cuda = runtime_paths(config)
    cosmos = [
        "bash",
        str(repo / "scripts/run_cosmos_edge_thor.sh"),
        "--models-dir",
        config["cosmos_dir"],
        "--runtime-prefix",
        prefix,
        "--cuda-dir",
        cuda,
        "--port",
        str(config["cosmos_port"]),
    ]
    if action == "check":
        return [segmentation + ["--check"]] + ([cosmos + ["--check"]] if check_cosmos else [])
    if action == "prepare":
        return [segmentation + ["--prepare"]]
    if action == "runtime":
        return [
            ["python3", str(repo / "scripts/prepare_segmentation_runtime.py"), "--prefix", prefix]
        ]
    if action == "export-engine":
        target = output or str(Path(config["segmentation_model_dir"]) / "retail-480.engine")
        return [
            segmentation
            + ["--export-engine", str(Path(target).resolve())]
            + (["--skip-build"] if skip_build else [])
        ]
    if action == "cosmos-prepare":
        return [
            [
                "python3",
                str(repo / "scripts/setup_cosmos_edge_thor.py"),
                "runtime",
                "--work-dir",
                config["cosmos_dir"],
                "--runtime-prefix",
                prefix,
            ]
        ]
    if action == "cosmos":
        return [cosmos]
    if action == "demo-videos":
        return [["python3", str(repo / "scripts/download_retail_25fps.py")]]
    if action == "webui":
        return [
            segmentation
            + (["--skip-build"] if skip_build else [])
            + [
                "--host",
                config["ui_host"],
                "--port",
                str(config["ui_port"]),
                "--api-base",
                config["vlm_api_base"],
                "--model",
                config["vlm_model"],
                "--vlm-backend",
                config["vlm_backend"],
            ]
            + (["--localhost-http"] if localhost_http else [])
            + (["--no-ssl"] if no_ssl else [])
        ]
    raise ValueError(f"Unknown action: {action}")


def run_action(args, config, repo=REPO):
    overrides = environment(config, native=args.action == "export-engine")
    commands = launch_commands(
        args.action,
        config,
        repo,
        skip_build=getattr(args, "skip_build", False),
        check_cosmos=getattr(args, "cosmos", False),
        output=getattr(args, "output", None),
        localhost_http=getattr(args, "localhost_http", False),
        no_ssl=getattr(args, "no_ssl", False),
    )
    if args.dry_run:
        print(json.dumps({"environment": overrides, "commands": commands}, indent=2))
        return
    for command in commands:
        print(shlex.join(command), flush=True)
        subprocess.run(command, env=os.environ | overrides, cwd=repo, check=True)
    if args.action == "export-engine":
        print("Engine built. Set segmentation_engine in your config to its output path to use it.")


def init_config(path):
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x") as destination:
        json.dump(DEFAULTS, destination, indent=2)
        destination.write("\n")
    print(f"Created {path} with the local Thor demo defaults.")


HF_VERSION = "1.30.0"
HF_WHEEL = (
    "https://files.pythonhosted.org/packages/c3/0e/"
    "3e45bbe0dd48f4e56b1d46649d342de853cd1c7e815323472ab62687f153/"
    "huggingface_hub-1.30.0-py3-none-any.whl"
    "#sha256=96ae0a8e99a234374a6fe43e989ebd21c04640b91ab2927e7e5773ba1131ca59"
)


def load_config(path, *, dry_run=False):
    path = Path(path).expanduser().resolve()
    if path.exists():
        return read_config(path)
    if not dry_run:
        init_config(path)
        return read_config(path)
    config = dict(DEFAULTS)
    for key in PATH_FIELDS:
        if config[key] is not None:
            config[key] = str((path.parent / config[key]).resolve())
    return config


def local_cosmos(config):
    url = urlsplit(config["vlm_api_base"])
    return (
        config["vlm_backend"] == "edgellm"
        and config["vlm_model"] == "cosmos-nano-int4"
        and url.scheme == "http"
        and url.hostname in ("localhost", "127.0.0.1")
        and url.port == config["cosmos_port"]
        and url.path.rstrip("/") == "/v1"
    )


def child_process_options():
    # A separate process group lets cleanup target our children while retaining
    # the terminal session used by sudo's credential cache on Thor (Python 3.12).
    if sys.version_info >= (3, 11):
        return {"process_group": 0}
    return {"start_new_session": True}


def run_preparation(command, *, on_progress=None, progress_interval=30, **kwargs):
    """Run setup with optional periodic ticks and cleanup of its process group.

    ``on_progress()`` receives no command, environment, or subprocess output.
    An interrupted or failed callback uses the same cleanup as interrupted setup.
    """
    if on_progress is not None:
        if not callable(on_progress):
            raise TypeError("on_progress must be callable")
        if (
            isinstance(progress_interval, bool)
            or not isinstance(progress_interval, (int, float))
            or not 0 < progress_interval < float("inf")
        ):
            raise ValueError("progress_interval must be a positive finite number")
    child = subprocess.Popen(command, **child_process_options(), **kwargs)
    try:
        if on_progress is None:
            return subprocess.CompletedProcess(command, child.wait())
        while True:
            try:
                return subprocess.CompletedProcess(command, child.wait(timeout=progress_interval))
            except subprocess.TimeoutExpired:
                on_progress()
    except BaseException:
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
                child.wait(timeout=10)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
        raise


def validate_engine(path):
    """Reject truncated or unrelated engines without loading another GPU model."""
    try:
        with path.open("rb") as source:
            header = source.read(4)
            if len(header) != 4:
                raise ValueError("truncated header")
            size = struct.unpack("<I", header)[0]
            if not 0 < size < min(16 * 1024 * 1024, path.stat().st_size - 4):
                raise ValueError("invalid metadata size")
            metadata = json.loads(source.read(size))
        if not isinstance(metadata, dict):
            raise TypeError("metadata must be an object")
        exporter.validate_metadata(metadata)
        receipt = path.with_suffix(".json")
        if receipt.exists():
            report = json.loads(receipt.read_text())
            if (
                report.get("engine_sha256") != exporter.digest(path)
                or report.get("gpu_capability") != [11, 0]
                or report.get("tensorrt") != "10.16.2.10"
                or report.get("source_sha256") != exporter.CHECKPOINT_SHA256
            ):
                raise ValueError("export receipt does not match this engine or Thor runtime")
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise KioskSetupError(
            f"Invalid retail engine {path}: {error}. Existing files were preserved."
        ) from error


class KioskAssets:
    def __init__(self, args):
        self.args = args
        self.config_path = Path(args.config).expanduser().resolve()
        self.config = load_config(self.config_path)
        self.logs = (
            REPO
            / "artifacts/kiosk-setup"
            / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8])
        )
        self.env = dict(os.environ)
        self.docker = None
        self.sudo_session = None

    def step(self, name, command, *, config=None, native=False):
        self.logs.mkdir(parents=True, exist_ok=True)
        log = self.logs / f"{name}.log"
        print(f"{name.replace('-', ' ').capitalize()}… Log: {log}", flush=True)
        env = self.env | environment(config or self.config, native=native)
        started = time.monotonic()

        def progress():
            elapsed = int(time.monotonic() - started)
            print(
                f"  Still working: {name.replace('-', ' ')} ({elapsed // 60}m {elapsed % 60}s). "
                "Keep this terminal open.",
                flush=True,
            )

        with log.open("a") as output:
            result = run_preparation(
                [str(value) for value in command],
                cwd=REPO,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                on_progress=progress,
            )
        if result.returncode:
            raise KioskSetupError(
                f"{name} failed. See {log}. Fix the reported problem, then run "
                "the kiosk installer again; completed downloads and builds are reused."
            )

    def probe(self, command, *, config=None):
        return (
            run_preparation(
                [str(value) for value in command],
                cwd=REPO,
                env=self.env | environment(config or self.config),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).returncode
            == 0
        )

    def image_ready(self, name):
        result = subprocess.run(
            self.docker + ["image", "inspect", name, "--format", "{{.Architecture}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        return result.returncode == 0 and result.stdout.strip() == "arm64"

    def cosmos_args(self, action):
        prefix, cuda = runtime_paths(self.config)
        return cosmos.normalize(
            cosmos.parser().parse_args(
                [
                    action,
                    "--work-dir",
                    self.config["cosmos_dir"],
                    "--runtime-prefix",
                    prefix,
                    "--cuda-dir",
                    cuda,
                ]
            )
        )

    def cosmos_command(self, action):
        prefix, cuda = runtime_paths(self.config)
        return [
            sys.executable,
            REPO / "scripts/setup_cosmos_edge_thor.py",
            action,
            "--work-dir",
            self.config["cosmos_dir"],
            "--runtime-prefix",
            prefix,
            "--cuda-dir",
            cuda,
        ]

    def local_checkpoint_present(self):
        try:
            cosmos.checkpoint_files(self.cosmos_args("check").model_dir)
            return True
        except (OSError, ValueError):
            return False

    def ensure_hf(self):
        directory = Path(self.config["segmentation_model_dir"]) / "demo-hf-cli"
        python = directory / "bin/python"
        marker = directory / ".demo-cli.json"
        if directory.exists() and not marker.is_file():
            raise KioskSetupError(
                f"{directory} was not created by demo setup; move it or use another model directory."
            )
        if marker.exists():
            try:
                receipt = json.loads(marker.read_text())
                if (
                    receipt.get("installer") != "live-vlm-webui"
                    or receipt.get("version") != HF_VERSION
                ):
                    raise ValueError("unrecognized installer/version")
            except (OSError, ValueError, AttributeError) as error:
                raise KioskSetupError(
                    f"Invalid demo CLI ownership marker: {marker}; files were preserved."
                ) from error
        directory.mkdir(parents=True, exist_ok=True)
        if not marker.exists():
            marker.write_text(
                json.dumps({"installer": "live-vlm-webui", "version": HF_VERSION}) + "\n"
            )
        if not python.exists() or not self.probe([python, "-m", "pip", "--version"]):
            self.step(
                "create-model-download-environment", [sys.executable, "-m", "venv", directory]
            )
        if not self.probe(
            [
                python,
                "-c",
                f"import huggingface_hub; assert huggingface_hub.__version__ == {HF_VERSION!r}",
            ]
        ):
            self.step(
                "install-model-downloader",
                [python, "-m", "pip", "install", "--disable-pip-version-check", HF_WHEEL],
            )
        hf = directory / "bin/hf"
        if not hf.is_file():
            raise KioskSetupError(f"The Hugging Face CLI is missing from {directory}.")
        self.env["PATH"] = str(directory / "bin") + os.pathsep + self.env.get("PATH", "")
        return hf

    def verify_download_access(self, hf):
        automatic = self.args.cosmos_source == "auto"
        public = self.args.cosmos_source == "public"
        repo = cosmos.SOURCE_MODEL if public else cosmos.CHECKPOINT_REPO
        revision = cosmos.SOURCE_REVISION if public else cosmos.CHECKPOINT_REVISION
        weight = (
            "transformer/diffusion_pytorch_model-00001-of-00007.safetensors"
            if public
            else "model.safetensors"
        )
        command = [hf, "download", repo, "--revision", revision, weight, "--dry-run"]
        if self.probe(command):
            if automatic:
                self.args.cosmos_source = "checkpoint"
                print("Using the ready Cosmos INT4 model available to this machine.", flush=True)
            return
        if automatic:
            work = Path(self.config["cosmos_dir"])
            work.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(work).free < 100 * 1024**3:
                raise KioskSetupError(
                    f"Automatic Cosmos setup needs at least 100 GB free at {work} "
                    "to prepare the public NVIDIA model. Free space and rerun this script."
                )
            self.args.cosmos_source = "public"
            print(
                "Preparing Cosmos INT4 automatically from the public NVIDIA model. "
                "The first download and conversion can take a long time; later runs reuse it.\n"
                f"Model source: https://huggingface.co/{cosmos.SOURCE_MODEL}",
                flush=True,
            )
            return self.verify_download_access(hf)
        print(f"Model download requires access to https://huggingface.co/{repo}", flush=True)
        interactive = sys.stdin.isatty() and sys.stdout.isatty()
        while interactive and not public:
            print(
                "The ready-made checkpoint is private. Choose how to continue:\n"
                "  1) Sign in to an account with checkpoint access\n"
                "  2) Build INT4 from the public NVIDIA model (100 GB free; takes longer)\n"
                "  q) Cancel setup",
                flush=True,
            )
            try:
                choice = input("Choose [1/2/q, default 1]: ").strip().lower() or "1"
            except EOFError:
                choice = "q"
            if choice in ("q", "quit", "cancel"):
                raise KioskSetupError(
                    "Setup cancelled. Installed files were kept for the next run."
                )
            if choice == "2":
                work = Path(self.config["cosmos_dir"])
                work.mkdir(parents=True, exist_ok=True)
                if shutil.disk_usage(work).free < 100 * 1024**3:
                    raise KioskSetupError(
                        f"Public model conversion needs at least 100 GB free at {work}."
                    )
                self.args.cosmos_source = "public"
                print("Continuing this setup with the public NVIDIA model.", flush=True)
                return self.verify_download_access(hf)
            if choice != "1":
                print("Enter 1, 2, or q.", flush=True)
                continue
            print(
                "Sign in below. Your token is handled by hf, not stored in demo config.",
                flush=True,
            )
            # Keep the controlling terminal for the CLI's hidden token prompt.
            result = subprocess.run([str(hf), "auth", "login"], env=self.env, check=False)
            if result.returncode == 0 and self.probe(command):
                return
            print(
                "The checkpoint is still unavailable. Signing in does not grant access.", flush=True
            )
        if interactive and public:
            print(
                "Could not reach the public model. Check the network and the model link above.",
                flush=True,
            )
        alternative = (
            " Check your network and model access, then retry."
            if public
            else " Your account needs access to this private checkpoint. For the public source, "
            "run python3 scripts/thor_demo.py kiosk-prepare --cosmos-source public (100 GB free disk; local GPU conversion)."
        )
        login_help = "" if public else f" To sign in from a terminal: {hf} auth login"
        raise KioskSetupError(
            f"Cannot download https://huggingface.co/{repo}.{alternative}{login_help}"
        )

    def cosmos_runtime_ready(self):
        args = self.cosmos_args("check")
        try:
            report = json.loads((args.work_dir / "encoder-cache-config-check.json").read_text())
            required = [
                args.work_dir / "edge-venv/bin/python",
                args.work_dir
                / "native-compat/_edgellm_cache_config_compat.cpython-312-aarch64-linux-gnu.so",
            ]
            return (
                self.image_ready(args.image)
                and all(path.is_file() for path in required)
                and report.get("passed") is True
                and report.get("source_commit") == cosmos.EDGE_COMMIT
            )
        except (OSError, ValueError):
            return False

    def ensure_native(self):
        prefix, _ = runtime_paths(self.config)
        try:
            native_runtime.check_runtime(Path(prefix), native_runtime.load_manifest())
        except (OSError, ValueError):
            self.step(
                "install-native-runtime",
                [
                    sys.executable,
                    REPO / "scripts/prepare_segmentation_runtime.py",
                    "--prefix",
                    prefix,
                ],
            )

    def ensure_webui(self):
        # Preparing weights/image must work before the target engine/runtime exists.
        baseline = self.config | {
            "segmentation_engine": None,
            "trt_runtime_prefix": None,
            "cuda_dir": None,
        }
        check = launch_commands("check", baseline)[0]
        if not self.probe(check, config=baseline):
            command = launch_commands("prepare", baseline)[0]
            if self.image_ready(self.config["segmentation_image"]):
                command.append("--skip-build")
            self.step("prepare-webui-and-segmentation", command, config=baseline)

    def select_engine(self):
        current = self.config["segmentation_engine"]
        target = (
            Path(current)
            if current
            else Path(self.config["segmentation_model_dir"]) / "retail-480.engine"
        )
        if target.exists():
            if not target.is_file() or target.stat().st_size == 0:
                raise KioskSetupError(f"Invalid existing engine: {target}. It was left unchanged.")
        else:
            # Engine is published last. Preserve sidecars from an interrupted export.
            if any(target.with_suffix(suffix).exists() for suffix in (".json", ".npz", ".onnx")):
                target = target.with_name(target.stem + "-" + uuid.uuid4().hex[:8] + ".engine")
            command = launch_commands(
                "export-engine", self.config, skip_build=True, output=str(target)
            )[0]
            self.step("build-retail-segmentation-engine", command, native=True)
        validate_engine(target)
        if str(target) != current:
            # Merge only this generated value; preserve unrelated user settings.
            data = json.loads(self.config_path.read_text())
            data["segmentation_engine"] = os.path.relpath(target, self.config_path.parent)
            cosmos.save_json(self.config_path, data)
            self.config = read_config(self.config_path)
            print(f"Enabled the segmentation engine in {self.config_path}.", flush=True)

    def install(self):
        # Keep host sudo authentication on the foreground terminal.
        subprocess.run(["bash", str(REPO / "scripts/prepare_thor_host.sh")], check=True)
        self.docker = cosmos.docker_access()
        if self.sudo_session is not None:
            self.sudo_session.start(self.docker)
        local = local_cosmos(self.config)
        present = self.local_checkpoint_present() if local else False
        resume_public = False
        if local:
            model_args = self.cosmos_args("check")
            journal = model_args.work_dir / "public-build-progress.json"
            if journal.is_file() and not cosmos.checkpoint_inspected(model_args):
                # Published weights can precede processor repair and final inspection.
                # Continue this installer's conversion, even if the retry omitted its flag.
                cosmos.BuildProgress(model_args)
                resume_public = True
                self.args.cosmos_source = "public"
                present = False
                print("Resuming the previous public Cosmos conversion.", flush=True)
        if local and not present:
            work = Path(self.config["cosmos_dir"])
            work.mkdir(parents=True, exist_ok=True)
            required_gb = 100 if self.args.cosmos_source == "public" else 40
            # The initial reservation includes source/intermediate files already on
            # disk during a resumed build; requiring it all again would block recovery.
            if not resume_public and shutil.disk_usage(work).free < required_gb * 1024**3:
                raise KioskSetupError(
                    f"Allow at least {required_gb} GB free at {work} for initial setup."
                )
            self.verify_download_access(self.ensure_hf())
        self.ensure_webui()
        self.ensure_native()
        if local:
            if not self.cosmos_runtime_ready():
                self.step("prepare-cosmos-runtime", self.cosmos_command("runtime"))
            if not present:
                action = "model-build" if self.args.cosmos_source == "public" else "model-download"
                self.step("prepare-cosmos-model", self.cosmos_command(action))
            elif not cosmos.checkpoint_inspected(self.cosmos_args("check")):
                self.step("verify-local-cosmos-checkpoint", self.cosmos_command("model-check"))
            self.step("check-cosmos-runtime", self.cosmos_command("check"))
        self.select_engine()
        # Install both input options so switching from USB to Store demo needs no setup.
        self.step(
            "prepare-store-videos", [sys.executable, REPO / "scripts/download_retail_25fps.py"]
        )
        self.step("check-webui", launch_commands("check", self.config)[0])
        print(
            "Kiosk assets ready: models, runtime, segmentation engine and store videos. No services were started.",
            flush=True,
        )


def kiosk_prepare(args):
    """Prepare assets for the upstream kiosk installer without starting any service."""
    if args.dry_run:
        config = load_config(args.config, dry_run=True)
        local = local_cosmos(config)
        steps = [
            "Check/install missing host prerequisites without changing JetPack or active services",
            "Create local config if absent; preserve existing settings",
            "Prepare/reuse pinned YOLO weights and the WebUI image",
            "Prepare/reuse the private TensorRT/CUDA runtime",
        ]
        if local:
            steps.append(
                "Prepare/reuse local Cosmos: "
                + args.cosmos_source
                + " model source, validated resumable conversion and runtime checks"
            )
        steps += [
            "Build/reuse the local segmentation engine and select it in config",
            "Install/verify all recorded store clips",
        ]
        print(
            json.dumps(
                {
                    "config": str(Path(args.config).expanduser().resolve()),
                    "local_cosmos": local,
                    "steps": steps,
                    "starts_services": False,
                    "opens_browser": False,
                },
                indent=2,
            )
        )
        return
    state = REPO / "artifacts/kiosk-setup"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "setup.lock").open("a") as lock, SudoKeepalive() as sudo_session:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise KioskSetupError(
                "Another kiosk asset setup is running. Wait for it to finish."
            ) from None
        assets = KioskAssets(args)
        assets.sudo_session = sudo_session
        assets.install()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=REPO / "thor-demo.json")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="Create a local config without overwriting an existing file")
    kiosk = sub.add_parser(
        "kiosk-prepare", help="Install/check assets for the upstream kiosk; no services or browser"
    )
    kiosk.add_argument("--cosmos-source", choices=("auto", "checkpoint", "public"), default="auto")
    kiosk.add_argument(
        "--dry-run", action="store_true", help="Preview without processes, network or files"
    )
    for action in (
        "prepare",
        "check",
        "webui",
        "runtime",
        "export-engine",
        "cosmos-prepare",
        "cosmos",
        "demo-videos",
    ):
        command = sub.add_parser(action)
        command.add_argument(
            "--dry-run",
            action="store_true",
            help="Show commands without running or writing anything",
        )
        if action == "webui":
            transport = command.add_mutually_exclusive_group()
            transport.add_argument(
                "--localhost-http", action="store_true", help="Serve local kiosk HTTP on loopback"
            )
            transport.add_argument(
                "--no-ssl", action="store_true", help="Serve HTTP on the configured ui_host"
            )
        if action in ("webui", "export-engine"):
            command.add_argument("--skip-build", action="store_true")
        if action == "check":
            command.add_argument(
                "--cosmos", action="store_true", help="Also check the optional local Cosmos runtime"
            )
        if action == "export-engine":
            command.add_argument("--output", type=Path)
    args = parser.parse_args()

    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.action == "kiosk-prepare":
            kiosk_prepare(args)
        elif args.action == "init":
            init_config(args.config)
        else:
            run_action(args, read_config(args.config))
    except KeyboardInterrupt:
        parser.exit(
            130, "Kiosk preparation stopped. Existing assets and services were preserved.\n"
        )
    except (KioskSetupError, ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Thor setup: {error}\n")


if __name__ == "__main__":
    main()
