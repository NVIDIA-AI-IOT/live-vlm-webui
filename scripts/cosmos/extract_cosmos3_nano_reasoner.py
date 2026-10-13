#!/usr/bin/env python3
"""Extract Nano's BF16 reasoner without importing torch or changing tensor bytes.

No weights are downloaded. --check-only validates local headers and metadata.
The output is an adaptation for Qwen3-VL loaders, NOT a claim of inference parity.
Mapping references:
https://github.com/NVIDIA/cosmos-framework/blob/main/cosmos_framework/inference/model.py
https://github.com/NVIDIA/cosmos-framework/blob/main/cosmos_framework/scripts/convert_model_to_vlm_safetensors.py
"""

import argparse
import collections
import hashlib
import json
import math
import os
import re
import shutil
import struct
import sys
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / "cosmos3_nano_extraction_reference.json"
METADATA_FILES = (
    "tokenizer.json",
    "tokenizer_config.json",
    "merges.txt",
    "chat_template.json",
    "generation_config.json",
    "preprocessor_config.json",
)
OPTIONAL_METADATA_FILES = (
    "vocab.json",
    "special_tokens_map.json",
    "added_tokens.json",
    "video_preprocessor_config.json",
    "processor_config.json",
    "chat_template.jinja",
    "LICENSE",
    "LICENSE.txt",
)
GEN_ROOT = {
    "action_proj_in.bias.weight",
    "action_proj_in.fc.weight",
    "action_proj_out.bias.weight",
    "action_proj_out.fc.weight",
    "audio_proj_in.bias",
    "audio_proj_in.weight",
    "audio_proj_out.bias",
    "audio_proj_out.weight",
    "proj_in.bias",
    "proj_in.weight",
    "proj_out.bias",
    "proj_out.weight",
    "time_embedder.linear_1.bias",
    "time_embedder.linear_1.weight",
    "time_embedder.linear_2.bias",
    "time_embedder.linear_2.weight",
    "action_modality_embed",
    "audio_modality_embed",
    "norm_moe_gen.weight",
}
GEN_LAYER = re.compile(
    r"layers\.(?:[0-9]|[12][0-9]|3[0-5])\."
    r"(?:(?:input_layernorm_moe_gen|post_attention_layernorm_moe_gen)\.weight|"
    r"mlp_moe_gen\.(?:down_proj|gate_proj|up_proj)\.weight|"
    r"self_attn\.(?:add_[qkv]_proj|to_add_out|norm_added_[qk])\.weight)$"
)
ATTENTION_ALIASES = {
    "to_q": "q_proj",
    "to_k": "k_proj",
    "to_v": "v_proj",
    "to_out": "o_proj",
    "norm_q": "q_norm",
    "norm_k": "k_norm",
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def sha256_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def contained_file(root, relative):
    p = Path(relative)
    require(not p.is_absolute() and ".." not in p.parts, f"Unsafe relative path: {relative}")
    result = root / p
    require(result.is_file() and not result.is_symlink(), f"Missing or symlinked input: {result}")
    require(result.resolve().is_relative_to(root.resolve()), f"Input escaped source: {result}")
    return result


def read_header(path):
    size = path.stat().st_size
    with path.open("rb") as stream:
        first = stream.read(8)
        require(len(first) == 8, f"Truncated safetensors file: {path}")
        length = struct.unpack("<Q", first)[0]
        require(
            2 <= length <= 100_000_000 and 8 + length <= size,
            f"Invalid safetensors header length: {path}",
        )
        raw = stream.read(length)
    header = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    require(isinstance(header, dict), f"Invalid header object: {path}")
    tensors = {key: value for key, value in header.items() if key != "__metadata__"}
    cursor = 0
    for key, tensor in sorted(tensors.items(), key=lambda item: item[1]["data_offsets"][0]):
        require(set(tensor) == {"dtype", "shape", "data_offsets"}, f"Bad tensor descriptor: {key}")
        require(tensor["dtype"] == "BF16", f"Expected BF16 tensor: {key}: {tensor['dtype']}")
        shape = tensor["shape"]
        require(
            isinstance(shape, list) and all(type(d) is int and d > 0 for d in shape),
            f"Invalid shape: {key}: {shape}",
        )
        offsets = tensor["data_offsets"]
        require(
            isinstance(offsets, list)
            and len(offsets) == 2
            and all(type(n) is int for n in offsets),
            f"Invalid offsets: {key}",
        )
        start, end = offsets
        require(
            start == cursor and end - start == 2 * math.prod(shape),
            f"Noncontiguous/invalid tensor bytes: {key}",
        )
        cursor = end
    require(8 + length + cursor == size, f"Truncated or trailing tensor payload: {path}")
    return tensors, 8 + length


def map_tensor(key, relative):
    if relative.startswith("vision_encoder/"):
        return "model.visual." + key
    require(relative.startswith("transformer/"), f"Unexpected tensor component: {relative}")
    if key in GEN_ROOT or GEN_LAYER.fullmatch(key):
        return None
    if key == "lm_head.weight":
        return key
    if key in ("embed_tokens.weight", "norm.weight") or key.startswith("layers."):
        for old, new in ATTENTION_ALIASES.items():
            key = key.replace(".self_attn." + old + ".", ".self_attn." + new + ".")
        return "model.language_model." + key
    raise ValueError(f"Unclassified input tensor (refusing to discard): {key}")


def expected_shapes(config):
    t, v = config["text_config"], config["vision_config"]
    h, intermediate = t["hidden_size"], t["intermediate_size"]
    q, kv, head = (
        t["num_attention_heads"] * t["head_dim"],
        t["num_key_value_heads"] * t["head_dim"],
        t["head_dim"],
    )
    shapes = {
        "lm_head.weight": [t["vocab_size"], h],
        "model.language_model.embed_tokens.weight": [t["vocab_size"], h],
        "model.language_model.norm.weight": [h],
    }
    layer = {
        "input_layernorm.weight": [h],
        "post_attention_layernorm.weight": [h],
        "self_attn.q_proj.weight": [q, h],
        "self_attn.k_proj.weight": [kv, h],
        "self_attn.v_proj.weight": [kv, h],
        "self_attn.o_proj.weight": [h, q],
        "self_attn.q_norm.weight": [head],
        "self_attn.k_norm.weight": [head],
        "mlp.gate_proj.weight": [intermediate, h],
        "mlp.up_proj.weight": [intermediate, h],
        "mlp.down_proj.weight": [h, intermediate],
    }
    for i in range(t["num_hidden_layers"]):
        shapes.update(
            {f"model.language_model.layers.{i}.{name}": shape for name, shape in layer.items()}
        )
    h, intermediate, out = v["hidden_size"], v["intermediate_size"], v["out_hidden_size"]
    layer = {
        "norm1.weight": [h],
        "norm1.bias": [h],
        "norm2.weight": [h],
        "norm2.bias": [h],
        "attn.qkv.weight": [3 * h, h],
        "attn.qkv.bias": [3 * h],
        "attn.proj.weight": [h, h],
        "attn.proj.bias": [h],
        "mlp.linear_fc1.weight": [intermediate, h],
        "mlp.linear_fc1.bias": [intermediate],
        "mlp.linear_fc2.weight": [h, intermediate],
        "mlp.linear_fc2.bias": [h],
    }
    for i in range(v["depth"]):
        shapes.update({f"model.visual.blocks.{i}.{name}": shape for name, shape in layer.items()})
    shapes["model.visual.patch_embed.proj.weight"] = [
        h,
        v["in_channels"],
        v["temporal_patch_size"],
        v["patch_size"],
        v["patch_size"],
    ]
    shapes["model.visual.patch_embed.proj.bias"] = [h]
    shapes["model.visual.pos_embed.weight"] = [v["num_position_embeddings"], h]
    merged = h * v["spatial_merge_size"] ** 2
    for prefix, norm_dim in [("merger", h)] + [
        (f"deepstack_merger_list.{i}", merged) for i in range(len(v["deepstack_visual_indexes"]))
    ]:
        for name, shape in {
            "norm.weight": [norm_dim],
            "norm.bias": [norm_dim],
            "linear_fc1.weight": [merged, merged],
            "linear_fc1.bias": [merged],
            "linear_fc2.weight": [out, merged],
            "linear_fc2.bias": [out],
        }.items():
            shapes[f"model.visual.{prefix}.{name}"] = shape
    return shapes


def validate(source, reference):
    config = read_json(source / "config.json")
    require(config.get("model_type") == "cosmos3_omni", "Source must be Cosmos3-Nano omni")
    canonical = reference["qwen_config"]
    for key in canonical:
        if key not in ("architectures", "model_type", "transformers_version"):
            require(config.get(key) == canonical[key], f"Nano/Qwen architecture mismatch: {key}")
    shapes = expected_shapes(canonical)
    require(
        set(shapes) == set(reference["canonical_tensor_names"]),
        "Shape schema differs from canonical Qwen tensor index",
    )
    index = read_json(source / "model.safetensors.index.json")["weight_map"]
    mapped, skipped, by_file = {}, [], collections.defaultdict(set)
    for key, relative in index.items():
        require(isinstance(relative, str), f"Invalid index path: {key}")
        by_file[relative].add(key)
        target = map_tensor(key, relative)
        if target is None:
            skipped.append(key)
            continue
        require(target not in mapped, f"Duplicate target tensor: {target}")
        mapped[target] = {"source_tensor": key, "source_file": relative}
    require(
        set(mapped) == set(shapes),
        f"Tensor mismatch: missing={sorted(set(shapes)-set(mapped))}; unexpected={sorted(set(mapped)-set(shapes))}",
    )
    headers, stamps = {}, {}
    for relative, keys in sorted(by_file.items()):
        path = contained_file(source, relative)
        tensors, data_start = read_header(path)
        require(
            set(tensors) == keys,
            f"Index/header mismatch in {relative}: missing={sorted(keys-set(tensors))}; unindexed={sorted(set(tensors)-keys)}",
        )
        headers[relative] = (tensors, data_start)
        stat = path.stat()
        stamps[relative] = (stat.st_size, stat.st_mtime_ns)
    for target, entry in mapped.items():
        tensors, data_start = headers[entry["source_file"]]
        tensor = tensors[entry["source_tensor"]]
        require(
            tensor["shape"] == shapes[target],
            f"Shape mismatch {target}: {tensor['shape']} != {shapes[target]}",
        )
        entry.update(
            {
                "dtype": tensor["dtype"],
                "shape": tensor["shape"],
                "source_offset": data_start + tensor["data_offsets"][0],
                "nbytes": tensor["data_offsets"][1] - tensor["data_offsets"][0],
            }
        )
    for name in METADATA_FILES:
        contained_file(source, name)
    processor = read_json(source / "preprocessor_config.json")
    require(
        processor.get("processor_class") == "Qwen3VLProcessor",
        "Nano processor is not Qwen3VLProcessor",
    )
    for key, value in (
        ("patch_size", canonical["vision_config"]["patch_size"]),
        ("temporal_patch_size", canonical["vision_config"]["temporal_patch_size"]),
        ("merge_size", canonical["vision_config"]["spatial_merge_size"]),
    ):
        require(processor.get(key) == value, f"Processor dimension mismatch: {key}")
    tokenizer = read_json(source / "tokenizer.json")
    tokens = dict(tokenizer["model"]["vocab"])
    tokens.update({item["content"]: item["id"] for item in tokenizer.get("added_tokens", [])})
    for field, token in (
        ("image_token_id", "<|image_pad|>"),
        ("video_token_id", "<|video_pad|>"),
        ("vision_start_token_id", "<|vision_start|>"),
        ("vision_end_token_id", "<|vision_end|>"),
    ):
        require(tokens.get(token) == config[field], f"Tokenizer special token mismatch: {token}")
    return config, mapped, skipped, stamps


def copy_tensor(src, dst, nbytes):
    remaining, digest = nbytes, hashlib.sha256()
    while remaining:
        block = src.read(min(8 * 1024 * 1024, remaining))
        require(bool(block), "Source truncated while copying")
        if dst is not None:
            dst.write(block)
        digest.update(block)
        remaining -= len(block)
    return digest.hexdigest()


def extract(source, output, reference, config, mapped, skipped, stamps):
    require(not output.exists(), f"Output already exists: {output}")
    require(
        not source.is_relative_to(output) and not output.is_relative_to(source),
        "Source/output directories overlap",
    )
    total = sum(entry["nbytes"] for entry in mapped.values())
    output.parent.mkdir(parents=True, exist_ok=True)
    require(
        shutil.disk_usage(output.parent).free > total + 256 * 1024 * 1024, "Insufficient disk space"
    )
    temporary = output.with_name(output.name + ".extracting")
    temporary.mkdir(exist_ok=False)
    # Deliberately leave an incomplete directory on failure; never publish it as a model.
    write_json(
        temporary / "EXTRACTION_INCOMPLETE.json", {"source": str(source), "validation": "pending"}
    )
    groups = collections.defaultdict(list)
    for target, entry in mapped.items():
        groups[entry["source_file"]].append(target)
    weight_map = {}
    for number, (relative, targets) in enumerate(sorted(groups.items()), 1):
        filename = f"model-{number:05d}-of-{len(groups):05d}.safetensors"
        header, offset = {"__metadata__": {"format": "pt"}}, 0
        targets.sort(key=lambda target: mapped[target]["source_offset"])
        for target in targets:
            e = mapped[target]
            header[target] = {
                "dtype": e["dtype"],
                "shape": e["shape"],
                "data_offsets": [offset, offset + e["nbytes"]],
            }
            weight_map[target] = filename
            offset += e["nbytes"]
        raw = json.dumps(header, separators=(",", ":")).encode("utf-8")
        raw += b" " * (-len(raw) % 8)
        source_path = contained_file(source, relative)
        with source_path.open("rb") as src, (temporary / filename).open("xb") as dst:
            dst.write(struct.pack("<Q", len(raw)))
            dst.write(raw)
            for target in targets:
                e = mapped[target]
                src.seek(e["source_offset"])
                e["sha256"] = copy_tensor(src, dst, e["nbytes"])
            dst.flush()
            os.fsync(dst.fileno())
        tensors, start = read_header(temporary / filename)
        with (temporary / filename).open("rb") as verify:
            for target in targets:
                verify.seek(start + tensors[target]["data_offsets"][0])
                require(
                    copy_tensor(verify, None, mapped[target]["nbytes"]) == mapped[target]["sha256"],
                    f"Copied tensor failed digest verification: {target}",
                )
        print(f"Copied and verified {filename} ({len(targets)} tensors)", flush=True)
    for relative, stamp in stamps.items():
        stat = (source / relative).stat()
        require(
            (stat.st_size, stat.st_mtime_ns) == stamp,
            f"Source changed during extraction: {relative}",
        )
    # Keep Nano's semantic configuration; change only the architecture dispatch.
    extracted_config = {key: config[key] for key in reference["qwen_config"] if key in config}
    extracted_config.update(
        {"architectures": ["Qwen3VLForConditionalGeneration"], "model_type": "qwen3_vl"}
    )
    write_json(temporary / "config.json", extracted_config)
    metadata_hashes = {}
    for name in METADATA_FILES + OPTIONAL_METADATA_FILES:
        if not (source / name).exists():
            continue
        original = contained_file(source, name)
        shutil.copyfile(original, temporary / name)
        digest = sha256_file(original)
        require(digest == sha256_file(temporary / name), f"Metadata copy mismatch: {name}")
        metadata_hashes[name] = digest
    write_json(
        temporary / "model.safetensors.index.json",
        {"metadata": {"total_size": total}, "weight_map": weight_map},
    )
    write_json(
        temporary / "extraction_manifest.json",
        {
            "source_model": "nvidia/Cosmos3-Nano",
            "source_directory": str(source),
            "source_config_sha256": sha256_file(source / "config.json"),
            "source_index_sha256": sha256_file(source / "model.safetensors.index.json"),
            "reference": reference["provenance"],
            "script_sha256": sha256_file(Path(__file__)),
            "tensor_count": len(mapped),
            "total_tensor_bytes": total,
            "tensors": mapped,
            "discarded_generation_tensors": sorted(skipped),
            "metadata_sha256": metadata_hashes,
            "validation": {
                "canonical_key_and_shape_coverage": "passed",
                "tensor_bytes_sha256": "passed",
                "original_nano_inference_parity": "PENDING",
                "tensorrt_edge_llm_support": "PENDING",
                "quantized_task_accuracy": "PENDING",
            },
        },
    )
    (temporary / "EXTRACTION_INCOMPLETE.json").unlink()
    require(not output.exists(), f"Output appeared during extraction: {output}")
    temporary.rename(output)
    print(
        f"Extracted {len(mapped)} BF16 tensors, {total:,} bytes to {output}. Inference parity is PENDING."
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Validate metadata and headers without copying tensors",
    )
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    reference = read_json(REFERENCE)
    config, mapped, skipped, stamps = validate(source, reference)
    print(
        f"Validated {len(mapped)} required reasoner tensors, {len(skipped)} explicitly excluded generation tensors; {sum(e['nbytes'] for e in mapped.values()):,} output tensor bytes."
    )
    if not args.check_only:
        extract(source, output, reference, config, mapped, skipped, stamps)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, TypeError) as error:
        sys.exit(f"Extraction refused: {error}")
