"""Scoped sudo refresh tests; no real credentials, processes, or sleeping."""

import importlib.util
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/sudo_session.py"
spec = importlib.util.spec_from_file_location("sudo_session_test", SCRIPT)
sudo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sudo)


class FakeEvent:
    def __init__(self):
        self.stopped = False
        self.waits = []
        self.wakeups = []

    def set(self):
        self.stopped = True

    def wait(self, timeout):
        self.waits.append(timeout)
        return self.stopped or (self.wakeups.pop(0) if self.wakeups else True)


class FakeThread:
    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.joins = 0

    def start(self):
        self.started = True

    def join(self):
        self.joins += 1


@pytest.fixture
def environment(monkeypatch):
    calls = []
    responses = []
    threads = []

    def refresh(command, **kwargs):
        calls.append((command, kwargs))
        response = responses.pop(0) if responses else 0
        if isinstance(response, BaseException):
            raise response
        return SimpleNamespace(returncode=response)

    def thread(**kwargs):
        result = FakeThread(**kwargs)
        threads.append(result)
        return result

    monkeypatch.setattr(sudo.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(sudo.subprocess, "run", refresh)
    monkeypatch.setattr(sudo, "Thread", thread)
    monkeypatch.setattr(sudo, "Event", FakeEvent)
    return SimpleNamespace(calls=calls, responses=responses, threads=threads)


def test_constructing_and_entering_context_does_not_access_sudo(environment):
    session = sudo.SudoKeepalive()
    with session as entered:
        assert entered is session
        assert environment.calls == []
        assert environment.threads == []
    assert session._stop.stopped
    assert environment.calls == []


@pytest.mark.parametrize("command", [[], ["docker"], ["/usr/bin/docker"], ["sudo", "-n", "echo"]])
def test_only_sudo_based_docker_needs_refresh(environment, command):
    with sudo.SudoKeepalive() as session:
        assert session.start(command) is False
    assert environment.calls == []
    assert environment.threads == []


def test_root_does_not_refresh_sudo(environment, monkeypatch):
    monkeypatch.setattr(sudo.os, "geteuid", lambda: 0)
    with sudo.SudoKeepalive() as session:
        assert session.start(["sudo", "-n", "docker"]) is False
    assert environment.calls == []
    assert environment.threads == []


@pytest.mark.parametrize("executable", ["sudo", "/usr/bin/sudo"])
def test_refresh_is_noninteractive_bounded_and_in_foreground_session(environment, executable):
    with sudo.SudoKeepalive(timeout=3) as session:
        assert session.start([executable, "-n", "docker"]) is True
        assert environment.calls == [
            (
                [executable, "-n", "-v"],
                {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "timeout": 3,
                    "check": False,
                },
            )
        ]
        worker = environment.threads[0]
        assert worker.started
        assert not worker.daemon
    assert worker.joins == 1
    assert session._stop.stopped


@pytest.mark.parametrize(
    "failure", [1, OSError("sudo unavailable"), subprocess.TimeoutExpired(["sudo"], 5)]
)
def test_initial_validation_failure_does_not_block_docker_policies(environment, failure):
    environment.responses.append(failure)
    with sudo.SudoKeepalive() as session:
        assert session.start(["sudo", "-n", "docker"]) is False
    assert len(environment.calls) == 1
    assert environment.threads == []


def test_repeated_start_does_not_create_another_worker(environment):
    with sudo.SudoKeepalive() as session:
        assert session.start(["sudo", "-n", "docker"])
        assert session.start(["sudo", "-n", "docker"])
    assert len(environment.calls) == 1
    assert len(environment.threads) == 1
    assert environment.threads[0].joins == 1


def test_refresh_repeats_every_45_seconds_and_survives_transient_failure(environment):
    environment.responses.extend([0, subprocess.TimeoutExpired(["sudo"], 5), 0])
    with sudo.SudoKeepalive() as session:
        session.start(["sudo", "-n", "docker"])
        session._stop.wakeups = [False, False, True]
        environment.threads[0].target()
        assert session._stop.waits == [45.0, 45.0, 45.0]
        assert len(environment.calls) == 3
    assert environment.threads[0].joins == 1


def test_exception_exit_stops_and_joins_worker_without_suppressing_error(environment):
    with pytest.raises(ValueError, match="setup failed"), sudo.SudoKeepalive() as session:
        session.start(["sudo", "-n", "docker"])
        raise ValueError("setup failed")
    assert session._stop.stopped
    assert environment.threads[0].joins == 1
    # An awakened worker must not issue another refresh after context exit.
    environment.threads[0].target()
    assert len(environment.calls) == 1


def test_closed_context_cannot_spawn_orphan_worker(environment):
    session = sudo.SudoKeepalive()
    session.close()
    with pytest.raises(RuntimeError, match="already closed"):
        session.start(["sudo", "-n", "docker"])
    assert environment.calls == []
    assert environment.threads == []


@pytest.mark.parametrize("field", ["interval", "timeout"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_refresh_timing_must_be_positive_and_bounded(environment, field, value):
    with pytest.raises(ValueError, match="positive and finite"):
        sudo.SudoKeepalive(**{field: value})
    assert environment.calls == []


def test_interrupt_during_worker_start_cannot_leave_refresh_running(environment, monkeypatch):
    def interrupted_start(worker):
        worker.started = True
        raise KeyboardInterrupt

    monkeypatch.setattr(FakeThread, "start", interrupted_start)
    with pytest.raises(KeyboardInterrupt), sudo.SudoKeepalive() as session:
        session.start(["sudo", "-n", "docker"])
    assert session._stop.stopped
    assert environment.threads[0].joins == 1
    environment.threads[0].target()
    assert len(environment.calls) == 1
