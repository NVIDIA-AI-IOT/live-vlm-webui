#!/usr/bin/env python3
"""Inspect packed Nano INT4 weights on CPU; optionally test Edge-LLM CPU loading.

Default mode reads tensor headers and all scale values, not full model weights.
--load-edgellm runs a separate CPU-only subprocess with an RSS ceiling. Passing
this inspection establishes checkpoint structure/consumability, not inference
accuracy or TensorRT kernel correctness.
"""

import argparse
import collections
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import traceback
from pathlib import Path

from extract_cosmos3_nano_reasoner import expected_shapes, read_json, require, unique_object

LINEAR = re.compile(
    r"model\.language_model\.layers\.\d+\.(?:self_attn\.[qkvo]_proj|mlp\.(?:gate|up|down)_proj)\.weight$"
)
DTYPE_BYTES = {
    "U8": 1,
    "I8": 1,
    "I16": 2,
    "I32": 4,
    "I64": 8,
    "F16": 2,
    "BF16": 2,
    "F32": 4,
    "F64": 8,
    "BOOL": 1,
}


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def header(path):
    import struct

    size = path.stat().st_size
    with path.open("rb") as stream:
        raw = stream.read(8)
        require(len(raw) == 8, f"Truncated file: {path}")
        length = struct.unpack("<Q", raw)[0]
        require(2 <= length <= 100_000_000 and length + 8 <= size, f"Bad header length: {path}")
        data = json.loads(stream.read(length), object_pairs_hook=unique_object)
    offset, tensors = 0, {}
    for name, entry in sorted(
        ((k, v) for k, v in data.items() if k != "__metadata__"),
        key=lambda pair: pair[1]["data_offsets"][0],
    ):
        require(entry["dtype"] in DTYPE_BYTES, f'Unexpected dtype {name}: {entry["dtype"]}')
        require(all(type(d) is int and d >= 0 for d in entry["shape"]), f"Invalid shape: {name}")
        start, end = entry["data_offsets"]
        require(
            start == offset
            and end - start == math.prod(entry["shape"]) * DTYPE_BYTES[entry["dtype"]],
            f"Invalid/noncontiguous payload: {name}",
        )
        offset = end
        tensors[name] = dict(
            entry, file=str(path), absolute_offset=8 + length + start, nbytes=end - start
        )
    require(8 + length + offset == size, f"Truncated/trailing data: {path}")
    return tensors


