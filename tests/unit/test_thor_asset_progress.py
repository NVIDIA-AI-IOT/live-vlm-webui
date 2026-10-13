"""Long setup steps report periodic ticks without leaking commands or leaving children."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/thor_demo.py"
sys.path.insert(0, str(SCRIPT.parent))
try:
    spec = importlib.util.spec_from_file_location("thor_asset_progress", SCRIPT)
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
finally:
    sys.path.pop(0)


class FakeChild:
    pid = 123456

    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.waits = []
        self.returncode = None

    def wait(self, timeout=None):
        self.waits.append(timeout)
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        self.returncode = outcome
        return outcome

    def poll(self):
        return self.returncode


def fake_process(monkeypatch, outcomes):
    child = FakeChild(outcomes)
    spawns = []
    signals = []

    def spawn(command, **kwargs):
        spawns.append((command, kwargs))
        return child

    monkeypatch.setattr(demo.subprocess, "Popen", spawn)
    monkeypatch.setattr(demo.os, "killpg", lambda pid, value: signals.append((pid, value)))
    return child, spawns, signals


def timeout(interval=30):
    return subprocess.TimeoutExpired("fixture setup", interval)


def test_progress_ticks_after_each_interval_until_exit_without_forwarding_secrets(
    monkeypatch, capsys
):
    child, spawns, signals = fake_process(monkeypatch, [timeout(12), timeout(12), 7])
    callbacks = []
    command = ["fixture-setup", "private-command-value"]
    env = {"HF_TOKEN": "private-environment-value"}

    def tick(*args, **kwargs):
        callbacks.append((args, kwargs))

    result = demo.run_preparation(command, on_progress=tick, progress_interval=12, env=env)
    assert child.waits == [12, 12, 12]
    assert callbacks == [((), {}), ((), {})]
    assert result.args == command and result.returncode == 7
    assert spawns == [(command, demo.child_process_options() | {"env": env})]
    assert signals == []
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_progress_default_interval_is_thirty_seconds(monkeypatch):
    child, _, signals = fake_process(monkeypatch, [timeout(), 0])
    ticks = []
    result = demo.run_preparation(["fixture"], on_progress=lambda: ticks.append(True))
    assert child.waits == [30, 30]
    assert ticks == [True]
    assert result.returncode == 0
    assert signals == []


def test_quick_command_does_not_emit_a_progress_tick(monkeypatch):
    child, _, signals = fake_process(monkeypatch, [0])

    def unexpected_tick():
        pytest.fail("A command that already exited emitted a progress tick")

    result = demo.run_preparation(["fixture"], on_progress=unexpected_tick)
    assert child.waits == [30]
    assert result.returncode == 0
    assert signals == []


def test_no_callback_preserves_original_blocking_wait(monkeypatch):
    child, spawns, signals = fake_process(monkeypatch, [3])
    result = demo.run_preparation(["fixture"], stdout=subprocess.DEVNULL)
    assert child.waits == [None]
    assert result.returncode == 3
    assert spawns == [(["fixture"], demo.child_process_options() | {"stdout": subprocess.DEVNULL})]
    assert signals == []


@pytest.mark.parametrize("needs_kill", [False, True])
@pytest.mark.parametrize("failure", [RuntimeError("progress failed"), KeyboardInterrupt()])
def test_callback_failure_stops_owned_process_group_and_propagates_original_exception(
    monkeypatch, needs_kill, failure
):
    outcomes = [timeout()]
    if needs_kill:
        outcomes.append(timeout(10))
    outcomes.append(0)
    child, _, signals = fake_process(monkeypatch, outcomes)

    def fail_tick():
        raise failure

    with pytest.raises(type(failure)) as caught:
        demo.run_preparation(["fixture"], on_progress=fail_tick)
    assert caught.value is failure
    assert signals[0] == (child.pid, demo.signal.SIGTERM)
    if needs_kill:
        assert signals == [(child.pid, demo.signal.SIGTERM), (child.pid, demo.signal.SIGKILL)]
        assert child.waits == [30, 10, 5]
    else:
        assert len(signals) == 1
        assert child.waits == [30, 10]


def test_interrupt_while_waiting_cleans_up_without_an_extra_progress_callback(monkeypatch):
    child, _, signals = fake_process(monkeypatch, [KeyboardInterrupt(), 0])
    ticks = []
    with pytest.raises(KeyboardInterrupt):
        demo.run_preparation(["fixture"], on_progress=lambda: ticks.append(True))
    assert ticks == []
    assert child.waits == [30, 10]
    assert signals == [(child.pid, demo.signal.SIGTERM)]


def test_callback_failure_does_not_signal_a_process_that_already_exited(monkeypatch):
    child, _, signals = fake_process(monkeypatch, [timeout()])

    def fail_tick():
        child.returncode = 0
        raise ValueError("callback error")

    with pytest.raises(ValueError, match="callback error"):
        demo.run_preparation(["fixture"], on_progress=fail_tick)
    assert signals == []
    assert child.waits == [30]


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan"), True, "30"])
def test_invalid_progress_interval_fails_before_starting_any_process(monkeypatch, interval):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid progress settings started a process")

    monkeypatch.setattr(demo.subprocess, "Popen", forbidden)
    with pytest.raises(ValueError, match="positive finite"):
        demo.run_preparation(["fixture"], on_progress=lambda: None, progress_interval=interval)


def test_invalid_callback_fails_before_starting_any_process(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid progress settings started a process")

    monkeypatch.setattr(demo.subprocess, "Popen", forbidden)
    with pytest.raises(TypeError, match="callable"):
        demo.run_preparation(["fixture"], on_progress="not callable")
