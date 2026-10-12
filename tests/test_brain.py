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
