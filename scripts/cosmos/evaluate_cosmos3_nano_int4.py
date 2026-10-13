#!/usr/bin/env python3
"""Bounded Nano reasoner evaluation; importing this file starts no GPU work.

prepare-data: pin MMMU, separate calibration/holdout by ID AND image content.
baseline: evaluate a native Cosmos3 or extracted Qwen3-VL checkpoint.
compare: report paired accuracy and optional preprocessing/logit parity.

Conversion integration (evaluate before export changes the model's weights):
    cases = load_cases(data_dir, "heldout")
    evaluate_model(model, processor, cases, "bf16", report_dir)
    # NVIDIA ModelOpt mtq.quantize(model, cfg, forward_loop=...) mutates model.
    evaluate_model(model, processor, cases, "awq_fake_quant", report_dir)

Fake-quant evaluation does NOT validate packed weights or TensorRT kernels.
No small sample result establishes general-purpose accuracy or non-inferiority.
"""

import argparse
import ast
import collections
import hashlib
import json
import math
import random
import re
import time
from pathlib import Path


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]


def _write_jsonl(path, rows):
    Path(path).write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))


def _image_digest(image):
    image = image.convert("RGB")
    return hashlib.sha256(str(image.size).encode() + b"\0" + image.tobytes()).hexdigest()


def _mc_question(row):
    options = row.get("options", [])
    if isinstance(options, str):
        options = ast.literal_eval(options)
    if not isinstance(options, list) or not 2 <= len(options) <= 26:
        return None
    answer = str(row.get("answer", "")).strip().upper()
    letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"[: len(options)]
    if answer not in letters or len(answer) != 1:
        return None
    question = re.sub(r"<image\s*\d+>", "", str(row.get("question", ""))).strip()
    if not question:
        return None
    question += "\n\n" + "\n".join(
        f"{letter}. {option}" for letter, option in zip(letters, options)
    )
    question += "\n\nAnswer with only the letter of the correct option."
    return question, answer, list(letters)


