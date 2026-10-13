#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Fetch pinned NVIDIA TensorRT/CUDA packages into a private directory.

No sudo, apt installation, driver changes, or package maintainer scripts are
used. This optional runtime is shared by retail YOLOE export and local Cosmos.
The default PyTorch segmentation image does not require this preparation.
"""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).with_name("thor_runtime_packages.json")
CUDA_RELATIVE = Path("usr/local/cuda-13.2")
REQUIRED_PATHS = (
    "usr/lib/python3.12/dist-packages/tensorrt/__init__.py",
    "usr/lib/aarch64-linux-gnu/libnvinfer.so.10",
    "usr/lib/aarch64-linux-gnu/libnvinfer_plugin.so.10",
    "usr/lib/aarch64-linux-gnu/libnvinfer_vc_plugin.so.10",
    "usr/lib/aarch64-linux-gnu/libnvonnxparser.so.10",
    "usr/lib/aarch64-linux-gnu/libnvinfer_builder_resource_sm110.so.10.16.2",
    "usr/local/cuda-13.2/lib64/libcudart.so.13",
    "usr/local/cuda-13.2/lib64/libcublas.so.13",
    "usr/local/cuda-13.2/lib64/libcublasLt.so.13",
    "usr/local/cuda-13.2/lib64/libnvrtc.so.13",
    "usr/local/cuda-13.2/include/cuda_runtime_api.h",
)


def digest(path):
    checksum = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def recipe_digest(manifest):
    return hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()


def load_manifest():
    return json.loads(MANIFEST_PATH.read_text())


def check_runtime(prefix, manifest):
    receipt = prefix / "runtime-receipt.json"
    if not receipt.is_file():
        raise ValueError(f"No prepared runtime receipt at {receipt}; run preparation first")
    data = json.loads(receipt.read_text())
    if data.get("recipe_sha256") != recipe_digest(manifest):
        raise ValueError(f"Runtime recipe mismatch at {prefix}; choose a new empty --prefix")
    for relative in REQUIRED_PATHS:
        if not (prefix / relative).is_file():
            raise ValueError(f"Incomplete runtime: {prefix / relative}")
    return data


def ensure_asset(package, cache_dir):
    destination = cache_dir / package["url"].rsplit("/", 1)[1]
    if destination.exists():
        if digest(destination) != package["sha256"]:
            raise ValueError(f"Checksum mismatch; existing file left unchanged: {destination}")
        return destination
    descriptor, filename = tempfile.mkstemp(prefix=f".{destination.name}.", dir=cache_dir)
    os.close(descriptor)
    temporary = Path(filename)
    try:
        print(f"Downloading {package['name']} {package['version']}", flush=True)
        subprocess.run(
            [
                "curl",
                "--fail",
                "--location",
                "--silent",
                "--show-error",
                "--retry",
                "3",
                "--connect-timeout",
                "15",
                "--max-time",
                "1800",
                "--proto",
                "=https",
                "--proto-redir",
                "=https",
                "--tlsv1.2",
                "--output",
                str(temporary),
                package["url"],
            ],
            check=True,
        )
        if digest(temporary) != package["sha256"]:
            raise ValueError(f"Downloaded package checksum mismatch: {package['name']}")
        temporary.replace(destination)
        return destination
    finally:
        temporary.unlink(missing_ok=True)


def prepare(prefix, cache_dir, *, manifest=None, check_only=False, dry_run=False):
    manifest = load_manifest() if manifest is None else manifest
    prefix = prefix.expanduser().resolve()
    cache_dir = cache_dir.expanduser().resolve()
    if cache_dir == prefix or cache_dir.is_relative_to(prefix):
        raise ValueError("--cache-dir must be outside --prefix for atomic runtime preparation")
    plan = {
        "prefix": str(prefix),
        "cuda_root": str(prefix / CUDA_RELATIVE),
        "download_bytes": sum(package["size"] for package in manifest["packages"]),
        "packages": manifest["packages"],
    }
    if dry_run:
        print(json.dumps(plan, indent=2))
        return plan
    if check_only:
        result = check_runtime(prefix, manifest)
        print(f"Verified local runtime prerequisites: {prefix}")
        return result
    if prefix.exists():
        result = check_runtime(prefix, manifest)
        print(f"Reusing verified runtime: {prefix}")
        return result
    for command in ("curl", "dpkg-deb"):
        if not shutil.which(command):
            raise ValueError(f"Required command is missing: {command}")
    prefix.parent.mkdir(parents=True, exist_ok=True)
    # Serialize setup of one prefix; readers only see a fully published directory.
    with (prefix.parent / f".{prefix.name}.prepare.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if prefix.exists():
            return check_runtime(prefix, manifest)
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(
            prefix=f".{prefix.name}.prepare-", dir=prefix.parent
        ) as temp:
            staging = Path(temp) / "runtime"
            staging.mkdir()
            for package in manifest["packages"]:
                archive = ensure_asset(package, cache_dir)
                subprocess.run(["dpkg-deb", "--extract", str(archive), str(staging)], check=True)
            # Debian's alternatives/postinstall hooks are intentionally not run.
            # Supply only relative aliases within our private CUDA directory.
            cuda_root = staging / CUDA_RELATIVE
            for alias, target in (
                ("lib64", "targets/sbsa-linux/lib"),
                ("include", "targets/sbsa-linux/include"),
            ):
                path = cuda_root / alias
                if not path.exists() and not path.is_symlink():
                    path.symlink_to(target)
            receipt = {
                "recipe_sha256": recipe_digest(manifest),
                "manifest": manifest,
                "scope": "Extracted runtime packages; GPU execution must be checked separately.",
            }
            (staging / "runtime-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            check_runtime(staging, manifest)
            staging.rename(prefix)
    print(f"Prepared TensorRT runtime: {prefix}")
    print(f"CUDA root: {prefix / CUDA_RELATIVE}")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prefix", type=Path, default=REPO_ROOT / ".models/thor-runtime")
    parser.add_argument("--cache-dir", type=Path, default=REPO_ROOT / ".models/downloads/nvidia")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument(
        "--dry-run", action="store_true", help="Print pinned downloads; do not write"
    )
    modes.add_argument(
        "--check", action="store_true", help="Check local prepared files; no network/write"
    )
    args = parser.parse_args()
    try:
        prepare(args.prefix, args.cache_dir, check_only=args.check, dry_run=args.dry_run)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        print(f"Runtime preparation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
