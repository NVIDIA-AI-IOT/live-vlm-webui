#!/usr/bin/env python3
"""Build the small typed cache-config companion; no CUDA/TRT compiler or model load.

Requires Linux aarch64, CPython3.12 development headers, g++, pybind11==3.0.4,
and the pinned, unmodified Edge-LLM0.11 public ContextCacheConfig header.
The separately fetched NVIDIA header retains its Apache-2.0 license.
"""

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

COMMIT = "95515c2f87fba8982db5a519f9022277667b3cc9"
HEADER_SHA256 = "224b2fa463f5b228b94f817c5a222a768c69555c63a3b14ab8e85cb27ed993ff"
ABI = "__pybind11_internals_v11_system_libstdcpp_gxx_abi_1xxx_use_cxx11_abi_1__"


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--edge-source", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parent / "native-compat"
    )
    parser.add_argument("--cxx", default="g++")
    parser.add_argument(
        "--pybind-include",
        type=Path,
        help="Optional existing pybind11 include directory; exact header version is checked",
    )
    args = parser.parse_args()
    if platform.machine() != "aarch64" or sys.version_info[:2] != (3, 12):
        parser.error("This companion targets Linux aarch64 CPython3.12 only")
    source = args.edge_source.resolve(strict=True)
    commit = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    header = source / "cpp/runtime/state/contextCache/contextCacheConfig.h"
    if commit != COMMIT or sha(header) != HEADER_SHA256:
        parser.error("Expected the exact pinned Edge-LLM0.11 commit and unmodified config header")
    if args.pybind_include is None:
        import pybind11

        if pybind11.__version__ != "3.0.4":
            parser.error("Install pybind11==3.0.4 in this isolated build environment")
        include = Path(pybind11.get_include())
    else:
        include = args.pybind_include.resolve(strict=True)
    common = (include / "pybind11/detail/common.h").read_text()
    versions = tuple(
        int(
            re.search(
                r"^#define PYBIND11_VERSION_" + key + r"\s+(\d+)", common, re.MULTILINE
            ).group(1)
        )
        for key in ("MAJOR", "MINOR", "PATCH")
    )
    if versions != (3, 0, 4):
        parser.error("pybind11 headers must be exactly3.0.4")
    python_include = Path(sysconfig.get_paths()["include"])
    if not (python_include / "Python.h").is_file():
        parser.error("CPython3.12 development headers are missing")
    receipt = build_companion(
        source=source,
        include=include,
        python_include=python_include,
        output_dir=args.output_dir,
        cxx=args.cxx,
    )
    print(json.dumps(receipt, indent=2))


def build_companion(*, source, include, python_include, output_dir, cxx):
    """Publish only a compiled, ABI-checked module; preserve existing outputs."""
    output_dir.mkdir(parents=True, exist_ok=True)
    cpp = Path(__file__).with_name("edgellm_cache_config_compat.cpp")
    output = output_dir / ("_edgellm_cache_config_compat" + sysconfig.get_config_var("EXT_SUFFIX"))
    receipt_path = output_dir / "build_receipt.json"
    if any(path.exists() or path.is_symlink() for path in (output, receipt_path)):
        raise FileExistsError("Refusing to overwrite an existing companion or build receipt")
    with tempfile.TemporaryDirectory(prefix=".companion-build-", dir=output_dir) as directory:
        staging = Path(directory)
        staged_module = staging / output.name
        command = [
            cxx,
            "-O2",
            "-shared",
            "-std=c++17",
            "-fPIC",
            "-fvisibility=hidden",
            "-I" + str(include),
            "-I" + str(python_include),
            "-I" + str(source / "cpp"),
            str(cpp),
            "-o",
            str(staged_module),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        (output_dir / "build.log").write_text(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError(f'Companion compilation failed; see {output_dir / "build.log"}')
        spec = importlib.util.spec_from_file_location("_edgellm_cache_config_compat", staged_module)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if module.pybind_internals_id != ABI or module.edgellm_source_commit != COMMIT:
            raise RuntimeError("Compiled companion ABI/source guard failed")
        receipt = {
            "source_commit": COMMIT,
            "header_sha256": HEADER_SHA256,
            "source_sha256": sha(cpp),
            "module_sha256": sha(staged_module),
            "pybind11_version": "3.0.4",
            "pybind_internals_id": ABI,
            "compiler": subprocess.check_output([cxx, "--version"], text=True).splitlines()[0],
            "command": command,
            "native_runtime_modified": False,
            "engines_modified": False,
            "runtime_constructed": False,
            "model_or_engine_loaded": False,
            "gpu_inference_performed": False,
        }
        staged_receipt = staging / "build_receipt.json"
        staged_receipt.write_text(json.dumps(receipt, indent=2) + "\n")
        # link() publishes atomically and refuses to overwrite a file introduced
        # concurrently. If interrupted between links, the visible module is still
        # fully validated and the runtime's configuration check can safely use it.
        os.link(staged_module, output)
        os.link(staged_receipt, receipt_path)
    return receipt


if __name__ == "__main__":
    main()