def prepare_data(
    output,
    calibration_count=128,
    heldout_count=64,
    seed=20261003,
    dataset_id="lmms-lab/MMMU",
    revision=None,
    max_rows=1200,
):
    """Fetch a bounded number of public examples via HF streaming, retaining images.

    Uses single-image multiple-choice questions only. Both arms use exactly the
    same restricted sample: this is NOT an official full MMMU benchmark score.
    Calibration JSONL contains no answers. Evaluation IDs and image digests are
    disjoint from calibration; both splits come from the pinned validation pool.
    """
    from datasets import get_dataset_config_names, load_dataset
    from huggingface_hub import HfApi

    output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"Refusing to overwrite an existing data directory: {output}")
    if calibration_count < 1 or heldout_count < 1:
        raise ValueError("Both sample counts must be positive")
    revision = HfApi().dataset_info(dataset_id, revision=revision).sha
    configurations = sorted(get_dataset_config_names(dataset_id, revision=revision))
    if not configurations:
        raise ValueError("No dataset configurations found")
    output.mkdir(parents=True)
    (output / "PREPARATION_INCOMPLETE").write_text("Not ready for calibration or evaluation\n")
    (output / "images").mkdir()
    # Current lmms-lab/MMMU uses one configuration named default. Actual
    # subjects are encoded by validation_<subject>_<number>; subfield is a
    # narrower topic (e.g. Managerial Accounting), not the 30-way subject.
    quota = max(10, math.ceil((calibration_count + heldout_count) / 30) + 3)
    pools, seen_ids, seen_images = collections.defaultdict(list), set(), set()
    scans = collections.Counter()
    scanned_total = 0
    for configuration in configurations:
        rows = load_dataset(
            dataset_id, configuration, split="validation", revision=revision, streaming=True
        )
        for row in rows:
            scanned_total += 1
            if scanned_total > max_rows:
                raise ValueError(
                    f"Dataset scan exceeded the bounded {max_rows} rows; refusing incomplete subject coverage"
                )
            identifier = str(row.get("id", ""))
            match = re.fullmatch(r"validation_(.+)_\d+", identifier)
            if not match:
                raise ValueError(f"Cannot determine MMMU subject from ID: {identifier!r}")
            subject = match.group(1)
            scans[subject] += 1
            if len(pools[subject]) >= quota:
                continue
            if identifier in seen_ids or row.get("question_type") != "multiple-choice":
                continue
            images = [
                row.get(f"image_{i}") for i in range(1, 8) if row.get(f"image_{i}") is not None
            ]
            if not images and row.get("image") is not None:
                images = [row["image"]]
            if len(images) != 1:
                continue
            parsed = _mc_question(row)
            if parsed is None:
                continue
            image = images[0].convert("RGB")
            digest = _image_digest(image)
            if digest in seen_images:
                continue
            question, answer, letters = parsed
            image_path = output / "images" / f"{digest}.png"
            image.save(image_path)
            pools[subject].append(
                {
                    "id": identifier,
                    "subject": subject,
                    "subfield": row.get("subfield"),
                    "image": str(image_path.relative_to(output)),
                    "image_sha256": digest,
                    "question": question,
                    "answer": answer,
                    "allowed_answers": letters,
                    "original_image_size": list(image.size),
                }
            )
            seen_ids.add(identifier)
            seen_images.add(digest)
    subjects = sorted(pools)
    if len(subjects) != 30:
        raise ValueError(
            f"Expected all 30 MMMU subjects, saw {len(subjects)}; refusing misleading balanced-sample claim"
        )
    random.Random(seed).shuffle(subjects)
    scans = {
        subject: {"rows_scanned": scans[subject], "eligible_unique_retained": len(pools[subject])}
        for subject in subjects
    }
    for subject in subjects:
        random.Random(f"{seed}:{subject}").shuffle(pools[subject])
        print(
            f"Prepared actual subject {subject}: {len(pools[subject])} eligible unique items",
            flush=True,
        )

    def take_balanced(count):
        chosen = []
        while len(chosen) < count:
            progress = False
            for subject in subjects:
                if pools[subject] and len(chosen) < count:
                    chosen.append(pools[subject].pop(0))
                    progress = True
            if not progress:
                raise ValueError(f"Not enough usable unique items to fill {count} cases")
        return chosen

    # Each arm independently cycles through every actual subject. A global
    # every-third-row assignment would accidentally place entire subjects in
    # one arm when there are 30 subjects, a multiple of three.
    cal = take_balanced(calibration_count)
    held = take_balanced(heldout_count)
    if {x["subject"] for x in cal} != set(subjects) or {x["subject"] for x in held} != set(
        subjects
    ):
        raise ValueError("Every subject must occur in BOTH calibration and evaluation")
    assert len(cal) == calibration_count and len(held) == heldout_count
    assert not ({x["id"] for x in cal} & {x["id"] for x in held})
    assert not ({x["image_sha256"] for x in cal} & {x["image_sha256"] for x in held})
    calibration = [{k: v for k, v in row.items() if k not in ("answer",)} for row in cal]
    _write_jsonl(output / "calibration.jsonl", calibration)
    _write_jsonl(output / "heldout.jsonl", held)
    _write_json(
        output / "data_manifest.json",
        {
            "dataset": dataset_id,
            "revision": revision,
            "source_split": "validation",
            "seed": seed,
            "selection": "All 30 actual ID-derived subjects in each arm; separate round-robin allocation; single-image multiple-choice; unique IDs and RGB pixel digests",
            "dataset_configurations": configurations,
            "rows_scanned_total": scanned_total,
            "actual_subject_count": len(subjects),
            "per_subject_candidate_cap": quota,
            "calibration_count": len(cal),
            "heldout_count": len(held),
            "id_overlap": [],
            "image_sha256_overlap": [],
            "calibration_subjects": dict(collections.Counter(x["subject"] for x in cal)),
            "heldout_subjects": dict(collections.Counter(x["subject"] for x in held)),
            "scans": scans,
            "calibration_jsonl_sha256": hashlib.sha256(
                (output / "calibration.jsonl").read_bytes()
            ).hexdigest(),
            "heldout_jsonl_sha256": hashlib.sha256(
                (output / "heldout.jsonl").read_bytes()
            ).hexdigest(),
            "limitations": [
                "Restricted small MMMU subset; not an official full benchmark score",
                "Not an evaluation of robot deployment, native video, or TensorRT kernels",
            ],
        },
    )
    (output / "PREPARATION_INCOMPLETE").unlink()
    return output


