#!/usr/bin/env python3
"""Configure the upstream kiosk helpers for an installed Thor multi-stream profile."""

# SPDX-License-Identifier: Apache-2.0
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BEGIN = "# BEGIN Live VLM multi-stream kiosk"
END = "# END Live VLM multi-stream kiosk"
PROC = Path("/proc")
SERVER_IDENTITY_KEYS = (
    "KIOSK_MODE",
    "KIOSK_REPO",
    "KIOSK_THOR_CONFIG",
    "KIOSK_PORT",
    "KIOSK_API_BASE",
    "KIOSK_MODEL",
    "KIOSK_VLM_BACKEND",
)


def pending_server_path(path):
    return path.with_name("kiosk-server.previous.json")


def read_pending_server(path):
    pending = pending_server_path(path)
    if pending.is_symlink():
        raise ValueError(f"Refusing linked pending kiosk identity: {pending}")
    data = json.loads(pending.read_text())
    if (
        not isinstance(data, dict)
        or data.get("installer") != "live-vlm-webui"
        or data.get("version") != 1
    ):
        raise ValueError(f"Unrecognized pending kiosk identity: {pending}")
    previous = data.get("previous")
    if (
        not isinstance(previous, dict)
        or set(previous) != set(SERVER_IDENTITY_KEYS)
        or not all(isinstance(value, str) and value for value in previous.values())
    ):
        raise ValueError(f"Invalid pending kiosk identity: {pending}")
    return previous


