#!/usr/bin/env python3
"""Portable adaptation of the locally benchmarked Edge-LLM 0.11 launcher.

This adapted file received CPU-only checks at publication, not another engine
build or GPU benchmark. --dry-run validates metadata and prints the exact plan
without importing TensorRT/PyTorch, creating a cache, or loading a GPU runtime.
The default profile matches the benchmark: batch4/IFB, input2048, KV16384,
image tokens64..1024, FP16 KV, and TensorRT WORKSPACE8GiB. WORKSPACE is not a
limit on total device memory. Longer/different profiles require new validation.
Encoder caching is disabled using the separately built typed compatibility module;
this avoids the reproduced upstream DeepStack cache-restore shape defect.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import sys
import time
from pathlib import Path

EDGE_VERSION = "0.11.0"
TRT_VERSION = "10.16.2.10"
NATIVE_PAYLOAD = "jetson-thor-jp72-cu13-sm110"
WORKSPACE_BYTES = 8 * (1 << 30)


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--served-model-name", default="cosmos3-nano-reasoner-int4")
    parser.add_argument("--max-input-len", type=int, default=2048)
    parser.add_argument("--max-kv-cache-capacity", type=int, default=16384)
    parser.add_argument("--max-batch-size", type=int, choices=(1, 4), default=4)
    parser.add_argument("--disable-in-flight-batching", action="store_true")
    parser.add_argument(
        "--encoder-cache-compat-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "native-compat",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.max_batch_size > 1 and args.disable_in_flight_batching:
        parser.error("batch size >1 requires in-flight batching")
    if args.max_input_len < 2048:
        parser.error("this wrapper requires an input profile of at least 2048 tokens")
    if args.max_kv_cache_capacity < args.max_input_len + 4096:
        parser.error("KV capacity must allow the input profile plus 4096 generated tokens")
    if not 1 <= args.port <= 65535:
        parser.error("invalid port")
    return args


def make_plan(args):
    model_dir = args.model_dir.resolve(strict=True)
    config = json.loads((model_dir / "config.json").read_text())
    quant = json.loads((model_dir / "hf_quant_config.json").read_text())["quantization"]
    if config.get("model_type") != "qwen3_vl":
        raise ValueError("Expected the extracted Qwen3-VL Cosmos3-Nano reasoner checkpoint")
    if not (
        quant.get("quant_algo") == "W4A16_AWQ"
        and quant.get("group_size") == 128
        and quant.get("kv_cache_quant_algo") is None
    ):
        raise ValueError("Expected W4A16_AWQ group128 with unquantized KV")
    processor = json.loads((model_dir / "preprocessor_config.json").read_text())
    if processor.get("size") != {"shortest_edge": 65536, "longest_edge": 1048576}:
        raise ValueError("Image processor must match the validated 65536..1048576 pixel bounds")
    ifb = not args.disable_in_flight_batching
    cache = args.cache_dir.resolve() / (
        f"cosmos3-nano-awq-edge011-b{args.max_batch_size}-ifb{int(ifb)}"
        f"-i{args.max_input_len}-kv{args.max_kv_cache_capacity}-image64-1024-ws8GiB-v1"
    )
    argv = [
        "tensorrt-edgellm-serve",
        str(model_dir),
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--served-model-name",
        args.served_model_name,
        "--reasoning-parser",
        "none",
        "--cache-dir",
        str(cache),
        "--max-batch-size",
        str(args.max_batch_size),
        "--max-input-len",
        str(args.max_input_len),
        "--max-kv-cache-capacity",
        str(args.max_kv_cache_capacity),
        "--max-image-tokens",
        "1024",
        "--max-image-tokens-per-image",
        "1024",
    ]
    if ifb:
        argv.append("--enable-in-flight-batching")
    return {
        "model_dir": str(model_dir),
        "cache_dir": str(cache),
        "server_argv": argv,
        "required_edgellm_version": EDGE_VERSION,
        "required_tensorrt_version": TRT_VERSION,
        "required_native_payload": NATIVE_PAYLOAD,
        "workspace_bytes": WORKSPACE_BYTES,
        "workspace_is_total_memory_limit": False,
        "min_image_tokens": 64,
        "encoder_embedding_cache_budget_bytes": 0,
        "encoder_cache_compat_dir": str(args.encoder_cache_compat_dir.resolve()),
        "encoder_cache_workaround": "typed public config field set to zero before native construction; no engine or input changes",
        "max_image_tokens": 1024,
        "max_image_tokens_per_image": 1024,
        "min_image_pixels": 65536,
        "max_image_pixels": 1048576,
        "profile_matches_benchmarked_batch4": args.max_batch_size == 4
        and ifb
        and args.max_input_len == 2048
        and args.max_kv_cache_capacity == 16384,
        "portable_wrapper_validation": "CPU-only checks at publication; original launcher and matching profile were GPU-benchmarked",
        "source_metadata_sha256": {
            name: hashlib.sha256((model_dir / name).read_bytes()).hexdigest()
            for name in ("config.json", "hf_quant_config.json", "preprocessor_config.json")
        },
    }


def main():
    args = parse_args()
    plan = make_plan(args)
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return
    if platform.machine() != "aarch64" or sys.version_info[:2] != (3, 12):
        raise RuntimeError("This reproduced environment requires Linux aarch64 and CPython 3.12")
    installed = importlib.metadata.version("tensorrt-edgellm")
    if installed != EDGE_VERSION:
        raise RuntimeError(f"Expected tensorrt-edgellm=={EDGE_VERSION}; got {installed}")
    import tensorrt as trt

    if trt.__version__ != TRT_VERSION:
        raise RuntimeError(f"Expected platform TensorRT {TRT_VERSION}; got {trt.__version__}")
    from tensorrt_edgellm import runtime

    native = runtime.load()
    if NATIVE_PAYLOAD not in str(native.__file__):
        raise RuntimeError(f"Expected native payload {NATIVE_PAYLOAD}; got {native.__file__}")
    from edgellm_stop_compat import install_prompt_stop_fix
    from experimental.builder.core import builder as official_builder
    from experimental.server.cli import main as official_server_main
    from experimental.server.runtime.engine import LLM
    from experimental.server.runtime.engine_build import BuildOptions

    install_prompt_stop_fix(LLM)
    original_to_argv = BuildOptions.to_argv
    original_setup_profiles = official_builder._setup_profiles

    def to_argv_with_image_minimum(self, model_dir, bundle_dir):
        argv = original_to_argv(self, model_dir, bundle_dir)
        if "--min-image-tokens" in argv:
            raise RuntimeError("Upstream already sets the visual minimum; review wrapper")
        return argv + ["--min-image-tokens", "64"]

    def setup_profiles_with_workspace(builder, config, network, cfg, build_args, bundle):
        config.set_memory_pool_limit(trt.MemoryPoolType.WORKSPACE, WORKSPACE_BYTES)
        if config.get_memory_pool_limit(trt.MemoryPoolType.WORKSPACE) != WORKSPACE_BYTES:
            raise RuntimeError("TensorRT rejected the 8 GiB workspace limit")
        if build_args.min_image_tokens != 64:
            raise RuntimeError("Unexpected visual minimum")
        return original_setup_profiles(builder, config, network, cfg, build_args, bundle)

    BuildOptions.to_argv = to_argv_with_image_minimum
    official_builder._setup_profiles = setup_profiles_with_workspace
    cache = Path(plan["cache_dir"])
    cache.mkdir(parents=True, exist_ok=True)
    compat_dir = args.encoder_cache_compat_dir.resolve(strict=True)
    sys.path.insert(0, str(compat_dir))
    from disable_encoder_cache import install as install_encoder_cache_disable

    cache_disable = install_encoder_cache_disable(cache / "encoder_cache_activation.json")
    manifest = dict(
        plan,
        encoder_cache_compat=cache_disable,
        timestamp_utc=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        pid=os.getpid(),
        status="build_or_load_requested",
        native_runtime_file=str(native.__file__),
        launcher_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        stop_compat_sha256=hashlib.sha256(
            Path(__file__).with_name("edgellm_stop_compat.py").read_bytes()
        ).hexdigest(),
    )
    (cache / "launch_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"event": "portable_launcher_start", **manifest}), flush=True)
    sys.argv = plan["server_argv"]
    official_server_main()


if __name__ == "__main__":
    main()