def scale_statistics(entry):
    import numpy as np

    require(entry["dtype"] in ("F16", "BF16", "F32", "F64"), "Scale must be floating point")
    with open(entry["file"], "rb") as stream:
        stream.seek(entry["absolute_offset"])
        raw = stream.read(entry["nbytes"])
    require(len(raw) == entry["nbytes"], "Truncated scale payload")
    if entry["dtype"] == "BF16":
        values = (np.frombuffer(raw, dtype="<u2").astype(np.uint32) << 16).view(np.float32)
    else:
        values = np.frombuffer(
            raw, dtype={"F16": "<f2", "F32": "<f4", "F64": "<f8"}[entry["dtype"]]
        )
    require(
        values.size > 0 and bool(np.isfinite(values).all()), "Empty/nonfinite quantization scales"
    )
    require(bool((values > 0).all()), "Nonpositive quantization scales")
    return {
        "count": int(values.size),
        "min": float(values.min()),
        "max": float(values.max()),
        "finite": True,
        "positive": True,
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def validate_architecture(config, source_config):
    # Transformers 5.14.1 corrects the legacy vision model_type and migrates
    # rope_scaling + rope_theta into rope_parameters. Compare semantics, not
    # serialization spelling. Defaults below are from its Qwen3-VL config.
    defaults = {
        "text_config": {
            "attention_bias": False,
            "head_dim": 128,
            "hidden_act": "silu",
            "hidden_size": 4096,
            "intermediate_size": 22016,
            "max_position_embeddings": 128000,
            "model_type": "qwen3_vl_text",
            "num_attention_heads": 32,
            "num_hidden_layers": 32,
            "num_key_value_heads": 32,
            "rms_norm_eps": 1e-6,
            "vocab_size": 151936,
        },
        "vision_config": {
            "deepstack_visual_indexes": [8, 16, 24],
            "depth": 27,
            "hidden_act": "gelu_pytorch_tanh",
            "hidden_size": 1152,
            "in_channels": 3,
            "intermediate_size": 4304,
            "model_type": "qwen3_vl_vision",
            "num_heads": 16,
            "num_position_embeddings": 2304,
            "out_hidden_size": 3584,
            "patch_size": 16,
            "spatial_merge_size": 2,
            "temporal_patch_size": 2,
        },
    }
    for section, section_defaults in defaults.items():
        actual = config.get(section, {})
        expected = source_config[section]
        for key, default in section_defaults.items():
            got, wanted = actual.get(key, default), expected.get(key, default)
            if section == "vision_config" and key == "model_type":
                got = "qwen3_vl_vision" if got == "qwen3_vl" else got
                wanted = "qwen3_vl_vision" if wanted == "qwen3_vl" else wanted
            if section == "text_config" and key == "num_key_value_heads" and got is None:
                got = actual.get("num_attention_heads", section_defaults["num_attention_heads"])
            require(got == wanted, f"Architecture differs: {section}.{key}: {got!r} != {wanted!r}")

    def normalized_rope(text):
        old, new = text.get("rope_scaling"), text.get("rope_parameters")

        def normalize(value):
            value = dict(value or {})
            value.setdefault("rope_theta", text.get("rope_theta", 500000.0))
            value.setdefault("rope_type", value.pop("type", "default"))
            return value

        if old and new:
            require(normalize(old) == normalize(new), "Conflicting legacy/current RoPE metadata")
        return normalize(new or old)

    require(
        normalized_rope(config["text_config"]) == normalized_rope(source_config["text_config"]),
        "Text RoPE parameters changed",
    )
    for key in (
        "image_token_id",
        "video_token_id",
        "vision_start_token_id",
        "vision_end_token_id",
        "tie_word_embeddings",
    ):
        require(
            config.get(key, source_config[key]) == source_config[key],
            f"Root token/tie configuration changed: {key}",
        )
    # bos/eos/use_cache/initializer/dtype fields may move or be omitted during
    # Transformers serialization; they are not weight-architecture dimensions.


def inspect(checkpoint, reference):
    config = read_json(checkpoint / "config.json")
    require(config.get("model_type") == "qwen3_vl", "Expected extracted Qwen3-VL Nano reasoner")
    require(
        config["text_config"]["num_hidden_layers"] == 36
        and config["text_config"]["hidden_size"] == 4096,
        "Unexpected Nano architecture",
    )
    source_config = reference["qwen_config"]
    validate_architecture(config, source_config)
    shapes = expected_shapes(source_config)
    files = sorted(checkpoint.glob("*.safetensors"))
    require(bool(files), "No safetensors weights found")
    tensors = {}
    for path in files:
        require(not path.is_symlink(), f"Refusing symlink: {path}")
        current = header(path)
        require(not set(current).intersection(tensors), f"Duplicate tensor in {path}")
        tensors.update(current)
    index_path = checkpoint / "model.safetensors.index.json"
    if index_path.exists():
        weight_map = read_json(index_path)["weight_map"]
        require(set(weight_map) == set(tensors), "Safetensors index does not match actual tensors")
        for name, relpath in weight_map.items():
            require(
                Path(tensors[name]["file"]).name == relpath, f"Wrong tensor shard in index: {name}"
            )
    require(
        set(shapes).issubset(tensors),
        f"Missing original model tensors: {sorted(set(shapes)-set(tensors))}",
    )
    quant_path = checkpoint / "hf_quant_config.json"
    hfq = read_json(quant_path) if quant_path.exists() else {}
    quant = hfq.get("quantization") or config.get("quantization_config") or {}
    algorithm = str(quant.get("quant_algo", "")).upper()
    require(
        "W4A16" in algorithm and "AWQ" in algorithm,
        f"Expected W4A16_AWQ metadata, got {algorithm!r}",
    )
    require(int(quant.get("group_size", 128)) == 128, "Expected quantization group size 128")
    require(not quant.get("kv_cache_quant_algo"), "Unexpected KV-cache quantization metadata")
    expected_extra, packed, high_precision = set(), [], []
    missing_pre_scales = []
    for name, shape in shapes.items():
        tensor = tensors[name]
        if not LINEAR.fullmatch(name):
            require(tensor["dtype"] in ("F16", "BF16"), f"Expected high precision: {name}")
            require(tensor["shape"] == shape, f"High precision shape changed: {name}")
            high_precision.append(name)
            continue
        n, k = shape
        require(tensor["dtype"] == "U8", f"INT4 weights are not packed uint8: {name}")
        require(tensor["shape"] == [n // 2, k], f'Wrong packedW4 shape: {name}: {tensor["shape"]}')
        prefix = name[: -len(".weight")]
        scale_name = prefix + ".weight_scale"
        require(scale_name in tensors, f"Missing AWQ group scales: {name}")
        scale = tensors[scale_name]
        require(scale["shape"] == [n, k // 128], f"Wrong group128 scale shape: {scale_name}")
        expected_extra.add(scale_name)
        record = {
            "name": name,
            "original_shape": shape,
            "packed_shape": tensor["shape"],
            "packed_bytes": tensor["nbytes"],
            "logical_parameters": n * k,
            "group_size": 128,
            "scale_dtype": scale["dtype"],
            "scale_statistics": scale_statistics(scale),
        }
        pre_names = [
            prefix + suffix
            for suffix in (".pre_quant_scale", "._pre_quant_scale")
            if prefix + suffix in tensors
        ]
        require(len(pre_names) <= 1, f"Duplicate pre_quant_scale aliases: {name}")
        if pre_names:
            pre_name = pre_names[0]
            pre = tensors[pre_name]
            require(pre["shape"] in ([k], [1, k]), f"Wrong AWQ input scale shape: {pre_name}")
            record["pre_quant_scale_statistics"] = scale_statistics(pre)
            expected_extra.add(pre_name)
        else:
            missing_pre_scales.append(name)
        packed.append(record)
    require(len(packed) == 252, f"Expected252 packed decoder projections, got {len(packed)}")
    require(
        not (set(tensors) - set(shapes) - expected_extra),
        f"Unexpected checkpoint tensors: {sorted(set(tensors)-set(shapes)-expected_extra)}",
    )
    vision = [name for name in high_precision if name.startswith("model.visual.")]
    require(len(vision) == 351, f"Expected351 complete visual tensors, got {len(vision)}")
    # Dense AWQ uses per-linear calibrated scaling and v0.11 deliberately skips
    # resmoothing. Treat missing scales as a review failure rather than assuming1.
    require(
        not missing_pre_scales,
        f"Missing per-linear calibrated AWQ pre-scales: {missing_pre_scales}",
    )
    by_dtype = collections.Counter()
    bytes_by_dtype = collections.Counter()
    for tensor in tensors.values():
        by_dtype[tensor["dtype"]] += 1
        bytes_by_dtype[tensor["dtype"]] += tensor["nbytes"]
    return {
        "structural_inspection": "passed",
        "quantization_metadata": quant,
        "tensor_count": len(tensors),
        "original_tensor_count": len(shapes),
        "packed_decoder_projection_count": len(packed),
        "high_precision_tensor_count": len(high_precision),
        "high_precision_visual_tensor_count": len(vision),
        "deepstack_tensor_count": sum("deepstack_merger_list" in name for name in vision),
        "lm_head_dtype": tensors["lm_head.weight"]["dtype"],
        "embedding_dtype": tensors["model.language_model.embed_tokens.weight"]["dtype"],
        "logical_model_parameters": sum(math.prod(shape) for shape in shapes.values()),
        "int4_weight_parameters": sum(record["logical_parameters"] for record in packed),
        "tensor_payload_bytes": sum(t["nbytes"] for t in tensors.values()),
        "checkpoint_file_bytes": sum(path.stat().st_size for path in files),
        "tensor_count_by_dtype": dict(by_dtype),
        "payload_bytes_by_dtype": dict(bytes_by_dtype),
        "all_quantization_scales_finite_and_positive": True,
        "packed_layers": packed,
        "accuracy_validated": False,
        "limitations": [
            "Tensor bytes were not evaluated numerically against fake quantization",
            "This is not TensorRT inference, engine validation, or a task-accuracy result",
        ],
    }


def load_worker(args):
    # Deliberately imported only inside the child process, with CUDA hidden.
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    sys.path.insert(0, str(args.repo.resolve()))
    result = {
        "status": "failed",
        "mode": "CPU checkpoint loading only",
        "accuracy_validated": False,
    }
    try:
        import resource

        import torch

        torch.set_num_threads(args.cpu_threads)
        from tensorrt_edgellm.model import AutoModel

        model = AutoModel.from_pretrained(
            str(args.checkpoint.resolve()), device="cpu", low_cpu_mem_usage=True
        )
        classes = collections.Counter(type(module).__name__ for module in model.modules())
        tensors = list(model.parameters()) + list(model.buffers())
        if any(t.device.type != "cpu" for t in tensors):
            raise ValueError("CPU loader created a non-CPU tensor")
        count = classes.get("ModelOptAWQPrepackedLinear", 0)
        require(count == 252, f"Expected252 loaded AWQ linears, got{count}")
        result.update(
            status="passed",
            model_class=type(model).__name__,
            module_class_counts=dict(classes),
            model_tensor_storage_bytes=sum(t.numel() * t.element_size() for t in tensors),
            peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
        )
    except Exception as error:  # noqa: BLE001 - record native/format errors in a failure receipt
        result.update(error=str(error), traceback=traceback.format_exc())
    write_json(args.report, result)
    return 0 if result["status"] == "passed" else 1


def load_cpu_bounded(args):
    child_report = args.report.with_name(args.report.stem + ".edgellm_cpu_load.json")
    log_path = args.report.with_name(args.report.stem + ".edgellm_cpu_load.log")
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--checkpoint",
        str(args.checkpoint),
        "--repo",
        str(args.repo),
        "--report",
        str(child_report),
        "--cpu-threads",
        str(args.cpu_threads),
        "--load-worker",
    ]
    environment = dict(os.environ, CUDA_VISIBLE_DEVICES="")
    ceiling = int(args.max_load_gib * 1024**3)
    peak, exceeded = 0, False
    with log_path.open("w") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=environment)
        try:
            while process.poll() is None:
                try:
                    status = Path(f"/proc/{process.pid}/status").read_text()
                    match = re.search(r"^VmRSS:\s+(\d+)\s+kB$", status, re.MULTILINE)
                    if match:
                        peak = max(peak, int(match.group(1)) * 1024)
                        if peak > ceiling:
                            exceeded = True
                            process.terminate()
                            try:
                                process.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                process.kill()
                            break
                except FileNotFoundError:
                    pass
                time.sleep(0.2)
            code = process.wait()
        except BaseException:
            process.terminate()
            process.wait(timeout=5)
            raise
    if exceeded:
        return {
            "status": "memory_limit_exceeded",
            "observed_peak_rss_bytes": peak,
            "limit_bytes": ceiling,
            "log": str(log_path),
        }
    if not child_report.exists():
        return {
            "status": "failed",
            "exit_code": code,
            "observed_peak_rss_bytes": peak,
            "log": str(log_path),
        }
    result = read_json(child_report)
    result.update(exit_code=code, observed_peak_rss_bytes=peak, log=str(log_path))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repo", type=Path)
    parser.add_argument(
        "--reference",
        type=Path,
        default=Path(__file__).with_name("cosmos3_nano_extraction_reference.json"),
    )
    parser.add_argument("--load-edgellm", action="store_true")
    parser.add_argument("--max-load-gib", type=float, default=20)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--load-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    args.checkpoint, args.report = args.checkpoint.resolve(), args.report.resolve()
    if args.load_worker:
        return load_worker(args)
    require(not args.report.exists(), f"Refusing to overwrite report: {args.report}")
    require(args.max_load_gib > 0 and args.cpu_threads > 0, "Invalid CPU resource limits")
    if args.load_edgellm:
        require(
            args.repo is not None and args.repo.is_dir(), "--repo required for Edge-LLM loading"
        )
    result = {"checkpoint": str(args.checkpoint), "accuracy_validated": False}
    exit_code = 0
    try:
        result.update(inspect(args.checkpoint, read_json(args.reference)))
        write_json(args.report, result)
        if args.load_edgellm:
            result["edgellm_cpu_load"] = load_cpu_bounded(args)
            if result["edgellm_cpu_load"]["status"] != "passed":
                exit_code = 2
    except Exception as error:  # noqa: BLE001 - record native/format errors in a failure receipt
        result.update(
            structural_inspection="failed", error=str(error), traceback=traceback.format_exc()
        )
        exit_code = 1
    write_json(args.report, result)
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "packed_layers"},
            indent=2,
            default=str,
        )
    )
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