def preserve_server_identity(path, previous):
    """Keep the original identity until a deferred managed restart succeeds."""
    if any(
        not isinstance(previous.get(key), str) or not previous[key] for key in SERVER_IDENTITY_KEYS
    ):
        return
    pending = pending_server_path(path)
    if pending.exists() or pending.is_symlink():
        read_pending_server(path)
        return
    data = {
        "installer": "live-vlm-webui",
        "version": 1,
        "previous": {key: previous[key] for key in SERVER_IDENTITY_KEYS},
    }
    descriptor, temporary = tempfile.mkstemp(prefix=".kiosk-server-previous-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as destination:
            json.dump(data, destination)
            destination.write("\n")
        # Publish atomically without replacing an earlier pending handoff.
        try:
            os.link(temporary, pending)
        except FileExistsError:
            read_pending_server(path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_settings(repo, config):
    sys.path.insert(0, str(repo / "scripts"))
    try:
        import thor_demo

        return thor_demo.read_config(config)
    finally:
        sys.path.pop(0)


def local_backend(settings):
    endpoint = urllib.parse.urlsplit(settings["vlm_api_base"])
    return (
        settings["vlm_backend"] == "edgellm"
        and settings["vlm_model"] == "cosmos-nano-int4"
        and endpoint.scheme == "http"
        and endpoint.hostname in ("localhost", "127.0.0.1")
        and endpoint.port == settings["cosmos_port"]
        and endpoint.path.rstrip("/") == "/v1"
    )


def configure(path, repo, config, settings, source, cameras):
    if source not in ("auto", "usb", "store") or cameras not in ("auto", "2", "4"):
        raise ValueError("Choose auto, usb or store with auto, 2 or 4 cameras")
    if path.is_symlink():
        raise ValueError(f"Refusing to replace linked kiosk configuration: {path}")
    content = path.read_text() if path.exists() else ""
    original = content
    previous = {}
    if BEGIN in content or END in content:
        if content.count(BEGIN) != 1 or content.count(END) != 1:
            raise ValueError("Malformed managed multi-stream configuration block")
        before, managed = content.split(BEGIN, 1)
        block, after = managed.split(END, 1)
        for line in block.splitlines():
            tokens = shlex.split(line)
            if len(tokens) == 1 and "=" in tokens[0]:
                key, value = tokens[0].split("=", 1)
                previous[key] = value
        content = before.rstrip() + "\n" + after.lstrip("\n")
    backend_settings = {
        key: settings.get(key)
        for key in (
            "vlm_api_base",
            "vlm_model",
            "vlm_backend",
            "cosmos_dir",
            "cosmos_port",
            "trt_runtime_prefix",
            "cuda_dir",
            "segmentation_model_dir",
        )
    }
    values = {
        "KIOSK_MODE": "multi-stream",
        "KIOSK_REPO": str(repo),
        "KIOSK_THOR_CONFIG": str(config),
        "KIOSK_SOURCE": source,
        "KIOSK_CAMERAS": cameras,
        "KIOSK_PORT": str(settings["ui_port"]),
        "KIOSK_API_BASE": settings["vlm_api_base"],
        "KIOSK_MODEL": settings["vlm_model"],
        "KIOSK_VLM_BACKEND": settings["vlm_backend"],
        "KIOSK_SERVER_SIGNATURE": hashlib.sha256(
            json.dumps(settings, sort_keys=True).encode()
        ).hexdigest(),
        "KIOSK_BACKEND_SIGNATURE": hashlib.sha256(
            json.dumps(backend_settings, sort_keys=True).encode()
        ).hexdigest(),
        "KIOSK_BACKEND_READY_URL": settings["vlm_api_base"].rstrip("/") + "/models",
    }
    content = content.rstrip() + "\n\n" + BEGIN + "\n"
    content += "\n".join(f"{key}={shlex.quote(value)}" for key, value in values.items())
    content += "\n" + END + "\n"
    if content == original:
        return set()
    changes = {"changed"}
    common_keys = ("KIOSK_REPO", "KIOSK_THOR_CONFIG")
    for kind in ("server", "backend"):
        keys = (*common_keys, f"KIOSK_{kind.upper()}_SIGNATURE")
        if any(previous.get(key) != values[key] for key in keys):
            changes.add(kind)
    if "server" in changes:
        preserve_server_identity(path, previous)
    descriptor, temporary = tempfile.mkstemp(prefix=".kiosk.env-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as destination:
            destination.write(content)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return changes


def http_json(url):
    try:
        # These probes follow the same direct local/backend paths as kiosk curl.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=3) as response:
            data = json.load(response)
            return data if isinstance(data, dict) else None
    except (OSError, ValueError, urllib.error.URLError):
        return None


def port_open(url):
    endpoint = urllib.parse.urlsplit(url)
    try:
        with socket.create_connection(
            (endpoint.hostname, endpoint.port or (443 if endpoint.scheme == "https" else 80)),
            timeout=0.5,
        ):
            return True
    except OSError:
        return False


def service_state(settings, kind):
    if kind == "backend":
        base = settings["vlm_api_base"].rstrip("/")
        if not port_open(base):
            return "free"
        response = http_json(base + "/models")
        if response is None:
            return "warming"
        models = response.get("data")
        if isinstance(models, list) and not models:
            return "warming"
        if isinstance(models, list) and any(
            isinstance(model, dict) and model.get("id") == settings["vlm_model"] for model in models
        ):
            return "ready"
        raise ValueError(
            "The occupied VLM endpoint does not offer the configured model; it was left running"
        )
    bases = ui_bases(settings)
    local_state = None
    occupied = False
    for index, base in enumerate(bases):
        if not port_open(base):
            continue
        occupied = True
        response = http_json(base + "/models") or {}
        expected = {
            "configured_model": settings["vlm_model"],
            "backend": settings["vlm_backend"],
            "api_base": settings["vlm_api_base"].rstrip("/"),
        }
        actual = {key: response.get(key) for key in expected}
        if isinstance(actual["api_base"], str):
            actual["api_base"] = actual["api_base"].rstrip("/")
        if actual != expected:
            raise ValueError(
                f"Port {settings['ui_port']} at {base} is occupied by another UI or an HTTPS server. "
                "It was left running. Choose a free ui_port in the Thor configuration before enabling this localhost HTTP kiosk."
            )
        if index == 0:
            segmentation = http_json(base + "/api/segmentation/status") or {}
            local_state = "ready" if segmentation.get("available") is True else "warming"
    if occupied and local_state is None:
        raise ValueError(
            "An existing WebUI uses this port only on a network address. It was left running. "
            "The kiosk requires localhost HTTP; choose a free ui_port before enabling it."
        )
    return local_state or "free"


def local_addresses():
    try:
        output = subprocess.check_output(["hostname", "-I"], text=True, timeout=5)
        return [
            str(address)
            for value in output.split()
            if not (address := ipaddress.ip_address(value)).is_loopback
            and not address.is_link_local
            and not address.is_unspecified
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return []


def ui_bases(settings):
    host = settings.get("ui_host", "0.0.0.0")
    hosts = ["localhost"]
    if host in ("0.0.0.0", "::"):
        hosts += local_addresses()
    elif host not in ("localhost", "127.0.0.1", "::1", "[::1]"):
        hosts.append(host)
    bases = []
    for host in dict.fromkeys(hosts):
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        bases.append(f"http://{host}:{settings['ui_port']}")
    return bases


def managed_server_running(repo, config):
    """Recognize our running WebUI process, excluding monitors of reused servers."""
    try:
        result = subprocess.run(
            [
                "systemctl",
                "--user",
                "show",
                "live-vlm-kiosk-server.service",
                "--property=ActiveState",
                "--property=MainPID",
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        properties = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
        if result.returncode or properties.get("ActiveState") != "active":
            return False
        pid = int(properties.get("MainPID", "0"))
        if pid <= 0:
            return False
        process = PROC / str(pid)
        if process.stat().st_uid != os.getuid():
            return False
        command = (process / "cmdline").read_bytes().rstrip(b"\0").decode().split("\0")
        return command[1:] == [
            str(repo / "scripts/thor_demo.py"),
            "--config",
            str(config),
            "webui",
            "--skip-build",
            "--localhost-http",
        ]
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def previous_server_settings(path, repo, config, settings):
    """Read only the installer's old identity, without evaluating shell configuration."""
    if path is None or repo is None or config is None or path.is_symlink():
        return None
    try:
        content = path.read_text()
        if content.count(BEGIN) != 1 or content.count(END) != 1:
            return None
        managed = content.split(BEGIN, 1)[1]
        if END not in managed:
            return None
        block = managed.split(END, 1)[0]
        previous = {}
        for line in block.splitlines():
            tokens = shlex.split(line)
            if len(tokens) == 1 and "=" in tokens[0]:
                key, value = tokens[0].split("=", 1)
                if key in previous:
                    return None
                previous[key] = value
        return settings_from_identity(previous, repo, config, settings)
    except (OSError, ValueError, KeyError):
        return None


def settings_from_identity(previous, repo, config, settings):
    if any(
        previous.get(key) != value
        for key, value in {
            "KIOSK_MODE": "multi-stream",
            "KIOSK_REPO": str(repo),
            "KIOSK_THOR_CONFIG": str(config),
            "KIOSK_PORT": str(settings["ui_port"]),
        }.items()
    ):
        return None
    try:
        return settings | {
            "vlm_api_base": previous["KIOSK_API_BASE"],
            "vlm_model": previous["KIOSK_MODEL"],
            "vlm_backend": previous["KIOSK_VLM_BACKEND"],
        }
    except KeyError:
        return None


def pending_server_settings(path, repo, config, settings):
    if path is None or repo is None or config is None:
        return None
    try:
        return settings_from_identity(read_pending_server(path), repo, config, settings)
    except (OSError, ValueError):
        return None


def check_start(settings, *, repo=None, config=None, env_path=None):
    try:
        service_state(settings, "server")
    except ValueError as conflict:
        candidates = [
            previous_server_settings(env_path, repo, config, settings),
            pending_server_settings(env_path, repo, config, settings),
        ]
        if not any(candidates) or not managed_server_running(repo, config):
            raise
        # Only a recorded UI owned by our active unit can be replaced. Retain
        # the original identity across any number of --no-start preparations.
        for previous in candidates:
            if previous is None:
                continue
            try:
                service_state(previous, "server")
                break
            except ValueError:
                continue
        else:
            raise conflict
    if local_backend(settings):
        service_state(settings, "backend")
    # A user unit cannot inherit a foreground terminal's password-only sudo cache.
    # Probe the same noninteractive context without changing groups or sudo policy.
    for command in (["docker", "info"], ["sudo", "-n", "docker", "info"]):
        try:
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
                start_new_session=True,
                check=False,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise ValueError(
        "Kiosk user services need Docker access without an interactive password. "
        "Configure Docker access for the desktop user, then rerun the installer; sudo policy and user groups were not changed."
    )


def run_service(repo, config, settings, kind):
    if kind == "backend" and not local_backend(settings):
        raise ValueError(
            "This profile uses an external backend; its Cosmos kiosk service must remain disabled"
        )
    reused = False
    while service_state(settings, kind) != "free":
        if not reused:
            print(
                f"Using the existing matching {kind}; waiting without starting another instance.",
                flush=True,
            )
            reused = True
        time.sleep(5)
    action = ["cosmos"] if kind == "backend" else ["webui", "--skip-build", "--localhost-http"]
    command = [sys.executable, str(repo / "scripts/thor_demo.py"), "--config", str(config), *action]
    os.execv(sys.executable, command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    configure_parser = sub.add_parser("configure")
    configure_parser.add_argument("--env", type=Path, required=True)
    configure_parser.add_argument("--source", choices=("auto", "usb", "store"), default="auto")
    configure_parser.add_argument("--cameras", choices=("auto", "2", "4"), default="auto")
    check_parser = sub.add_parser("check-start")
    check_parser.add_argument("--env", type=Path)
    sub.add_parser("local-backend")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--kind", choices=("server", "backend"), required=True)
    args = parser.parse_args()
    repo = args.repo.expanduser().resolve()
    config = args.config.expanduser().resolve()
    try:
        settings = load_settings(repo, config)
        if args.action == "configure":
            changed = configure(args.env, repo, config, settings, args.source, args.cameras)
            print(" ".join(sorted(changed)) if changed else "unchanged")
        elif args.action == "check-start":
            check_start(settings, repo=repo, config=config, env_path=args.env)
        elif args.action == "local-backend":
            return 0 if local_backend(settings) else 1
        else:
            run_service(repo, config, settings, args.kind)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f"Multi-stream kiosk: {error}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
