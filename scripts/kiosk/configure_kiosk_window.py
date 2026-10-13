#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Apply kiosk or half-desktop geometry to the dedicated Chromium window on X11."""

import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=3, check=True).stdout


def half_workarea(desktops, frame_extents):
    """Return client geometry that fits its decorations inside half the work area."""
    current = next(line.split() for line in desktops.splitlines() if line.split()[1] == "*")
    area = current.index("WA:")
    x, y = map(int, current[area + 1].split(","))
    width, height = map(int, current[area + 2].split("x"))
    left, right, top, bottom = frame_extents
    return x, y, max(1, width // 2 - left - right), max(1, height - top - bottom)


def configure(mode, profile):
    if not os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"):
        print("Skipping X11 window positioning outside an X11 session.")
        return
    if not all(shutil.which(tool) for tool in ("wmctrl", "xprop")):
        print("Install wmctrl and x11-utils for X11 fullscreen/half-desktop positioning.")
        return
    profile_arg = "--user-data-dir=" + profile
    deadline = time.monotonic() + 40
    stable_since = None
    while time.monotonic() < deadline:
        windows = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, timeout=3, check=False
        )
        for line in windows.stdout.splitlines():
            fields = line.split(None, 4)
            if len(fields) < 5:
                continue
            window, _, pid, _, _ = fields
            try:
                process = Path("/proc") / pid
                if process.stat().st_uid != os.getuid():
                    continue
                args = (process / "cmdline").read_text().rstrip("\0").split("\0")
                # Snap Chromium rewrites argv as one space-separated process title.
                if len(args) == 1:
                    args = shlex.split(args[0])
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                continue
            if profile_arg not in args:
                continue
            state = subprocess.run(
                ["xprop", "-id", window, "_NET_WM_STATE"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            ).stdout
            if mode == "window":
                run("wmctrl", "-ir", window, "-b", "remove,fullscreen")
                run("wmctrl", "-ir", window, "-b", "remove,maximized_vert,maximized_horz")
                time.sleep(0.2)
                extents_text = run("xprop", "-id", window, "_NET_FRAME_EXTENTS")
                extents = [0, 0, 0, 0]
                if "=" in extents_text:
                    extents = list(map(int, extents_text.split("=", 1)[1].split(",")))
                geometry = half_workarea(run("wmctrl", "-d"), extents)
                run("wmctrl", "-ir", window, "-e", "0," + ",".join(map(str, geometry)))
                print(f"App window {window}: left half of desktop work area, {geometry}.")
                return
            elif "_NET_WM_STATE_FULLSCREEN" in state:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= 3:
                    print(f"Kiosk window {window}: X11 fullscreen confirmed.")
                    return
            else:
                stable_since = None
                subprocess.run(
                    ["wmctrl", "-ir", window, "-b", "add,fullscreen"],
                    check=True,
                    timeout=3,
                )
            break
        else:
            stable_since = None
        time.sleep(0.5)
    # Keep the camera demo running even if window management is unavailable.
    print("Warning: browser window positioning could not be confirmed.", file=sys.stderr)


if __name__ == "__main__":
    try:
        configure(sys.argv[1], sys.argv[2])
    except (OSError, subprocess.SubprocessError, ValueError, StopIteration) as error:
        # Window manager failure must not repeatedly restart an otherwise working demo.
        print(f"Warning: browser window positioning failed: {error}", file=sys.stderr)