def load_cases(data_dir, suite="heldout", limit=None):
    data_dir = Path(data_dir)
    if (data_dir / "PREPARATION_INCOMPLETE").exists():
        raise ValueError("Dataset preparation is incomplete")
    manifest = json.loads((data_dir / "data_manifest.json").read_text())
    for name in ("calibration", "heldout"):
        path = data_dir / f"{name}.jsonl"
        if hashlib.sha256(path.read_bytes()).hexdigest() != manifest[f"{name}_jsonl_sha256"]:
            raise ValueError(f"Dataset manifest hash mismatch: {name}")
    cal, held = _read_jsonl(data_dir / "calibration.jsonl"), _read_jsonl(data_dir / "heldout.jsonl")
    for key in ("id", "image_sha256"):
        if {x[key] for x in cal} & {x[key] for x in held}:
            raise ValueError(f"Calibration/evaluation overlap: {key}")
    if suite == "calibration":
        cases = cal
    elif suite == "heldout":
        cases = held
    elif suite == "smoke":
        cases = [
            {
                "id": "text_arithmetic",
                "question": "What is 7 multiplied by 8? Answer with only the number.",
            },
            {"id": "text_instruction", "question": "Reply with exactly READY."},
        ] + held[:3]
    else:
        raise ValueError(f"Unknown suite: {suite}")
    cases = cases[:limit] if limit is not None else cases
    for row in cases:
        if row.get("image") and not Path(row["image"]).is_absolute():
            row["image"] = str((data_dir / row["image"]).resolve())
    return cases


def calibration_pairs(data_dir):
    """Pass a zero-argument lambda calling this to image_dataset in the0.11 API."""
    from PIL import Image

    for row in load_cases(data_dir, "calibration"):
        with Image.open(row["image"]) as image:
            image = image.convert("RGB")
            if _image_digest(image) != row["image_sha256"]:
                raise ValueError(f"Image checksum changed: {row['id']}")
            yield image, row["question"]


def _tensor_digest(tensor):
    import torch

    tensor = tensor.detach().contiguous().cpu()
    h = hashlib.sha256()
    h.update(str(tensor.dtype).encode())
    h.update(json.dumps(list(tensor.shape)).encode())
    h.update(tensor.view(torch.uint8).numpy().tobytes())
    return h.hexdigest()


def _answer(text, allowed):
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    if text in allowed:
        return text
    found = re.fullmatch(
        r"(?:[Tt]he\s+)?(?:[Aa]nswer(?:\s+is)?\s*[:：]?\s*)?\(?([A-Z])\)?[.。]?", text
    )
    return found.group(1) if found and found.group(1) in allowed else None


