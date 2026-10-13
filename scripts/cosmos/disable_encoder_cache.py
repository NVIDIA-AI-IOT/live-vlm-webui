"""Typed, version-guarded workaround for Edge-LLM 0.11's unbound encoder cache budget.

Only changes ContextCacheConfig.encoderEmbeddingCacheBudgetBytes before native
runtime construction. No TensorRT engine, image, pixel, or model changes.
"""

import hashlib
import importlib.metadata
import json
import time
from pathlib import Path

EXPECTED_NATIVE_SHA256 = "36fd822b8256ed79aa14871292cc517eb35cc24e71ce519361253291176b438e"
EXPECTED_ABI = "__pybind11_internals_v11_system_libstdcpp_gxx_abi_1xxx_use_cxx11_abi_1__"
EXPECTED_SOURCE = "95515c2f87fba8982db5a519f9022277667b3cc9"


def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def install(receipt_path=None):
    if importlib.metadata.version("tensorrt-edgellm") != "0.11.0":
        raise RuntimeError(
            "Cache workaround supports only the audited Edge-LLM0.11.0 native payload"
        )
    from tensorrt_edgellm import runtime

    native = runtime.load()
    import _edgellm_cache_config_compat as compat
    from experimental.server.runtime import engine

    if sha(native.__file__) != EXPECTED_NATIVE_SHA256:
        raise RuntimeError("Native payload hash differs from audited JP7.2/SM110 Edge-LLM0.11.0")
    if (
        compat.pybind_internals_id != EXPECTED_ABI
        or compat.edgellm_source_commit != EXPECTED_SOURCE
    ):
        raise RuntimeError("Companion pybind ABI or public struct source differs")
    if getattr(engine._native_context_cache_config, "_encoder_cache_disabled", False):
        raise RuntimeError(
            "Cache workaround was already installed; refuse ambiguous duplicate installation"
        )
    baseline = compat.read_config(native.ContextCacheConfig())
    if baseline != {
        "enabled": False,
        "max_records": 1024,
        "recurrent_snapshot_pool_bytes": 0,
        "partial_kv_snapshot_pool_bytes": 0,
        "encoder_embedding_cache_budget_bytes": 268435456,
    }:
        raise RuntimeError("Native default config differs from the audited struct")
    original = engine._native_context_cache_config
    receipt = {
        "status": "installed_before_runtime_configuration",
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "native_sha256": EXPECTED_NATIVE_SHA256,
        "companion_sha256": sha(compat.__file__),
        "helper_sha256": sha(__file__),
        "pybind_internals_id": EXPECTED_ABI,
        "source_commit": EXPECTED_SOURCE,
        "engine_rebuild_required": False,
        "pixel_or_model_inputs_changed": False,
        "native_config_calls": 0,
    }

    def save():
        if receipt_path is not None:
            path = Path(receipt_path)
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(json.dumps(receipt, indent=2) + "\n")
            temporary.replace(path)

    def config_without_encoder_cache(rt, config):
        value = original(rt, config)
        before = compat.read_config(value)
        after = compat.disable_encoder_cache(value)
        expected = dict(before, encoder_embedding_cache_budget_bytes=0)
        if after != expected or compat.read_config(value) != expected:
            raise RuntimeError("Typed encoder-cache config mutation did not round-trip exactly")
        receipt.update(
            status="native_config_budget_zero_applied",
            native_config_calls=receipt["native_config_calls"] + 1,
            native_config_before=before,
            native_config_after=after,
        )
        save()
        print(json.dumps({"event": "encoder_embedding_cache_disabled", **receipt}), flush=True)
        return value

    config_without_encoder_cache._encoder_cache_disabled = True
    engine._native_context_cache_config = config_without_encoder_cache
    save()
    return receipt
