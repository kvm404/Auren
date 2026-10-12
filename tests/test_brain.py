"""Which brain runs: Claude's process only when the mode uses it, models from config."""

from auren import brain as b
from auren import settings


class _Proc:
    def __init__(self):
        self.alive = True

    def poll(self):
        return None if self.alive else 0


def _brain(monkeypatch, mode):
    started = []

    def start(self):
        started.append(self.model)
        self.proc = _Proc()

    def stop(self):
        if self.proc:
            self.proc.alive = False
        self.proc = None

    monkeypatch.setattr(b.ClaudeBrain, "start", start)
    monkeypatch.setattr(b.ClaudeBrain, "stop", stop)
    return b.Brain(None, mode=mode, claude_model="sonnet", codex_model="gpt-x", codex_effort="medium"), started


def test_codex_mode_doesnt_start_claude(monkeypatch):
    brain, started = _brain(monkeypatch, "codex")
    brain.warm()
    brain.forget()
    assert started == [] and brain.primary.proc is None
    assert (brain.backup.model, brain.backup.effort) == ("gpt-x", "medium")


def test_switching_mode_starts_and_stops_claude(monkeypatch):
    brain, started = _brain(monkeypatch, "auto")
    brain.warm()
    brain.warm()                                  # already running: left alone
    assert started == ["sonnet"]
    brain.mode = "codex"
    brain.warm()
    assert brain.primary.proc is None
    brain.mode = "claude"
    brain.warm()
    assert started == ["sonnet", "sonnet"]


def test_claude_model_and_effort_restart_it(monkeypatch):
    brain, started = _brain(monkeypatch, "auto")
    brain.warm()
    brain.set_claude(model="opus")
    assert started == ["sonnet", "opus"] and brain.primary.effort == "low"
    brain.set_claude(effort="high")
    assert brain.primary.effort == "high" and brain.primary.model == "opus"
    brain.set_codex("gpt-y", "xhigh")
    assert (brain.backup.model, brain.backup.effort) == ("gpt-y", "xhigh")


def test_model_settings_are_checked(tmp_path, monkeypatch):
    import pytest

    assert settings.CODEX_EFFORTS == b.Brain.CODEX_EFFORTS
    assert settings.set_value("brain.claude_model", "claude-haiku-5-5") == "claude-haiku-5-5"
    with pytest.raises(ValueError):
        settings.set_value("brain.claude_model", "haiku; rm -rf")
    with pytest.raises(ValueError):
        settings.set_value("brain.codex_effort", "max")


def _claude(tmp_path, monkeypatch, *mode):
    import sys
    from pathlib import Path

    from auren.tools import Runtime

    monkeypatch.setattr(b, "BRAIN_DIR", tmp_path)
    monkeypatch.setattr(b.ClaudeBrain, "_command",
                        lambda self: [sys.executable, str(Path(__file__).with_name("fake_claude.py")), *mode])
    c = b.ClaudeBrain(Runtime(tmp_path / "t.sock"))
    c.profile = type("P", (), {"live_context": lambda self: ""})()
    return c


def _stop_after(c, seconds):
    """Stop `seconds` after the message reached Claude (not before: that's a different case)."""
    import threading
    import time

    def stop():
        deadline = time.monotonic() + 5
        while not c._sent and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(seconds)
        c.interrupt()
    threading.Thread(target=stop, daemon=True).start()


def test_a_stop_keeps_the_conversation(tmp_path, monkeypatch):
    import pytest

    c = _claude(tmp_path, monkeypatch)
    try:
        _stop_after(c, 0.5)
        with pytest.raises(b.Interrupted):
            list(c.ask("remember PINEAPPLE, then answer slowly"))
        proc = c.proc
        assert proc is not None and proc.poll() is None          # same process: the context is still there
        said = [e for e in c.ask("what was the word?") if e[0] == "say"]
        assert "PINEAPPLE" in said[0][1] and c.proc is proc
    finally:
        c.stop()


def test_claude_that_doesnt_stop_is_killed(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(b, "INTERRUPT_GRACE", 0.5)
    c = _claude(tmp_path, monkeypatch, "stubborn")
    try:
        _stop_after(c, 0.3)
        with pytest.raises(b.Interrupted):
            list(c.ask("answer slowly"))
        assert c.proc is None                                     # killed: the next request starts afresh
    finally:
        c.stop()


def test_a_stop_before_the_message_is_sent_keeps_it_unsent(tmp_path, monkeypatch):
    import pytest

    c = _claude(tmp_path, monkeypatch)
    try:
        c.start()
        c.profile = type("P", (), {"live_context": lambda self: c.interrupt() or ""})()   # stopped while preparing
        with pytest.raises(b.Interrupted):
            list(c.ask("remember PINEAPPLE"))
        c.profile = type("P", (), {"live_context": lambda self: ""})()
        said = [e for e in c.ask("what was the word?") if e[0] == "say"]
        assert "PINEAPPLE" not in said[0][1]                       # Claude never got it
    finally:
        c.stop()


def test_a_stop_with_nothing_in_progress_does_nothing(tmp_path, monkeypatch):
    c = _claude(tmp_path, monkeypatch)
    try:
        c.start()
        proc = c.proc
        c.interrupt()
        said = [e for e in c.ask("hello") if e[0] == "say"]
        assert said and c.proc is proc
    finally:
        c.stop()


def test_a_stop_while_claude_isnt_reading_kills_it(tmp_path, monkeypatch):
    import threading
    import time

    import pytest

    monkeypatch.setattr(b, "INTERRUPT_GRACE", 0.3)
    c = _claude(tmp_path, monkeypatch, "deaf")
    try:
        c.start()
        c.profile = type("P", (), {"live_context": lambda self: "x" * 1_000_000})()   # more than the pipe holds
        threading.Thread(target=lambda: (time.sleep(0.3), c.interrupt()), daemon=True).start()
        with pytest.raises(b.Interrupted):
            list(c.ask("hello"))
        assert c.proc is None                                     # the next request starts afresh
    finally:
        c.stop()
