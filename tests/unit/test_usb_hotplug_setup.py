"""Host USB hot-plug setup without touching real devices, udev or root files."""

import errno
import importlib.util
import os
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/prepare_usb_cameras.sh"
_spec = importlib.util.spec_from_file_location(
    "usb_camera_devices", ROOT / "scripts/usb_camera_devices.py"
)
helper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(helper)


@pytest.fixture
def fake_host(tmp_path, monkeypatch):
    sysfs = tmp_path / "sys/class/video4linux"
    devices = tmp_path / "dev"
    mirror = devices / "live-vlm-cameras"
    lock = tmp_path / "event.lock"
    sysfs.mkdir(parents=True)
    devices.mkdir()
    nodes = {}
    directory = {"st_uid": 0, "st_gid": 44}
    original_lstat, original_fstat = os.lstat, os.fstat
    original_chmod, original_replace, original_unlink = os.chmod, os.replace, os.unlink

    def metadata(mode, number=0, uid=0, gid=44):
        return SimpleNamespace(st_mode=mode, st_rdev=number, st_uid=uid, st_gid=gid, st_nlink=1)

    def lstat(path, *args, **kwargs):
        path = Path(path)
        if path in nodes:
            return nodes[path]
        value = original_lstat(path, *args, **kwargs)
        if path == mirror:
            return metadata(value.st_mode, uid=directory["st_uid"], gid=directory["st_gid"])
        return value

    def fstat(fd):
        value = original_fstat(fd)
        if Path(os.readlink(f"/proc/self/fd/{fd}")) == lock:
            return metadata(value.st_mode)
        return value

    def mknod(path, mode, device):
        path = Path(path)
        assert path.parent == mirror
        path.touch()
        nodes[path] = metadata(mode, device, gid=0)

    def chown(path, uid, gid, **_kwargs):
        path = Path(path)
        assert path == mirror or path in nodes
        if path == mirror:
            directory.update(st_uid=uid, st_gid=gid)
        else:
            nodes[path].st_uid, nodes[path].st_gid = uid, gid

    def chmod(path, mode, **kwargs):
        path = Path(path)
        if path in nodes:
            nodes[path].st_mode = stat.S_IFCHR | mode
        else:
            original_chmod(path, mode, **kwargs)

    def replace(source, target):
        original_replace(source, target)
        nodes[Path(target)] = nodes.pop(Path(source))

    def unlink(path, *args, **kwargs):
        original_unlink(path, *args, **kwargs)
        nodes.pop(Path(path), None)

    monkeypatch.setattr(helper.os, "geteuid", lambda: 0)
    monkeypatch.setattr(helper.os, "lstat", lstat)
    monkeypatch.setattr(helper.os, "fstat", fstat)
    monkeypatch.setattr(helper.os, "mknod", mknod)
    monkeypatch.setattr(helper.os, "chown", chown)
    monkeypatch.setattr(helper.os, "chmod", chmod)
    monkeypatch.setattr(helper.os, "replace", replace)
    monkeypatch.setattr(helper.os, "unlink", unlink)
    monkeypatch.setattr(helper.grp, "getgrnam", lambda name: SimpleNamespace(gr_gid=44))

    def camera(name="video0", minor=7, usb=True):
        parent = tmp_path / "hardware" / name
        interface = parent / "interface"
        interface.mkdir(parents=True)
        if usb:
            (parent / "idVendor").write_text("046d\n")
            (parent / "idProduct").write_text("082d\n")
            bus = tmp_path / "bus/usb"
            bus.mkdir(parents=True, exist_ok=True)
            (parent / "subsystem").symlink_to(bus)
        node = sysfs / name
        node.mkdir()
        (node / "device").symlink_to(interface)
        (node / "dev").write_text(f"81:{minor}\n")
        path = devices / name
        path.touch()
        nodes[path] = metadata(stat.S_IFCHR | 0o660, os.makedev(81, minor))
        return node, path

    def reconcile(name=None):
        return helper.reconcile(
            name, sysfs_root=sysfs, device_root=devices, mirror_root=mirror, lock_path=lock
        )

    return SimpleNamespace(
        sysfs=sysfs,
        devices=devices,
        mirror=mirror,
        lock=lock,
        nodes=nodes,
        directory=directory,
        camera=camera,
        reconcile=reconcile,
    )


