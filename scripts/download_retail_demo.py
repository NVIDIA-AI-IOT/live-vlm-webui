#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Prepare four pinned, CC BY-SA 4.0 retail demo clips using only the standard library."""

import argparse
import hashlib
import os
import stat
import sys
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile

REPO_ROOT = Path(__file__).resolve().parents[1]
DATASET_URL = "https://huggingface.co/datasets/Intel/Video_Summarization_For_Retail"
REVISION = "1fe8033097b49d07415b866bcba6ba7d7a4442c4"
ARCHIVE_URL = f"{DATASET_URL}/resolve/{REVISION}/VideoSumForRetailData.zip"
ARCHIVE_SHA256 = "543d53bc8af96d4d5b5dd180799e93cb615f0e417f777157e777ae7328f76073"
ARCHIVE_BYTES = 179739885
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
CLIPS = {
    "clip1.mp4": (10372510, "c18dee574af7ed3c797f180fbbdc189e4a0951fc120638cfbd4f4c35ccb8e434"),
    "clip3.mp4": (10270833, "cf5a2a741eaaa3671a50ffc5ed4da3cc3f79a8815660de6eae2a61fdaedf4d55"),
    "clip8.mp4": (10974997, "8d991e947cde4f8e0471cfafaf56e76c1afeed6d19a33ee9f4b2e12b0854e0b5"),
    "clip12.mp4": (10917357, "ede1e31ee312db1d17bfa29a180ceec544ef97be4ccc73248e9a077b3d125db2"),
}
NOTICE = f"""RECORDED RETAIL DEMO FOOTAGE

Dataset: Video Summarization For Retail
Publisher: Intel (Hugging Face dataset repository)
Curated by: Parker Lischwe
Source: {DATASET_URL}
Pinned revision: {REVISION}
Archive: {ARCHIVE_URL}
Archive SHA256: {ARCHIVE_SHA256}
Selected original files: clip1.mp4, clip3.mp4, clip8.mp4, clip12.mp4
License: Creative Commons Attribution-ShareAlike 4.0 International
License URL: {LICENSE_URL}

The selected files are extracted without re-encoding or editing. They are four
recorded excerpts from the same camera, not four synchronized camera views.
The demo loops them and may resize/rate-limit frames and add live segmentation
overlays. The footage and these rendered video adaptations are provided under
CC BY-SA 4.0. Retain this attribution and license, identify further changes,
and apply CC BY-SA 4.0 to shared adaptations. No endorsement by Intel, the
curator, or people/brands depicted is implied.

This notice applies to the footage and its adaptations, not the independently
maintained application source code. The dataset card acknowledges identifiable
people; it does not document participant releases or original filming provenance.
The dataset and CC license provide no warranties. See the source card and full
license for the supplied limitations and terms.
"""


def verify_file(path, expected_bytes, expected_sha256):
    if not path.is_file() or path.stat().st_size != expected_bytes:
        raise ValueError(f"Unexpected file size: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected_sha256:
        raise ValueError(f"SHA256 mismatch: {path}; keep it aside before retrying setup")


def atomic_copy(source, destination, expected_bytes, expected_sha256):
    """Publish a file only after its exact length and digest have been checked."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=destination.parent, prefix=f".{destination.name}.", suffix=".part", delete=False
        ) as output:
            temporary = Path(output.name)
            digest = hashlib.sha256()
            size = 0
            while chunk := source.read(1024 * 1024):
                size += len(chunk)
                if size > expected_bytes:
                    raise ValueError(f"Download or archive member is too large: {destination.name}")
                digest.update(chunk)
                output.write(chunk)
            if size != expected_bytes or digest.hexdigest() != expected_sha256:
                raise ValueError(f"Content verification failed: {destination.name}")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def prepare(archive_path, output_dir, *, offline=False):
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        verify_file(archive_path, ARCHIVE_BYTES, ARCHIVE_SHA256)
        print(f"Verified cached archive: {archive_path}")
    elif offline:
        raise FileNotFoundError(f"Offline setup requires the cached archive: {archive_path}")
    else:
        print(f"Downloading pinned Intel retail dataset ({ARCHIVE_BYTES:,} bytes)...", flush=True)
        request = Request(ARCHIVE_URL, headers={"User-Agent": "live-vlm-webui-retail-demo/1"})
        with urlopen(request, timeout=60) as response:
            atomic_copy(response, archive_path, ARCHIVE_BYTES, ARCHIVE_SHA256)
        print(f"Verified downloaded archive: {archive_path}")

    # Select exact known members, never extractall() or archive-provided paths.
    with ZipFile(archive_path) as archive:
        names = archive.namelist()
        for filename, (expected_bytes, expected_sha256) in CLIPS.items():
            member = f"VideoSumForRetailData/clips/{filename}"
            if names.count(member) != 1:
                raise ValueError(f"Expected exactly one archive member: {member}")
            info = archive.getinfo(member)
            if info.is_dir() or stat.S_ISLNK(info.external_attr >> 16):
                raise ValueError(f"Archive member is not a regular video file: {member}")
            if info.file_size != expected_bytes:
                raise ValueError(f"Unexpected archive member size: {member}")
            destination = output_dir / filename
            if destination.exists():
                verify_file(destination, expected_bytes, expected_sha256)
                print(f"Verified existing clip: {filename}")
            else:
                with archive.open(info) as source:
                    atomic_copy(source, destination, expected_bytes, expected_sha256)
                print(f"Extracted and verified: {filename}")

    notice_path = output_dir / "NOTICE.txt"
    notice_bytes = NOTICE.encode("utf-8")
    if not notice_path.exists() or notice_path.read_bytes() != notice_bytes:
        atomic_copy(
            BytesIO(notice_bytes),
            notice_path,
            len(notice_bytes),
            hashlib.sha256(notice_bytes).hexdigest(),
        )
    print(f"Ready: {len(CLIPS)} recorded retail demo clips in {output_dir}")
    print(f"Footage attribution and license: {notice_path}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--archive",
        type=Path,
        default=REPO_ROOT / "artifacts/retail-dataset/VideoSumForRetailData.zip",
        help="Verified archive cache path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "artifacts/retail-demo",
        help="Directory for the four selected clips and NOTICE.txt",
    )
    parser.add_argument(
        "--offline", action="store_true", help="Require the cached archive; no network"
    )
    args = parser.parse_args(argv)
    try:
        prepare(args.archive, args.output_dir, offline=args.offline)
    except (OSError, ValueError, BadZipFile) as exc:
        print(f"Retail demo setup failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
