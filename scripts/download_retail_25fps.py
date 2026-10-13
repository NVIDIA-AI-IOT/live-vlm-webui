#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Install four verified native-25-FPS Pexels shopping clips without re-encoding."""

import argparse
import hashlib
import sys
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen

if __package__:
    from .download_retail_demo import atomic_copy, verify_file
else:
    from download_retail_demo import atomic_copy, verify_file

REPO_ROOT = Path(__file__).resolve().parents[1]
LICENSE_URL = "https://www.pexels.com/license/"
CLIPS = {
    "8420772": {
        "bytes": 4073037,
        "sha256": "69f90a91e628304eac08771f278431796e16d7032483cedb5b851614d148208d",
        "source": "woman-looking-at-a-product-inside-a-grocery-store-8420772",
    },
    "8420775": {
        "bytes": 7166140,
        "sha256": "fe80006a60fc28c348aebc72c174afecbe17ae35d0ecc64ee8bab2ae07be3d6e",
        "source": "an-elderly-people-on-a-grocery-store-8420775",
    },
    "8420778": {
        "bytes": 3880250,
        "sha256": "ce1388c3b9c663f64570c9b9bc4260180292559e24c07c33d20b3d73f17c5edc",
        "source": "an-elderly-man-looking-at-a-red-bell-pepper-8420778",
    },
    "8421358": {
        "bytes": 5331267,
        "sha256": "faa4dfe3a5e39a17fa16b5988bab780a64aaec162fe0ccb10d7f37c45a32fbab",
        "source": "couple-paying-for-their-groceries-8421358",
    },
}


def download_url(clip_id):
    return f"https://videos.pexels.com/video-files/{clip_id}/" f"{clip_id}-hd_1920_1080_25fps.mp4"


def attribution_notice():
    lines = [
        "RECORDED NATIVE-25-FPS SHOPPING DEMO FOOTAGE",
        "Creator: Kampus Production; distributed by Pexels",
        f"License: Pexels License — {LICENSE_URL}",
        "Full terms: https://www.pexels.com/terms-of-service/",
        "Source/licensing and source-rate verification date: 2026-10-06",
        "",
    ]
    for clip_id, spec in CLIPS.items():
        lines.extend(
            [
                f"File: pexels-{clip_id}-1080p.mp4",
                f"Source: https://www.pexels.com/video/{spec['source']}/",
                f"Download: {download_url(clip_id)}",
                f"SHA256: {spec['sha256']}",
                "",
            ]
        )
    lines.extend(
        [
            "These are separate recorded shopping scenes, not synchronized cameras.",
            "The official 1080p files are installed unchanged. Their original UHD",
            "sources and published source listings also specify 25 FPS. All 1,113",
            "decoded 1080p frames were unique with 40 ms PTS spacing; there is no",
            "24-to-25 conversion, duplication, interpolation, or speed change here.",
            "This verifies supplied-file cadence, not the original capture hardware.",
            "Playback loops and may resize frames and add live AI overlays and text.",
            "",
            "The Pexels License permits commercial and noncommercial use and",
            "adaptation subject to its restrictions. This demo credits the creator",
            "although attribution is not required. Do not imply endorsement by the",
            "creator or depicted people/brands, portray people offensively, or",
            "redistribute/sell the original footage as standalone stock media.",
            "No model/property releases are supplied by this setup. Full terms",
            "and the source pages govern footage use; this is not a CC BY-SA grant.",
            "",
            "This notice applies only to the four Pexels files above. Intel clips",
            "retain their separate CC BY-SA 4.0 NOTICE.txt. Application source code",
            "has its own license. No original video asset belongs in Git.",
            "",
        ]
    )
    return "\n".join(lines)


def prepare(output_dir, *, offline=False):
    output_dir.mkdir(parents=True, exist_ok=True)
    for clip_id, spec in CLIPS.items():
        destination = output_dir / f"pexels-{clip_id}-1080p.mp4"
        if destination.exists():
            verify_file(destination, spec["bytes"], spec["sha256"])
            print(f"Verified existing 25 FPS clip: {destination.name}")
        elif offline:
            raise FileNotFoundError(f"Offline setup requires installed clip: {destination}")
        else:
            print(f"Downloading {destination.name} ({spec['bytes']:,} bytes)...", flush=True)
            request = Request(
                download_url(clip_id), headers={"User-Agent": "live-vlm-webui-retail-demo/1"}
            )
            with urlopen(request, timeout=60) as response:
                atomic_copy(response, destination, spec["bytes"], spec["sha256"])
            print(f"Downloaded and verified: {destination.name}")
    notice = attribution_notice().encode("utf-8")
    atomic_copy(
        BytesIO(notice),
        output_dir / "NOTICE-PEXELS.txt",
        len(notice),
        hashlib.sha256(notice).hexdigest(),
    )
    print(f"Ready: {len(CLIPS)} native 25 FPS store clips in {output_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "artifacts/retail-demo")
    parser.add_argument("--offline", action="store_true", help="Verify installed clips; no network")
    args = parser.parse_args(argv)
    try:
        prepare(args.output_dir, offline=args.offline)
    except (OSError, ValueError) as exc:
        print(f"25 FPS retail setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