def test_empty_boot_then_hotplug_without_replacing_directory(fake_host):
    host = fake_host
    assert host.reconcile() == {"updated": [], "removed": []}
    inode = host.mirror.stat().st_ino
    host.camera("video0", minor=7)
    host.camera("video2", minor=13)
    assert host.reconcile()["updated"] == ["video0", "video2"]
    assert host.mirror.stat().st_ino == inode
    assert host.directory == {"st_uid": 0, "st_gid": 44}
    for name, minor in (("video0", 7), ("video2", 13)):
        device = host.nodes[host.mirror / name]
        assert device.st_rdev == os.makedev(81, minor)
        assert device.st_uid == 0 and device.st_gid == 44
        assert stat.S_IMODE(device.st_mode) == 0o660
    assert host.reconcile() == {"updated": [], "removed": []}


def test_disconnect_and_repeated_remove_are_idempotent(fake_host):
    host = fake_host
    sysfs, _ = host.camera()
    host.reconcile("video0")
    (sysfs / "device").unlink()
    assert host.reconcile("video0") == {"updated": [], "removed": ["video0"]}
    assert host.reconcile("video0") == {"updated": [], "removed": []}


def test_delayed_remove_preserves_current_reconnected_camera(fake_host):
    host = fake_host
    sysfs, source = host.camera(minor=7)
    host.reconcile("video0")
    # udev remove and add can queue together: current kernel state wins.
    (sysfs / "dev").write_text("81:19\n")
    host.nodes[source].st_rdev = os.makedev(81, 19)
    assert host.reconcile("video0")["updated"] == ["video0"]
    assert host.nodes[host.mirror / "video0"].st_rdev == os.makedev(81, 19)
    assert host.reconcile("video0") == {"updated": [], "removed": []}


def test_disappearance_during_publication_removes_stale_node(fake_host, monkeypatch):
    host = fake_host
    host.camera()
    numbers = iter((os.makedev(81, 7), None))
    monkeypatch.setattr(helper, "usb_device_number", lambda *_args: next(numbers))
    assert host.reconcile("video0") == {"updated": ["video0"], "removed": ["video0"]}
    assert not (host.mirror / "video0").exists()


@pytest.mark.parametrize(
    "name", ["", "../video0", "/dev/video0", "video-1", "video0/child", "video0;id"]
)
def test_rejects_non_device_names(fake_host, name):
    with pytest.raises(ValueError, match="videoN"):
        fake_host.reconcile(name)
    assert not fake_host.mirror.exists()


@pytest.mark.parametrize("problem", ["not_usb", "wrong_major", "wrong_minor", "regular", "symlink"])
def test_only_current_matching_usb_video_character_nodes_are_mirrored(fake_host, problem):
    host = fake_host
    _, source = host.camera(usb=problem != "not_usb")
    if problem == "wrong_major":
        host.nodes[source].st_rdev = os.makedev(1, 7)
    elif problem == "wrong_minor":
        host.nodes[source].st_rdev = os.makedev(81, 99)
    elif problem == "regular":
        host.nodes[source].st_mode = stat.S_IFREG | 0o660
    elif problem == "symlink":
        host.nodes[source].st_mode = stat.S_IFLNK | 0o777
    assert host.reconcile() == {"updated": [], "removed": []}
    assert not list(host.mirror.iterdir())


@pytest.mark.parametrize("problem", ["symlink", "user_owned", "group_writable"])
def test_rejects_untrusted_mirror_directory(fake_host, problem, tmp_path):
    host = fake_host
    if problem == "symlink":
        other = tmp_path / "outside"
        other.mkdir()
        host.mirror.symlink_to(other, target_is_directory=True)
    else:
        host.mirror.mkdir()
        if problem == "user_owned":
            host.directory["st_uid"] = 1000
        else:
            host.mirror.chmod(0o775)
    with pytest.raises(ValueError, match="Unsafe camera mirror"):
        host.reconcile()


def test_unexpected_mirror_symlink_is_not_followed_or_overwritten(fake_host, tmp_path):
    host = fake_host
    host.camera()
    host.reconcile()
    (host.mirror / "video0").unlink()
    outside = tmp_path / "outside-data"
    outside.write_text("preserve")
    (host.mirror / "video0").symlink_to(outside)
    with pytest.raises(ValueError, match="Unexpected entry"):
        host.reconcile("video0")
    assert outside.read_text() == "preserve"
    assert (host.mirror / "video0").is_symlink()


def test_failed_publication_preserves_existing_node(fake_host, monkeypatch):
    host = fake_host
    sysfs, source = host.camera()
    host.reconcile()
    (sysfs / "dev").write_text("81:19\n")
    host.nodes[source].st_rdev = os.makedev(81, 19)

    def deny(*_args):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(helper.os, "mknod", deny)
    with pytest.raises(PermissionError):
        host.reconcile("video0")
    assert host.nodes[host.mirror / "video0"].st_rdev == os.makedev(81, 7)
    assert [item.name for item in host.mirror.iterdir()] == ["video0"]


