# SPDX-License-Identifier: Apache-2.0
"""Allowlisted, locally installed retail clips; never accept client file paths."""

import os
import re
from pathlib import Path

SOURCE_URL = "https://huggingface.co/datasets/Intel/Video_Summarization_For_Retail"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
ATTRIBUTION = "Intel — Video Summarization For Retail; curated by Parker Lischwe"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
PEXELS_SOURCES = {
    "store-25-1": "https://www.pexels.com/video/woman-looking-at-a-product-inside-a-grocery-store-8420772/",
    "store-25-2": "https://www.pexels.com/video/an-elderly-people-on-a-grocery-store-8420775/",
    "store-25-3": "https://www.pexels.com/video/an-elderly-man-looking-at-a-red-bell-pepper-8420778/",
    "store-25-4": "https://www.pexels.com/video/couple-paying-for-their-groceries-8421358/",
}
CLIPS = {
    "store-25-1": ("pexels-8420772-1080p.mp4", "Browsing the shelves"),
    "store-25-2": ("pexels-8420775-1080p.mp4", "Shopping by the produce"),
    "store-25-3": ("pexels-8420778-1080p.mp4", "Choosing a bell pepper"),
    "store-25-4": ("pexels-8421358-1080p.mp4", "Paying at the checkout"),
    "retail-1": ("clip1.mp4", "Aisle arrival"),
    "retail-2": ("clip3.mp4", "Browsing products"),
    "retail-3": ("clip8.mp4", "Shopping with a basket"),
    "retail-4": ("clip12.mp4", "Passing in the aisle"),
}


def demo_playback_bounds():
    """Read a strict global resize limit; callers snapshot it at app creation."""
    value = os.environ.get("LIVE_VLM_DEMO_MAX_SIZE", "960x540")
    match = re.fullmatch(r"([1-9][0-9]{2,3})x([1-9][0-9]{2,3})", value)
    if match:
        width, height = map(int, match.groups())
        if 320 <= width <= 1920 and 180 <= height <= 1080 and not (width % 2 or height % 2):
            return width, height
    raise ValueError(
        "LIVE_VLM_DEMO_MAX_SIZE must be WIDTHxHEIGHT with even width 320–1920 "
        "and even height 180–1080 (for example 960x540 or 640x360)"
    )


def resolve_demo(demo_id):
    if not isinstance(demo_id, str) or demo_id not in CLIPS:
        raise ValueError("Select a known store demo video")
    directory = Path(os.environ.get("LIVE_VLM_DEMO_DIR", "artifacts/retail-demo")).resolve()
    path = (directory / CLIPS[demo_id][0]).resolve()
    if path.parent != directory or not path.is_file():
        installer = (
            "download_retail_25fps.py" if demo_id in PEXELS_SOURCES else "download_retail_demo.py"
        )
        raise FileNotFoundError(
            f"Store demo videos are not installed. Run python3 scripts/{installer}"
        )
    return path


def demo_catalog(*, playback_max_width=960, playback_max_height=540):
    videos = []
    for demo_id, (_, title) in CLIPS.items():
        try:
            resolve_demo(demo_id)
            available = True
        except FileNotFoundError:
            available = False
        pexels = demo_id in PEXELS_SOURCES
        videos.append(
            {
                "demo_id": demo_id,
                "title": title,
                "available": available,
                "attribution": "Kampus Production via Pexels" if pexels else ATTRIBUTION,
                "source_url": PEXELS_SOURCES[demo_id] if pexels else SOURCE_URL,
                "license": "Pexels License" if pexels else "CC BY-SA 4.0",
                "license_url": PEXELS_LICENSE_URL if pexels else LICENSE_URL,
                "changes": "Selected clips, resized and looped; live AI overlays added.",
                "source_fps": 25 if pexels else 24,
                "preferred_for_realtime": pexels,
            }
        )
    return {
        "videos": videos,
        "max_fps": 25,
        "playback_max_width": playback_max_width,
        "playback_max_height": playback_max_height,
    }
