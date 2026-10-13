#!/usr/bin/env python3
"""Run pinned Edge-LLM INT4 AWQ with paired held-out evaluation hooks.

Uses the official quantize_and_export pipeline and ModelOpt packing. Evaluation
before packing checks the in-memory fake-quant model, not the exported runtime.
No GPU work or heavyweight imports occur until main() after argument parsing.
"""

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import random
import subprocess
import sys
import time
import traceback
from pathlib import Path

PINNED_COMMIT_PREFIX = "95515c2"


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, default=str) + "\n")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--num-samples", type=int, default=128)
    parser.add_argument("--evaluation-limit", type=int, default=64)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--min-pixels", type=int, default=65536)
    parser.add_argument("--max-pixels", type=int, default=1048576)
    parser.add_argument("--max-observed-drop-pp", type=float, default=2.0)
    parser.add_argument("--seed", type=int, default=20261003)
    args = parser.parse_args()
    for name in ("repo", "source", "output", "data_dir", "report_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if args.output.exists():
        raise ValueError(f"Refusing to overwrite an existing output: {args.output}")
    if args.report_dir.exists():
        raise ValueError(f"Refusing to overwrite existing reports: {args.report_dir}")
    if args.output.is_relative_to(args.source) or args.source.is_relative_to(args.output):
        raise ValueError("Input/output directories must be separate")
    if not (1 <= args.num_samples <= 128 and args.evaluation_limit > 0):
        raise ValueError("Calibration samples must be 1..128; evaluation limit must be positive")
    if not (0 < args.min_pixels <= args.max_pixels):
        raise ValueError("Invalid image pixel limits")
    commit = subprocess.check_output(
        ["git", "-C", str(args.repo), "rev-parse", "HEAD"], text=True
    ).strip()
    if not commit.startswith(PINNED_COMMIT_PREFIX):
        raise ValueError(f"Expected Edge-LLM v0.11.0 commit {PINNED_COMMIT_PREFIX}, got {commit}")
    dirty = subprocess.check_output(
        ["git", "-C", str(args.repo), "diff", "--name-only", "HEAD", "--", "tensorrt_edgellm"],
        text=True,
    )
    if dirty.strip():
        raise ValueError(f"Official quantizer source has local edits: {dirty}")
    source_config = json.loads((args.source / "config.json").read_text())
    if (
        source_config.get("model_type") != "qwen3_vl"
        or source_config.get("text_config", {}).get("num_hidden_layers") != 36
        or source_config.get("text_config", {}).get("hidden_size") != 4096
    ):
        raise ValueError("Expected the extracted Cosmos3-Nano Qwen3-VL reasoner")
    if not (args.source / "extraction_manifest.json").is_file():
        raise ValueError("Missing Nano reasoner extraction manifest")

    sys.path.insert(0, str(args.repo))
    import evaluate_cosmos3_nano_int4 as evaluation
    import numpy as np
    import torch

    quantizer = importlib.import_module("tensorrt_edgellm.quantization.quantize")
    if not Path(quantizer.__file__).resolve().is_relative_to(args.repo):
        raise ValueError("Imported quantizer did not come from the pinned source checkout")
    if not torch.cuda.is_available():
        raise RuntimeError("A working CUDA PyTorch runtime is required")
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.cuda.manual_seed_all(args.seed)
    torch.backends.cuda.matmul.allow_tf32 = False
    cases = evaluation.load_cases(args.data_dir, "heldout", args.evaluation_limit)
    if len(cases) != args.evaluation_limit:
        raise ValueError(f"Expected {args.evaluation_limit} heldout cases, got {len(cases)}")
    # Check calibration files and hashes up front, retaining only metadata/PIL data.
    calibration_count = sum(1 for _ in evaluation.calibration_pairs(args.data_dir))
    if calibration_count < args.num_samples:
        raise ValueError("Prepared calibration data has fewer cases than requested")
    args.report_dir.mkdir(parents=True, exist_ok=False)
    receipt = {
        "status": "running",
        "arguments": vars(args),
        "edgellm_commit": commit,
        "source_config_sha256": hashlib.sha256(
            (args.source / "config.json").read_bytes()
        ).hexdigest(),
        "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "cuda_device": torch.cuda.get_device_name(),
        "torch_cuda": torch.version.cuda,
        "versions": {},
        "recipe": {
            "backbone": "int4_awq",
            "dtype": "fp16",
            "lm_head": "fp16",
            "visual_encoder_and_deepstack": "fp16",
            "kv_cache": "fp16",
            "calibration": "heldout-disjoint image/question pairs through full VLM",
        },
        "accuracy_certified": False,
        "limitations": [
            "In-memory candidate evaluation uses fake quantization before checkpoint packing",
            "Packed checkpoint and TensorRT engine accuracy require separate validation",
            "A small heldout subset does not establish general or video accuracy",
        ],
    }
    for package in (
        "torch",
        "torchvision",
        "transformers",
        "nvidia-modelopt",
        "datasets",
        "safetensors",
    ):
        try:
            receipt["versions"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            receipt["versions"][package] = "not installed"
    receipt_path = args.report_dir / "quantization_run.json"
    write_json(receipt_path, receipt)
    original_loader = quantizer._load_model
    original_export = quantizer.export_hf_checkpoint
    original_processor = quantizer.AutoProcessor
    state = {}

    class BoundedProcessorFactory:
        @staticmethod
        def from_pretrained(*factory_args, **kwargs):
            kwargs.update(min_pixels=args.min_pixels, max_pixels=args.max_pixels)
            return original_processor.from_pretrained(*factory_args, **kwargs)

    def evaluate(model, processor, label):
        if processor is None:
            raise ValueError("Multimodal processor is required")
        # Both arms get identical deterministic greedy decoding and preprocessing.
        return evaluation.evaluate_model(
            model,
            processor,
            cases,
            label,
            args.report_dir,
            max_new_tokens=args.max_new_tokens,
            save_logits=True,
            device="cuda",
            seed=args.seed,
        )

    def load_with_baseline(*load_args, **load_kwargs):
        if "baseline" in state:
            raise RuntimeError("Unexpected repeated model load")
        model, tokenizer, processor = original_loader(*load_args, **load_kwargs)
        state["processor"] = processor
        state["baseline"] = evaluate(model, processor, "fp16_unquantized")
        receipt["baseline_report"] = str(state["baseline"])
        write_json(receipt_path, receipt)
        return model, tokenizer, processor

    def export_with_evaluation(model, *export_args, **export_kwargs):
        if "candidate" in state:
            raise RuntimeError("Unexpected repeated checkpoint export")
        state["candidate"] = evaluate(model, state["processor"], "int4_awq_fake_quant")
        comparison = evaluation.compare_reports(
            state["baseline"], state["candidate"], args.report_dir / "fp16_vs_int4_fake_quant.json"
        )
        delta = comparison.get("accuracy_delta_percentage_points")
        baseline_report = json.loads(Path(state["baseline"]).read_text())
        candidate_report = json.loads(Path(state["candidate"]).read_text())
        incomplete = any(
            report.get("truncation_count", 0) or report.get("unclosed_thinking_count", 0)
            for report in (baseline_report, candidate_report)
        )
        receipt.update(
            {
                "candidate_report": str(state["candidate"]),
                "observed_accuracy_delta_percentage_points": delta,
                "observed_drop_threshold_pp": args.max_observed_drop_pp,
                "generation_incomplete": incomplete,
                "observed_subset_gate_passed": not incomplete
                and delta is not None
                and delta >= -args.max_observed_drop_pp,
            }
        )
        write_json(receipt_path, receipt)
        # A failed subset gate still produces inspectable candidate weights, clearly
        # marked unapproved. It must never be described as an accuracy-certified model.
        return original_export(model, *export_args, **export_kwargs)

    started = time.monotonic()
    quantizer._load_model = load_with_baseline
    quantizer.export_hf_checkpoint = export_with_evaluation
    quantizer.AutoProcessor = BoundedProcessorFactory
    try:
        quantizer.quantize_and_export(
            model_dir=str(args.source),
            output_dir=str(args.output),
            quantization="int4_awq",
            dtype="fp16",
            device="cuda",
            lm_head_quantization=None,
            visual_quantization=None,
            visual_mha_quantization=None,
            kv_cache_quantization=None,
            audio_quantization=None,
            cp_quantization=None,
            image_dataset=lambda: evaluation.calibration_pairs(args.data_dir),
            num_samples=args.num_samples,
        )
        if "candidate" not in state:
            raise RuntimeError("Expected candidate evaluation hook was not invoked")
        # Official exporter recopies source processor configs after save_pretrained.
        # Persist the exact bounded processor used by calibration and both eval arms.
        protected_metadata = {
            name: (args.output / name).read_bytes()
            for name in ("config.json", "hf_quant_config.json")
            if (args.output / name).is_file()
        }
        state["processor"].save_pretrained(str(args.output))
        for name, original_bytes in protected_metadata.items():
            if (args.output / name).read_bytes() != original_bytes:
                raise RuntimeError(f"Processor saving unexpectedly changed model metadata: {name}")
        receipt["processor_saved_with_validated_limits"] = True
        if not (args.output / "config.json").is_file() or not list(
            args.output.glob("*.safetensors")
        ):
            raise RuntimeError("Quantizer did not produce a checkpoint")
        receipt["status"] = "weights_written_accuracy_not_certified"
        receipt["elapsed_seconds"] = time.monotonic() - started
        write_json(receipt_path, receipt)
        write_json(args.output / "quantization_validation_status.json", receipt)
    except Exception as error:
        receipt.update(
            status="failed",
            error=str(error),
            traceback=traceback.format_exc(),
            elapsed_seconds=time.monotonic() - started,
        )
        write_json(receipt_path, receipt)
        raise
    finally:
        quantizer._load_model = original_loader
        quantizer.export_hf_checkpoint = original_export
        quantizer.AutoProcessor = original_processor
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": receipt["status"],
                "observed_subset_gate_passed": receipt.get("observed_subset_gate_passed"),
                "observed_accuracy_delta_percentage_points": receipt.get(
                    "observed_accuracy_delta_percentage_points"
                ),
                "accuracy_certified": False,
                "report": str(receipt_path),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