def test_nonroot_helper_fails_before_creating_anything(fake_host, monkeypatch):
    monkeypatch.setattr(helper.os, "geteuid", lambda: 1000)
    with pytest.raises(PermissionError, match="root"):
        fake_host.reconcile()
    assert not fake_host.mirror.exists()
    assert not fake_host.lock.exists()


def test_lock_contention_has_a_bounded_deadline(fake_host, monkeypatch):
    def busy(*_args):
        raise BlockingIOError()

    ticks = iter((0, 4))
    monkeypatch.setattr(helper.fcntl, "flock", busy)
    monkeypatch.setattr(helper.time, "monotonic", lambda: next(ticks))
    with pytest.raises(TimeoutError, match="remained busy"):
        fake_host.reconcile()
    assert not fake_host.mirror.exists()


def test_dry_run_invokes_no_privileged_or_mutating_commands(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "calls"
    for name in ("sudo", "install", "udevadm", "systemd-tmpfiles", "docker", "systemctl"):
        path = commands / name
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name}" >> "{log}"\nexit 99\n')
        path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"],
        env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert "/dev/live-vlm-cameras (root:video 755)" in result.stdout
    assert not log.exists()


def test_install_file_is_idempotent_and_rejects_symlink(tmp_path):
    # Exercise installer functions against temporary files, with ownership/stat
    # and installation emulated. Never execute the privileged main entrypoint.
    prefix = SCRIPT.read_text().split('if [[ "$mode" == dry-run ]]; then', 1)[0]
    prefix = prefix.replace("(($# <= 1))", "true || (($# <= 1))")
    commands = tmp_path / "bin"
    commands.mkdir()
    stat_command = commands / "stat"
    stat_command.write_text(
        '#!/bin/bash\nprintf "0 %s\\n" "$(/usr/bin/stat -c %a -- "${@: -1}")"\n'
    )
    stat_command.chmod(0o755)
    install = commands / "install"
    log = tmp_path / "installs"
    install.write_text(
        "#!/bin/bash\n"
        f'printf "install\\n" >> "{log}"\n'
        '/bin/cp -- "${@: -2:1}" "${@: -1}"\n'
        '/bin/chmod 755 -- "${@: -1}"\n'
    )
    install.chmod(0o755)
    source = tmp_path / "source.py"
    target = tmp_path / "installed.py"
    source.write_text("print('hello')\n")
    env = os.environ | {"PATH": f"{commands}:{os.environ['PATH']}"}
    body = prefix + f'\ninstall_file "{source}" "{target}" 755\n'
    for _ in range(2):
        subprocess.run(["bash", "-c", body], env=env, check=True, capture_output=True, timeout=5)
    assert target.read_text() == source.read_text()
    assert log.read_text().splitlines() == ["install"]
    target.unlink()
    target.symlink_to(source)
    result = subprocess.run(
        ["bash", "-c", body], env=env, capture_output=True, text=True, timeout=5, check=False
    )
    assert result.returncode != 0
    assert "Refusing linked destination" in result.stderr
    assert log.read_text().splitlines() == ["install"]


def test_event_lock_rejects_symlink_without_touching_destination(fake_host, tmp_path):
    outside = tmp_path / "outside-lock"
    outside.write_text("preserve")
    fake_host.lock.symlink_to(outside)
    with pytest.raises(OSError):
        fake_host.reconcile()
    assert outside.read_text() == "preserve"
    assert not fake_host.mirror.exists()


def test_event_lock_rejects_untrusted_owner(fake_host, monkeypatch):
    monkeypatch.setattr(
        helper.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1),
    )
    with pytest.raises(ValueError, match="Unsafe USB camera event lock"):
        fake_host.reconcile()
    assert not fake_host.mirror.exists()


def test_check_mode_never_invokes_privileged_or_mutating_commands(tmp_path):
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "calls"
    for name in ("sudo", "install", "udevadm", "systemd-tmpfiles", "docker", "systemctl"):
        path = commands / name
        path.write_text(f'#!/bin/sh\nprintf "%s\\n" "{name}" >> "{log}"\nexit 99\n')
        path.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT), "--check"],
        env=os.environ | {"PATH": f"{commands}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    assert result.returncode in (0, 1)
    assert not log.exists()
