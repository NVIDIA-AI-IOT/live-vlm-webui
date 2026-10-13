#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Build a fixed-label YOLOE-26s retail TensorRT engine on the target GPU.

Validated on Thor with torch 2.11.0+cu130, ultralytics==8.4.173,
onnx==1.19.1, and NVIDIA TensorRT 10.16.2.10 (Python bindings AND native libraries).
Use the segmentation runtime's pinned CLIP package and local mobileclip2_b.ts.
This tool never installs packages, downloads weights, or starts the web server.

The four retail labels are baked into the engine. Input is FP32 BCHW, spatial
288x480, dynamic batch 1..4; TensorRT may use FP16 internal tactics. Other source
aspect ratios must be letterboxed to that exact spatial size. Outputs include
mask coefficients/prototypes; the application still produces original-size masks.

Example inside the CUDA/TensorRT-enabled runtime:
    python scripts/export_retail_segmentation.py --output artifacts/retail-480.engine

Official contracts:
    https://docs.ultralytics.com/models/yoloe/#export-usage
    https://docs.ultralytics.com/integrations/tensorrt/
"""

import argparse
import ast
import hashlib
import importlib
import json
import os
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLASSES = ("person", "shopping cart", "shopping basket", "backpack")
IMAGE_SIZE = (288, 480)
MIN_SHAPE = (1, 3, *IMAGE_SIZE)
MAX_SHAPE = (4, 3, *IMAGE_SIZE)
CHECKPOINT_SHA256 = "48f24206bc8680d60cbbfa296b0140da849669b9515058b72f5a945142df0654"
ENCODER_SHA256 = "35d7f213e4d75f38514e4656ad3cb91158bd33e3805d8ac349f23b186f66982f"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate_metadata(metadata):
    """Check the same metadata contract consumed by the retail runtime."""
    expected = {
        "task": "segment",
        "head": "YOLOESegment26",
        "imgsz": list(IMAGE_SIZE),
        "batch": 4,
        "channels": 3,
        "stride": 32,
        "end2end": False,
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise ValueError(
                f"Unexpected exported {key}: {metadata.get(key)!r}; expected {value!r}"
            )
    names = metadata.get("names", {})
    if names != dict(enumerate(CLASSES)) and names != {
        str(index): name for index, name in enumerate(CLASSES)
    }:
        raise ValueError("Exported retail class names or their order do not match")
    args = metadata.get("args", {})
    if args.get("dynamic") is not True or args.get("nms") not in (None, False):
        raise ValueError("Expected dynamic batch export without embedded NMS")


def read_onnx_metadata(model):
    metadata = {}
    for item in model.metadata_props:
        try:
            metadata[item.key] = ast.literal_eval(item.value)
        except (ValueError, SyntaxError):
            metadata[item.key] = item.value
    validate_metadata(metadata)
    return metadata


def dependencies():
    modules = {}
    for name in ("torch", "ultralytics", "onnx", "tensorrt", "clip"):
        try:
            modules[name] = importlib.import_module(name)
        except (ImportError, OSError) as exc:
            raise RuntimeError(
                f"Cannot load {name}: {exc}. Use the Thor segmentation runtime with "
                "onnx==1.19.1 and NVIDIA TensorRT Python/native libraries. See --help."
            ) from exc
    if modules["ultralytics"].__version__ != "8.4.173":
        raise RuntimeError("This export recipe requires ultralytics==8.4.173")
    torch = modules["torch"]
    if not torch.cuda.is_available():
        raise RuntimeError("Export must run locally on the target CUDA GPU")
    if modules["onnx"].__version__ != "1.19.1":
        raise RuntimeError("This export recipe requires onnx==1.19.1")
    if modules["tensorrt"].__version__ != "10.16.2.10":
        raise RuntimeError(
            "This export recipe requires TensorRT 10.16.2.10 Python/native libraries"
        )
    if torch.cuda.get_device_capability(0) != (11, 0):
        raise RuntimeError("This export recipe targets Jetson Thor (CUDA capability 11.0)")
    return modules


def build_engine(onnx_path, trt, workspace_gib):
    logger = trt.Logger(trt.Logger.INFO)
    builder = trt.Builder(logger)
    network = builder.create_network(1 << int(trt.NetworkDefinitionCreationFlag.EXPLICIT_BATCH))
    parser = trt.OnnxParser(network, logger)
    if not parser.parse(onnx_path.read_bytes()):
        errors = "\n".join(str(parser.get_error(index)) for index in range(parser.num_errors))
        raise RuntimeError(f"TensorRT ONNX parse failed:\n{errors}")
    if network.num_inputs != 1 or network.get_input(0).name != "images":
        raise ValueError("Expected one ONNX input named images")
    config = builder.create_builder_config()
    config.set_flag(trt.BuilderFlag.FP16)
    config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, workspace_gib << 30)
    config.builder_optimization_level = 3
    profile = builder.create_optimization_profile()
    # TensorRT 10.16 set_shape returns None; validate with its getter.
    profile.set_shape("images", MIN_SHAPE, MAX_SHAPE, MAX_SHAPE)
    if tuple(tuple(shape) for shape in profile.get_shape("images")) != (
        MIN_SHAPE,
        MAX_SHAPE,
        MAX_SHAPE,
    ):
        raise RuntimeError("TensorRT did not retain the requested batch/spatial profile")
    config.add_optimization_profile(profile)
    serialized = builder.build_serialized_network(network, config)
    if serialized is None:
        raise RuntimeError("TensorRT did not produce an engine")
    # Deserialize on this GPU and verify bindings before publishing an artifact.
    runtime = trt.Runtime(logger)
    engine = runtime.deserialize_cuda_engine(serialized)
    if engine is None or engine.num_optimization_profiles != 1 or engine.num_io_tensors != 3:
        raise RuntimeError("Built engine does not have the expected input/two-output contract")
    if tuple(tuple(shape) for shape in engine.get_tensor_profile_shape("images", 0)) != (
        MIN_SHAPE,
        MAX_SHAPE,
        MAX_SHAPE,
    ):
        raise RuntimeError("Built engine has an unexpected input profile")
    if engine.get_tensor_dtype("images") != trt.float32:
        raise RuntimeError("Expected FP32 engine input with FP16 internal tactics")
    return bytes(serialized)


def export(weights, output, *, workspace_gib=2, force=False):
    if output.suffix != ".engine":
        raise ValueError("--output must end in .engine")
    for path, expected in (
        (weights, CHECKPOINT_SHA256),
        (weights.parent / "mobileclip2_b.ts", ENCODER_SHA256),
    ):
        if not path.is_file() or digest(path) != expected:
            raise ValueError(
                f"Missing or mismatched pinned model asset: {path}; expected {expected}"
            )
    destinations = [output.with_suffix(suffix) for suffix in (".engine", ".onnx", ".npz", ".json")]
    if not force and any(path.exists() for path in destinations):
        raise FileExistsError(
            "Output or sidecar exists; use another --output or --force to replace"
        )
    os.environ["YOLO_AUTOINSTALL"] = "False"
    modules = dependencies()
    torch, ultralytics, onnx, trt = (
        modules[name] for name in ("torch", "ultralytics", "onnx", "tensorrt")
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    ultralytics.settings.update({"weights_dir": str(weights.parent)})
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix=".retail-export-", dir=output.parent) as directory:
        staging = Path(directory)
        checkpoint = staging / "retail-480.pt"
        shutil.copy2(weights, checkpoint)
        model = ultralytics.YOLOE(str(checkpoint))
        if model.task != "segment":
            raise ValueError("Expected the YOLOE-26s segmentation checkpoint")
        model.set_classes(list(CLASSES))
        prompts = staging / "retail-480.npz"
        model.save_prompt_embeddings(str(prompts))
        onnx_path = Path(
            model.export(
                format="onnx",
                imgsz=list(IMAGE_SIZE),
                batch=4,
                dynamic=True,
                quantize=32,
                device=0,
                simplify=False,
                opset=17,
                max_det=50,
            )
        )
        metadata = read_onnx_metadata(onnx.load(onnx_path))
        native = build_engine(onnx_path, trt, workspace_gib)
        metadata["retail_export"] = {
            "source_sha256": CHECKPOINT_SHA256,
            "precision": "FP16 tactics with mixed-precision fallback; FP32 input/output",
            "input_min": MIN_SHAPE,
            "input_opt": MAX_SHAPE,
            "input_max": MAX_SHAPE,
        }
        header = json.dumps(metadata).encode("utf-8")
        engine_path = staging / "retail-480.engine"
        engine_path.write_bytes(struct.pack("<I", len(header)) + header + native)
        validate_metadata(json.loads(header))
        report = {
            "engine": str(output),
            "engine_sha256": digest(engine_path),
            "source_sha256": CHECKPOINT_SHA256,
            "metadata": metadata,
            "seconds": time.perf_counter() - started,
            "gpu": torch.cuda.get_device_name(0),
            "gpu_capability": torch.cuda.get_device_capability(0),
            "torch": torch.__version__,
            "cuda": torch.version.cuda,
            "ultralytics": ultralytics.__version__,
            "onnx": onnx.__version__,
            "tensorrt": trt.__version__,
            "next_check": "Compare original-resolution masks with PyTorch before production use.",
        }
        report_path = staging / "retail-480.json"
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        # Publish the usable engine last; a failed export never leaves a partial engine.
        for source, destination in (
            (onnx_path, output.with_suffix(".onnx")),
            (prompts, output.with_suffix(".npz")),
            (report_path, output.with_suffix(".json")),
            (engine_path, output),
        ):
            os.replace(source, destination)
    print(json.dumps(report, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check", action="store_true", help="Check dependencies and GPU; do not export"
    )
    parser.add_argument("--weights", type=Path, default=REPO_ROOT / ".models/yoloe-26s-seg.pt")
    parser.add_argument("--output", type=Path, default=REPO_ROOT / "artifacts/retail-480.engine")
    parser.add_argument(
        "--workspace-gib", type=int, default=2, help="TensorRT workspace limit (default: 2 GiB)"
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace an existing engine and its sidecars"
    )
    args = parser.parse_args()
    if args.workspace_gib < 1:
        parser.error("--workspace-gib must be a positive integer")
    try:
        if args.check:
            modules = dependencies()
            print(
                json.dumps(
                    {
                        name: getattr(module, "__version__", "loaded")
                        for name, module in modules.items()
                    },
                    indent=2,
                )
            )
            return 0
        export(
            args.weights.expanduser().resolve(),
            args.output.expanduser().resolve(),
            workspace_gib=args.workspace_gib,
            force=args.force,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        print(f"Retail TensorRT export failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
