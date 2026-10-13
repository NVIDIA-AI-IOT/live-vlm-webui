#!/usr/bin/env python3
"""Check a running Thor demo; optionally run and clean up a 2/4-stream model smoke."""

# SPDX-License-Identifier: Apache-2.0
import argparse
import json
import math
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


def smoke(base, insecure=False, cameras=0, temporal=False, timeout=90):
    if type(cameras) is not int or cameras not in (0, 2, 4):
        raise ValueError("cameras must be 0 (readiness only), 2, or 4")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("timeout must be a positive number of seconds")
    context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
    base = base.rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ValueError("Use an HTTP(S) WebUI URL without credentials")

    def request(path, method="GET", data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(
            base + path, data=body, method=method, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            return json.load(response)

    models = request("/models")
    status = request("/api/segmentation/status")
    if not status.get("available"):
        raise RuntimeError(f"Segmentation unavailable: {status.get('error', status)}")
    if not models.get("models"):
        raise RuntimeError("VLM model discovery returned no models")
    report = {
        "api": base,
        "models": [m.get("id") for m in models["models"]],
        "segmentation_backend": status.get("backend"),
        "inference_tested": False,
    }
    if not cameras:
        return report
    catalog = request("/api/demo/videos")
    clips = [c for c in catalog["videos"] if c.get("available") and c.get("preferred_for_realtime")]
    if len(clips) < cameras:
        raise RuntimeError(f"Install {cameras} recorded store clips before this inference smoke")
    session_id = "portability-check-" + uuid.uuid4().hex
    path = "/api/multi-camera/sessions/" + session_id
    segmentation = {
        "enabled": True,
        "target_fps": 25,
        "confidence": status.get("recommended_confidence", 0.25),
    }
    if status.get("fixed_classes"):
        segmentation.update(classes=status["fixed_classes"], imgsz=status["fixed_imgsz"])
    payload = {
        "session_id": session_id,
        "preview_mode": "segmented",
        "vlm_enabled": True,
        "vlm": {"max_tokens": 32, "prompt": "Describe the visible action in one short sentence."},
        "process_every": 25,
        "segmentation": segmentation,
        "temporal": {"enabled": temporal, "window_seconds": 2, "sample_fps": 2},
        "cameras": [
            {"camera_id": f"camera-{i+1}", "source": "demo", "demo_id": clip["demo_id"]}
            for i, clip in enumerate(clips[:cameras])
        ],
    }
    cleanup = True
    try:
        try:
            request("/api/multi-camera/sessions", "POST", payload)
        except urllib.error.HTTPError as error:
            # A collision means this ID belongs to somebody else's session.
            # Ambiguous transport failures still need cleanup in case POST succeeded.
            if error.code == 409:
                cleanup = False
            raise
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            snapshot = request(path)
            results = snapshot.get("cameras", [])
            failures = [c.get("error") or c.get("segmentation", {}).get("error") for c in results]
            if any(failures):
                raise RuntimeError(f"Camera inference failed: {failures}")
            if len(results) == cameras and all(
                c.get("metrics", {}).get("last_request_succeeded") is True
                and c.get("segmentation", {}).get("metrics", {}).get("frames_completed", 0) > 0
                and (not temporal or c["metrics"].get("temporal_enabled") is True)
                for c in results
            ):
                report.update(
                    inference_tested=True,
                    temporal=temporal,
                    cameras=[
                        {
                            "camera_id": c["camera_id"],
                            "vlm_e2e_ms": c["metrics"].get("last_e2e_latency_ms"),
                            "segmentation_fps": c["segmentation"]["metrics"].get("fps"),
                            "temporal_frames": c["metrics"].get("temporal_frame_count"),
                        }
                        for c in results
                    ],
                )
                return report
            time.sleep(1)
        raise RuntimeError(
            "Timed out waiting for successful VLM and segmentation results on every stream"
        )
    finally:
        if cleanup:
            try:
                request(path, "DELETE")
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise RuntimeError(
                        f"Could not remove smoke session {session_id}: {error}"
                    ) from error


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="https://localhost:8090")
    parser.add_argument(
        "--insecure", action="store_true", help="Accept the demo's self-signed certificate"
    )
    parser.add_argument(
        "--cameras",
        type=int,
        choices=(2, 4),
        default=0,
        help="Run real model inference on installed recorded clips (otherwise readiness only)",
    )
    parser.add_argument("--temporal", action="store_true")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()
    try:
        print(
            json.dumps(
                smoke(args.url, args.insecure, args.cameras, args.temporal, args.timeout), indent=2
            )
        )
    except (RuntimeError, ValueError, OSError) as error:
        parser.exit(1, f"Thor smoke check: {error}\n")


if __name__ == "__main__":
    main()
