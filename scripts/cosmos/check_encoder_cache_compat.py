#!/usr/bin/env python3
"""CPU configuration-object ABI check; never constructs a native runtime or model."""

import argparse
import hashlib
import importlib.metadata
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--compat-dir", type=Path, default=Path(__file__).resolve().parent / "native-compat"
    )
    args = parser.parse_args()
    from disable_encoder_cache import EXPECTED_ABI, EXPECTED_NATIVE_SHA256, EXPECTED_SOURCE, sha

    if importlib.metadata.version("tensorrt-edgellm") != "0.11.0":
        raise RuntimeError("Config check requires audited Edge-LLM0.11.0")
    from tensorrt_edgellm import runtime

    native = runtime.load()
    sys.path.insert(0, str(args.compat_dir.resolve(strict=True)))
    import _edgellm_cache_config_compat as compat

    native_path = Path(native.__file__)
    if (
        "jetson-thor-jp72-cu13-sm110" not in str(native_path)
        or sha(native_path) != EXPECTED_NATIVE_SHA256
    ):
        raise RuntimeError("Refusing config access for an unsupported native payload")
    if (
        compat.pybind_internals_id != EXPECTED_ABI
        or compat.edgellm_source_commit != EXPECTED_SOURCE
        or compat.context_cache_config_size != 32
    ):
        raise RuntimeError("Refusing config access for an unsupported companion ABI/source")
    config = native.ContextCacheConfig()
    defaults = compat.read_config(config)
    assert defaults == {
        "enabled": False,
        "max_records": 1024,
        "recurrent_snapshot_pool_bytes": 0,
        "partial_kv_snapshot_pool_bytes": 0,
        "encoder_embedding_cache_budget_bytes": 268435456,
    }, defaults
    expected = {
        "enabled": True,
        "max_records": 137,
        "recurrent_snapshot_pool_bytes": 0x102030405,
        "partial_kv_snapshot_pool_bytes": 0x506070809,
        "encoder_embedding_cache_budget_bytes": 268435456,
    }
    for key, value in expected.items():
        if key != "encoder_embedding_cache_budget_bytes":
            setattr(config, key, value)
    assert compat.read_config(config) == expected
    result = compat.disable_encoder_cache(config)
    expected["encoder_embedding_cache_budget_bytes"] = 0
    assert result == expected and compat.read_config(config) == expected
    for key, value in expected.items():
        if key != "encoder_embedding_cache_budget_bytes":
            assert getattr(config, key) == value
    assert compat.disable_encoder_cache(config) == expected
    fresh = native.ContextCacheConfig()
    assert compat.read_config(fresh) == defaults
    try:
        compat.disable_encoder_cache(object())
    except TypeError:
        pass
    else:
        raise AssertionError("Companion accepted a wrong Python type")
    report = {
        "passed": True,
        "native_file": str(native_path),
        "native_sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
        "companion_file": compat.__file__,
        "companion_sha256": hashlib.sha256(Path(compat.__file__).read_bytes()).hexdigest(),
        "pybind_internals_id": compat.pybind_internals_id,
        "source_commit": compat.edgellm_source_commit,
        "config_defaults": defaults,
        "tested_modified_config": result,
        "bound_fields_preserved": True,
        "independent_config_default_unchanged": True,
        "wrong_python_type_rejected": True,
        "runtime_constructed": False,
        "model_or_engine_loaded": False,
        "gpu_inference_performed": False,
    }
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
