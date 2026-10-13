"""Keep foreground sudo authentication fresh while the demo owns its work.

This is best effort: sudo policy controls timestamp lifetime and scope. The
refresh stays in the caller's session, never prompts, and does not change or
invalidate credentials. Command-scoped NOPASSWD Docker access may disallow
``sudo -v`` entirely; that is a valid reason to skip the keepalive.
"""

import math
import os
import subprocess
from pathlib import Path
from threading import Event, Lock, Thread


class SudoKeepalive:
    """Refresh an already-authenticated sudo session until the context exits."""

    def __init__(self, *, interval=45.0, timeout=5.0):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Sudo refresh interval must be positive and finite")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Sudo refresh timeout must be positive and finite")
        self.interval = interval
        self.timeout = timeout
        self._stop = Event()
        self._lock = Lock()
        self._thread = None
        self._sudo = None
        self._closed = False

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
        return False

    def start(self, docker_command):
        """Start after foreground authentication; return whether refresh is active."""
        if (
            not docker_command
            or Path(docker_command[0]).name != "sudo"
            or Path(docker_command[-1]).name != "docker"
            or os.geteuid() == 0
        ):
            return False
        with self._lock:
            if self._closed:
                raise RuntimeError("Sudo keepalive has already closed")
            if self._thread is not None:
                return True
            self._sudo = str(docker_command[0])
            # Do not require sudo validation when policy only permits Docker.
            if not self._refresh():
                return False
            thread = Thread(target=self._run, name="live-vlm-sudo-keepalive", daemon=False)
            self._thread = thread
            try:
                thread.start()
            except BaseException:
                # An interruption can arrive after the worker has been created.
                self._stop.set()
                self._closed = True
                try:
                    thread.join()
                except RuntimeError:
                    pass  # Thread creation failed before it could start.
                self._thread = None
                raise
            return True

    def _refresh(self):
        try:
            result = subprocess.run(
                [self._sudo, "-n", "-v"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def _run(self):
        while not self._stop.wait(self.interval):
            self._refresh()

    def close(self):
        """Wake the worker immediately and wait for any bounded refresh to finish."""
        with self._lock:
            self._closed = True
            self._stop.set()
            thread = self._thread
        if thread is not None:
            thread.join()
