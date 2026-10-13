#!/usr/bin/env python3
"""Align legacy image metadata with the bounded processor saved by conversion.

Reproduces the post-export metadata repair used for this checkpoint. Changes
only preprocessor_config.json; model weights, quantization, tokenizer, and
video metadata are untouched. Requires the new processor config to already
contain the validated calibration/evaluation bounds. No heavy imports.
"""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    root = args.checkpoint.resolve(strict=True)
    bounds = {"shortest_edge": 65536, "longest_edge": 1048576}
    modern = json.loads((root / "processor_config.json").read_text())
    if (
        modern.get("processor_class") != "Qwen3VLProcessor"
        or modern["image_processor"].get("size") != bounds
    ):
        raise ValueError(
            "Saved processor does not match the validated image bounds; refusing repair"
        )
    path = root / "preprocessor_config.json"
    legacy = json.loads(path.read_text())
    if legacy.get("processor_class") != "Qwen3VLProcessor":
        raise ValueError("Unexpected legacy processor class")
    for key in ("patch_size", "temporal_patch_size", "merge_size", "image_mean", "image_std"):
        if legacy.get(key) != modern["image_processor"].get(key):
            raise ValueError("Processor geometry/normalization mismatch: " + key)
    if args.check_only:
        if legacy.get("size") != bounds:
            raise ValueError("Legacy image bounds still need repair")
        print("Image metadata bounds agree; no files changed.")
        return
    legacy["size"] = bounds
    for key, value in [("min_pixels", 65536), ("max_pixels", 1048576)]:
        if key in legacy:
            legacy[key] = value
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(legacy, indent=2) + "\n")
    temporary.replace(path)
    print("Aligned preprocessor_config.json image bounds with the saved processor.")


if __name__ == "__main__":
    main()