def evaluate_model(
    model,
    processor,
    cases,
    label,
    output_dir,
    *,
    max_new_tokens=256,
    save_logits=False,
    device="cuda",
    seed=20261003,
):
    """Evaluate a loaded model, including a ModelOpt in-memory fake-quant model.

    Set processor image limits before calling, identically in all arms. Uses
    greedy decoding, records exact preprocessed tensors, never forces an answer.
    Output format failures count as incorrect, rather than being discarded.
    """
    import numpy as np
    import torch
    from PIL import Image

    if not re.fullmatch(r"[A-Za-z0-9_-]+", label):
        raise ValueError("Report label must contain only letters, digits, underscore, or hyphen")
    directory = Path(output_dir).resolve() / label
    directory.mkdir(parents=True, exist_ok=False)
    model.eval()
    torch.manual_seed(seed)
    eos = model.generation_config.eos_token_id
    eos_ids = set(eos if isinstance(eos, (list, tuple)) else [eos]) - {None}
    report = {
        "label": label,
        "model_class": type(model).__name__,
        "seed": seed,
        "model_dtype": str(next(model.parameters()).dtype),
        "eos_token_ids": sorted(eos_ids),
        "decoding": {"do_sample": False, "num_beams": 1, "max_new_tokens": max_new_tokens},
        "image_processor": (
            processor.image_processor.to_dict() if hasattr(processor, "image_processor") else None
        ),
        "rows": [],
        "complete": False,
        "scope": "Greedy single-image/text reference inference; in-memory AWQ is fake-quant, not TensorRT execution",
    }
    for case in cases:
        start = time.monotonic()
        content = []
        if case.get("image"):
            with Image.open(case["image"]) as original:
                image = original.convert("RGB")
            if _image_digest(image) != case["image_sha256"]:
                raise ValueError(f"Image checksum changed: {case['id']}")
            content.append({"type": "image", "image": image})
        content.append({"type": "text", "text": case["question"]})
        inputs = processor.apply_chat_template(
            [{"role": "user", "content": content}],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
        inputs = {k: v for k, v in inputs.items() if isinstance(v, torch.Tensor) and v.numel()}
        fingerprints = {
            k: {"shape": list(v.shape), "dtype": str(v.dtype), "sha256": _tensor_digest(v)}
            for k, v in inputs.items()
        }
        dtype = next(model.parameters()).dtype
        inputs = {
            k: v.to(device=device, dtype=dtype if v.is_floating_point() else v.dtype)
            for k, v in inputs.items()
        }
        prompt_tokens = inputs["input_ids"].shape[-1]
        with torch.inference_mode():
            output = model.generate(
                **inputs,
                do_sample=False,
                num_beams=1,
                max_new_tokens=max_new_tokens,
                return_dict_in_generate=True,
                output_logits=save_logits,
                pad_token_id=processor.tokenizer.pad_token_id,
            )
        generated = output.sequences[0, prompt_tokens:].detach().cpu().tolist()
        text = processor.tokenizer.decode(generated, skip_special_tokens=True).strip()
        prediction = _answer(text, case["allowed_answers"]) if "allowed_answers" in case else None
        ended_with_eos = bool(generated and generated[-1] in eos_ids)
        truncated = len(generated) >= max_new_tokens and not ended_with_eos
        unclosed_thinking = text.count("<think>") > text.count("</think>")
        row = {
            "id": case["id"],
            "subject": case.get("subject"),
            "question": case["question"],
            "image_sha256": case.get("image_sha256"),
            "input_tensors": fingerprints,
            "generated_token_ids": generated,
            "text": text,
            "prediction": prediction,
            "finish_reason": "eos" if ended_with_eos else ("length" if truncated else "other"),
            "truncated": truncated,
            "unclosed_thinking": unclosed_thinking,
            "expected_answer": case.get("answer"),
            "correct": prediction == case["answer"] if "answer" in case else None,
            "format_valid": prediction is not None if "allowed_answers" in case else None,
            "prompt_token_count": prompt_tokens,
            "elapsed_seconds": time.monotonic() - start,
        }
        if save_logits:
            logits = output.logits[0][0].detach().float().cpu()
            row["logits_finite"] = bool(torch.isfinite(logits).all())
            if not row["logits_finite"]:
                raise ValueError(f"Non-finite logits on {case['id']}")
            filename = f"logits-{len(report['rows']):04d}.npy"
            np.save(directory / filename, logits.numpy(), allow_pickle=False)
            row["first_token_logits_file"] = filename
        report["rows"].append(row)
        _write_json(directory / "results.json", report)
        print(
            f"{label} {len(report['rows'])}/{len(cases)} {case['id']}: {text[:100]!r}", flush=True
        )
        del inputs, output
    scored = [row for row in report["rows"] if row["correct"] is not None]
    report.update(
        {
            "complete": True,
            "n": len(report["rows"]),
            "scored_n": len(scored),
            "accuracy": sum(row["correct"] for row in scored) / len(scored) if scored else None,
            "invalid_answer_count": sum(row["format_valid"] is False for row in scored),
            "truncation_count": sum(row["truncated"] for row in report["rows"]),
            "unclosed_thinking_count": sum(row["unclosed_thinking"] for row in report["rows"]),
        }
    )
    _write_json(directory / "results.json", report)
    return directory / "results.json"


def _wilson(correct, count):
    if not count:
        return None
    z = 1.959963984540054
    p = correct / count
    den = 1 + z * z / count
    center = (p + z * z / (2 * count)) / den
    radius = z * math.sqrt(p * (1 - p) / count + z * z / (4 * count * count)) / den
    return [max(0, center - radius), min(1, center + radius)]


def compare_reports(reference_file, candidate_file, output_file=None):
    import numpy as np

    reference_file, candidate_file = Path(reference_file), Path(candidate_file)
    ref, cand = json.loads(reference_file.read_text()), json.loads(candidate_file.read_text())
    if not ref["complete"] or not cand["complete"]:
        raise ValueError("Cannot compare incomplete evaluations")
    if ref["decoding"] != cand["decoding"]:
        raise ValueError("Decoding parameters differ between arms")
    a = {row["id"]: row for row in ref["rows"]}
    b = {row["id"]: row for row in cand["rows"]}
    if len(a) != len(ref["rows"]) or len(b) != len(cand["rows"]) or set(a) != set(b):
        raise ValueError("Comparison requires identical unique case IDs")
    rows = []
    for identifier, x in a.items():
        y = b[identifier]
        for field in ("question", "image_sha256", "expected_answer"):
            if x[field] != y[field]:
                raise ValueError(f"Different {field}: {identifier}")
        row = {
            "id": identifier,
            "preprocessing_identical": x["input_tensors"] == y["input_tensors"],
            "generated_tokens_identical": x["generated_token_ids"] == y["generated_token_ids"],
            "reference_correct": x["correct"],
            "candidate_correct": y["correct"],
        }
        if x.get("first_token_logits_file") and y.get("first_token_logits_file"):
            u = np.load(
                reference_file.parent / x["first_token_logits_file"], allow_pickle=False
            ).astype(np.float64)
            v = np.load(
                candidate_file.parent / y["first_token_logits_file"], allow_pickle=False
            ).astype(np.float64)
            if u.shape != v.shape or not np.isfinite(u).all() or not np.isfinite(v).all():
                raise ValueError(f"Invalid comparison logits: {identifier}")
            row.update(
                {
                    "logits_cosine": float(
                        u @ v / max(np.linalg.norm(u) * np.linalg.norm(v), 1e-30)
                    ),
                    "logits_max_abs_error": float(np.max(np.abs(u - v))),
                    "logits_relative_l2_error": float(
                        np.linalg.norm(u - v) / max(np.linalg.norm(u), 1e-30)
                    ),
                    "argmax_identical": int(u.argmax()) == int(v.argmax()),
                }
            )
        rows.append(row)
    matched_inputs = all(row["preprocessing_identical"] for row in rows)
    if not matched_inputs:
        raise ValueError(
            "Preprocessing differs between arms; paired accuracy comparison is invalid"
        )
    scored = [row for row in rows if row["reference_correct"] is not None]
    n = len(scored)
    lost = sum(row["reference_correct"] and not row["candidate_correct"] for row in scored)
    gained = sum(not row["reference_correct"] and row["candidate_correct"] for row in scored)
    disagreement = lost + gained
    pvalue = (
        min(
            1.0,
            2
            * sum(math.comb(disagreement, k) for k in range(min(lost, gained) + 1))
            / 2**disagreement,
        )
        if disagreement
        else 1.0
    )
    result = {
        "reference": str(reference_file),
        "candidate": str(candidate_file),
        "n": len(rows),
        "scored_n": n,
        "preprocessing_identical": matched_inputs,
        "exact_generation_match_fraction": (
            sum(row["generated_tokens_identical"] for row in rows) / len(rows) if rows else None
        ),
        "reference_accuracy": ref.get("accuracy"),
        "candidate_accuracy": cand.get("accuracy"),
        "reference_truncation_count": ref.get("truncation_count", 0),
        "candidate_truncation_count": cand.get("truncation_count", 0),
        "reference_unclosed_thinking_count": ref.get("unclosed_thinking_count", 0),
        "candidate_unclosed_thinking_count": cand.get("unclosed_thinking_count", 0),
        "accuracy_delta_percentage_points": 100 * (gained - lost) / n if n else None,
        "reference_accuracy_wilson_95": _wilson(sum(row["reference_correct"] for row in scored), n),
        "candidate_accuracy_wilson_95": _wilson(sum(row["candidate_correct"] for row in scored), n),
        "correct_to_incorrect": lost,
        "incorrect_to_correct": gained,
        "exact_mcnemar_p": pvalue if n else None,
        "rows": rows,
        "limitations": [
            "A small heldout subset cannot establish general accuracy or a <=1 percentage-point degradation bound",
            "Non-significant McNemar p does not demonstrate equivalence",
            "Fake-quant results do not verify packed checkpoint export, TensorRT execution, or video understanding",
        ],
    }
    if output_file:
        _write_json(output_file, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare-data")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--calibration-count", type=int, default=128)
    prepare.add_argument("--heldout-count", type=int, default=64)
    prepare.add_argument("--revision")
    baseline = commands.add_parser("baseline")
    baseline.add_argument("--model", required=True)
    baseline.add_argument("--label", required=True)
    baseline.add_argument("--data-dir", type=Path, required=True)
    baseline.add_argument("--output-dir", type=Path, required=True)
    baseline.add_argument("--suite", choices=("smoke", "heldout"), default="smoke")
    baseline.add_argument("--limit", type=int)
    baseline.add_argument("--save-logits", action="store_true")
    baseline.add_argument("--max-new-tokens", type=int, default=256)
    baseline.add_argument("--max-pixels", type=int, default=1048576)
    baseline.add_argument("--min-pixels", type=int, default=65536)
    baseline.add_argument("--attn-implementation", default="sdpa")
    baseline.add_argument("--dtype", choices=("fp16", "bf16"), default="bf16")
    comparison = commands.add_parser("compare")
    comparison.add_argument("--reference", type=Path, required=True)
    comparison.add_argument("--candidate", type=Path, required=True)
    comparison.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare-data":
        print(
            prepare_data(
                args.output, args.calibration_count, args.heldout_count, revision=args.revision
            )
        )
    elif args.command == "compare":
        result = compare_reports(args.reference, args.candidate, args.output)
        print(json.dumps({k: v for k, v in result.items() if k != "rows"}, indent=2))
    else:
        import torch
        from transformers import AutoConfig, AutoModelForImageTextToText, AutoProcessor

        config = AutoConfig.from_pretrained(args.model)
        processor = AutoProcessor.from_pretrained(
            args.model, min_pixels=args.min_pixels, max_pixels=args.max_pixels
        )
        dtype = torch.float16 if args.dtype == "fp16" else torch.bfloat16
        model = AutoModelForImageTextToText.from_pretrained(
            args.model,
            config=config,
            dtype=dtype,
            attn_implementation=args.attn_implementation,
            low_cpu_mem_usage=True,
        ).to("cuda")
        result = evaluate_model(
            model,
            processor,
            load_cases(args.data_dir, args.suite, args.limit),
            args.label,
            args.output_dir,
            max_new_tokens=args.max_new_tokens,
            save_logits=args.save_logits,
        )
        print(result)


if __name__ == "__main__":
    main()
