#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Maintain a root-owned USB video-node directory for non-root containers.

Called by udev for one videoN or without arguments to reconcile all existing
nodes. Only device nodes are mirrored: no camera is opened and no process or
service is started. Current sysfs state decides add/remove, so a delayed remove
event cannot remove a reconnected camera that already has the same name.
"""

import argparse
import errno
import fcntl
import grp
import json
import os
import re
import stat
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

SYSFS_ROOT = Path("/sys/class/video4linux")
DEVICE_ROOT = Path("/dev")
MIRROR_ROOT = Path("/dev/live-vlm-cameras")
LOCK_PATH = Path("/run/live-vlm-cameras.lock")
DEVICE_NAME = re.compile(r"video[0-9]+\Z")


def usb_device_number(name, sysfs_root=SYSFS_ROOT, device_root=DEVICE_ROOT):
    """Return the actual major/minor only for a current, matching USB video node."""
    if not isinstance(name, str) or not DEVICE_NAME.fullmatch(name):
        raise ValueError("Expected a videoN device name")
    node = sysfs_root / name
    try:
        location = (node / "device").resolve(strict=True)
        usb = any(
            (parent / "idVendor").is_file()
            and (parent / "idProduct").is_file()
            and (parent / "subsystem").resolve().name == "usb"
            for parent in (location, *location.parents)
        )
        if not usb:
            return None
        identity = (node / "dev").read_text().strip()
        if not re.fullmatch(r"81:[0-9]{1,7}", identity):
            return None
        major, minor = map(int, identity.split(":"))
        current = os.lstat(device_root / name)
        if not stat.S_ISCHR(current.st_mode):
            return None
        if current.st_rdev != os.makedev(major, minor):
            return None
        return current.st_rdev
    except (OSError, ValueError):
        # Device removal can race any of the sysfs reads or stat operations.
        return None


def trusted_directory(path, video_gid):
    """Create the mirror once; never replace its inode after Docker binds it."""
    try:
        os.mkdir(path, 0o755)
    except FileExistsError:
        pass
    current = os.lstat(path)
    if not stat.S_ISDIR(current.st_mode) or current.st_uid != 0 or current.st_mode & 0o022:
        raise ValueError(f"Unsafe camera mirror directory: {path}")
    os.chown(path, 0, video_gid, follow_symlinks=False)
    os.chmod(path, 0o755, follow_symlinks=False)


@contextmanager
def event_lock(path=LOCK_PATH):
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        current = os.fstat(descriptor)
        if (
            not stat.S_ISREG(current.st_mode)
            or current.st_uid != 0
            or current.st_nlink != 1
            or current.st_mode & 0o077
        ):
            raise ValueError("Unsafe USB camera event lock")
        deadline = time.monotonic() + 3
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("USB camera event lock remained busy") from None
                time.sleep(0.02)
        yield
    finally:
        os.close(descriptor)


def _unlink_node(path):
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        return False
    if not stat.S_ISCHR(current.st_mode) or current.st_uid != 0:
        raise ValueError(f"Unexpected entry in camera mirror: {path.name}")
    path.unlink()
    return True


def _publish_node(path, number, video_gid):
    try:
        current = os.lstat(path)
    except FileNotFoundError:
        current = None
    if current is not None:
        if not stat.S_ISCHR(current.st_mode) or current.st_uid != 0:
            raise ValueError(f"Unexpected entry in camera mirror: {path.name}")
        if (
            current.st_rdev == number
            and current.st_gid == video_gid
            and stat.S_IMODE(current.st_mode) == 0o660
        ):
            return False
    temporary = path.with_name(f".{path.name}-{uuid4().hex}")
    try:
        os.mknod(temporary, stat.S_IFCHR | 0o600, number)
        os.chown(temporary, 0, video_gid, follow_symlinks=False)
        os.chmod(temporary, 0o660, follow_symlinks=False)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return True


def reconcile(
    name=None,
    *,
    sysfs_root=SYSFS_ROOT,
    device_root=DEVICE_ROOT,
    mirror_root=MIRROR_ROOT,
    lock_path=LOCK_PATH,
):
    if os.geteuid() != 0:
        raise PermissionError("USB camera device setup must run as root")
    if name is not None and not DEVICE_NAME.fullmatch(name):
        raise ValueError("Expected a videoN device name")
    video_gid = grp.getgrnam("video").gr_gid
    changed, removed = [], []
    with event_lock(lock_path):
        trusted_directory(mirror_root, video_gid)
        names = (
            [name]
            if name is not None
            else sorted(
                {
                    item.name
                    for root in (sysfs_root, mirror_root)
                    for item in root.glob("video*")
                    if DEVICE_NAME.fullmatch(item.name)
                }
            )
        )
        for device in names:
            target = mirror_root / device
            number = usb_device_number(device, sysfs_root, device_root)
            if number is None:
                if _unlink_node(target):
                    removed.append(device)
                continue
            if _publish_node(target, number, video_gid):
                changed.append(device)
            # If removal raced publication, don't leave an obsolete node behind.
            if usb_device_number(device, sysfs_root, device_root) != number and _unlink_node(
                target
            ):
                removed.append(device)
    return {"updated": changed, "removed": removed}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "device", nargs="?", help="videoN from udev; omit for initial reconciliation"
    )
    args = parser.parse_args(argv)
    try:
        print(json.dumps(reconcile(args.device)))
    except (OSError, ValueError, KeyError) as exc:
        if isinstance(exc, OSError) and exc.errno == errno.EPERM:
            message = "Cannot create camera device nodes; run the host installer as root"
        else:
            message = str(exc)
        print(f"USB camera setup: {message}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
