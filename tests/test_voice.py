
import contextlib
import io
import sys
from pathlib import Path

import numpy as np
import pytest

from auren import daemon, settings
from auren.tools import Runtime
from auren.voice import tuning


def test_tuning_defaults_are_decision_0001():
    assert tuning() == {"speed": 1.0, "volume": 1.0, "pause": 0.0}


def test_tuning_clamps_rounds_and_ignores_junk():
    t = tuning({"speed": "2", "steps": "4.6", "volume": "loud", "pause": -1, "other": 3})
    assert t == {"speed": 1.25, "volume": 1.0, "pause": 0.0}


def test_speed_maps_onto_phonons_duration_delta():
    from auren.voice import TUNING, duration_delta

    _, low, high = TUNING["speed"]
    assert duration_delta(1.0) == 0 and duration_delta(high) == -0.5 and duration_delta(low) == 0.4
    assert duration_delta(1.1) < 0 < duration_delta(0.9)


class _Quiet(daemon.Daemon):
    """Only what the tts command touches: no voice model, no brain."""

    def __init__(self):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "Everly", "safety": "ask", **tuning()}
        self.tools = Runtime(Path("/nonexistent/tools.sock"))       # never started
        self.voice = None
        self.profile = None
        self.vocab = []
        self.refreshed = 0

    def _refresh_state(self):
        self.refreshed += 1


def _config_in(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(settings, "LEGACY", tmp_path / "settings.json")


def _saved(tmp_path) -> dict:
    return settings.read((tmp_path / "config.toml").read_text())[0]


def test_tts_command_saves_and_resets(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    assert d._tts(["speed", "1.2", "pause", "0.3"]) == "tts speed 1.2 volume 1.0 pause 0.3"
    saved = _saved(tmp_path)
    assert saved["speed"] == 1.2 and saved["pause"] == 0.3 and saved["voiceName"] == "Everly"
    assert d._tts(["reset"]) == "tts speed 1.0 volume 1.0 pause 0.0"
    assert d.refreshed == 2


def test_tts_command_rejects_bad_input(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    for words in ([], ["speed"], ["pitch", "2"], ["steps", "5"]):
        assert d._tts(words) == "usage: tts speed 0.8..1.25 | volume 0.1..1.0 | pause 0.0..1.0 | reset"
    assert not (tmp_path / "config.toml").exists()


class _FakeBrain:
    EFFORTS = daemon.Brain.EFFORTS
    mode, using_backup, backup_until = "auto", False, None

    def __init__(self):
        self.efforts, self.codex, self.warmed = [], None, 0

    def set_claude(self, effort=None, model=None):
        self.efforts.append(effort or model)

    def set_codex(self, model, effort):
        self.codex = (model, effort)

    def warm(self):
        self.warmed += 1


def test_effort_command_saves_and_restarts_the_brain(tmp_path, monkeypatch):
    import asyncio

    _config_in(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, *a, **k: None)
    monkeypatch.setattr(daemon.Daemon, "_history", lambda self, record: None)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": None)

    async def run():
        d = _Quiet()
        d.brain, d.busy = _FakeBrain(), asyncio.Lock()
        assert (await d.command("effort turbo")).startswith("usage:")
        assert await d.command("effort high") == "effort high"
        await asyncio.sleep(0.05)                     # the restart runs in the background
        return d

    d = asyncio.run(run())
    assert d.brain.efforts == ["high"]
    assert _saved(tmp_path)["effort"] == "high"


def test_codex_effort_command_saves_without_a_fresh_start(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace

    _config_in(tmp_path, monkeypatch)

    async def run():
        d = _Quiet()
        d.brain, d.busy = _FakeBrain(), asyncio.Lock()
        d.brain.backup = SimpleNamespace(model="gpt-6-luna", effort="low")
        assert (await d.command("codex-effort max")).startswith("usage: codex-effort minimal|low")
        assert await d.command("codex-effort high") == "codex-effort high"
        return d

    d = asyncio.run(run())
    assert d.brain.codex == ("gpt-6-luna", "high") and d.brain.efforts == []     # Claude isn't restarted
    assert _saved(tmp_path)["brain.codex_effort"] == "high" and d.refreshed == 1


def test_settings_ignore_an_unknown_effort(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    (tmp_path / "config.toml").write_text('[brain]\neffort = "ludicrous"\n')
    assert daemon.load_settings()["effort"] == "low"


def test_language_command_is_removed(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    import asyncio
    for language in ("en", "hi"):
        assert asyncio.run(d.command(f"language {language}")) == "unknown command: language"
    assert not (tmp_path / "config.toml").exists()


def test_daemon_state_and_status_have_no_language_field(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace

    monkeypatch.setattr(daemon, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(daemon, "STATE_FILE", tmp_path / "state.json")
    d = _Quiet()
    d.brain = _FakeBrain()
    d.brain.primary = SimpleNamespace(effort="low")
    d.brain.backup = SimpleNamespace(model="gpt-6.1-sol", effort="medium")
    d.set_state("idle")
    state = json.loads((tmp_path / "state.json").read_text())
    status = json.loads(asyncio.run(d.command("status")))
    assert state["state"] == status["state"] == "idle"
    assert state["voiceName"] == "Everly" and state["codexModel"] == "gpt-6.1-sol" and state["codexEffort"] == "medium"
    assert "language" not in state and "language" not in status


def test_state_goes_back_to_claude_when_codex_handover_ends(tmp_path, monkeypatch):
    """After Claude's limit, Codex answers for Brain.RETRY_AFTER; an idle daemon still rewrites the state then."""
    import asyncio
    import json
    import time
    from types import SimpleNamespace

    monkeypatch.setattr(daemon, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(daemon, "STATE_FILE", tmp_path / "state.json")
    until = time.time() + 0.2

    class Handover(_FakeBrain):
        @property
        def using_backup(self):
            return time.time() < until

        @property
        def backup_until(self):
            return until if self.using_backup else None

    async def run():
        d = daemon.Daemon.__new__(daemon.Daemon)
        d.settings, d.voice, d.speak_enabled = {"voice": True, "voiceName": "Everly", **tuning()}, None, False
        d.tools, d.loop = SimpleNamespace(level="ask"), asyncio.get_running_loop()
        d.brain = Handover()
        d.brain.primary, d.brain.backup = SimpleNamespace(effort="low"), SimpleNamespace(model="m", effort="low")
        d.set_state("idle")
        first = json.loads((tmp_path / "state.json").read_text())["brain"]
        await asyncio.sleep(1)
        return first, json.loads((tmp_path / "state.json").read_text())["brain"]

    assert asyncio.run(run()) == ("codex", "claude")


class _FakePhonon:
    """Stands in for ptts.TTS: two chunks per sentence, and a log of what it was asked."""

    sample_rate = 24000
    known = {"Everly", "Juniper", "Hugh", "Trevor"}

    def __init__(self, **kwargs):
        self.kwargs, self.said, self.conditions = kwargs, [], []

    def set_conditions(self, conditions):
        self.conditions.append(conditions)

    @contextlib.contextmanager
    def stream(self, text, voice):
        if voice not in self.known:
            raise LookupError(voice)
        self.said.append((text, voice))
        yield iter([np.ones(240, dtype=np.float32), np.ones(240, dtype=np.float32)])


@pytest.fixture
def phonon(monkeypatch):
    from types import SimpleNamespace

    from auren import voice

    played = io.BytesIO()
    played.close = lambda: None
    monkeypatch.setitem(sys.modules, "ptts", SimpleNamespace(TTS=_FakePhonon, DEFAULT_MODEL="hf://x/phonon@1"))
    monkeypatch.setattr(voice.subprocess, "Popen", lambda *a, **k: SimpleNamespace(stdin=played, wait=lambda: 0))
    monkeypatch.setattr(voice.Voice, "_follow", lambda self: None)
    return played


def test_voice_streams_every_sentence_in_english_with_its_voice_speed_and_volume(phonon):
    from auren import voice

    v = voice.Voice("Hugh", speed=1.25, volume=0.5, pause=0.1)
    tts = v.tts
    tts.said.clear()                     # the voices it registered while loading
    # Unexpected non-English text must never switch the engine into Hindi mode.
    assert v.speak("Firefox is open. नमस्ते! Done?")
    assert tts.kwargs == {"lang": "en"}
    assert tts.said == [("Firefox is open.", "Hugh"), ("नमस्ते!", "Hugh"), ("Done?", "Hugh")]
    assert tts.conditions == [{"duration_delta": -0.5}]          # set once, not per sentence
    audio = np.frombuffer(phonon.getvalue(), dtype=np.float32)
    assert len(audio) == 3 * 480 + 2 * int(0.1 * 24000)        # the pause goes between sentences only
    assert audio.max() == 0.5 and audio[479] == 0.5 and audio[480] == 0.0    # 0.1 s of silence, then "नमस्ते!"
    v.tune({"speed": 1.0})
    v.speak("Again.")
    assert tts.conditions[-1] == {"duration_delta": 0.0}


def test_voice_knows_only_its_four_voices(phonon):
    from auren import voice

    v = voice.Voice()
    assert v.name == "Everly" and v.names == list(voice.VOICES) and len(v.names) == 4
    v.set_voice("Trevor")
    with pytest.raises(ValueError, match="no voice F2"):
        v.set_voice("F2")
    assert v.name == "Trevor"


class _FakeVoice:
    name = "Everly"

    def __init__(self):
        self.tuned, self.stopped = None, False

    def set_voice(self, name):
        if name == "F2":
            raise ValueError("no voice F2")
        self.name = name

    def tune(self, values):
        self.tuned = tuning(values)

    def stop(self):
        self.stopped = True


def test_reload_applies_what_changed_in_config(tmp_path, monkeypatch):
    import asyncio

    _config_in(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon, "vocabulary", lambda profile: [])
    efforts = []

    async def set_claude(self, effort=None, model=None):
        efforts.append((effort, model))

    monkeypatch.setattr(daemon.Daemon, "_set_claude", set_claude)

    async def run():
        d = _Quiet()
        d.settings = settings.load()
        d.brain, d.voice = _FakeBrain(), _FakeVoice()
        assert await d.command("reload") == "no changes"
        for path, value in (("voice.name", "Trevor"), ("voice.speed", "1.2"), ("voice.enabled", "false"),
                            ("brain.mode", "codex"), ("brain.effort", "high"),
                            ("brain.claude_model", "sonnet"), ("brain.codex_effort", "medium")):
            settings.set_value(path, value)
        assert await d.command("reload") == "Auren picked it up"
        await asyncio.sleep(0)
        return d

    d = asyncio.run(run())
    assert d.voice.name == "Trevor" and d.voice.tuned["speed"] == 1.2 and d.voice.stopped
    assert d.brain.mode == "codex" and efforts == [("high", "sonnet")]
    assert d.brain.codex == ("gpt-6-luna", "medium")
    assert d.refreshed == 1


def test_cancel_while_working_stops_the_brain_and_its_tools(tmp_path, monkeypatch):
    import asyncio

    from auren.tools import Runtime

    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, state, detail="", **k: setattr(self, "state", state))

    class Brain:
        interrupted = 0

        def interrupt(self):
            self.interrupted += 1

    d = _Quiet()
    d.listening, d.level, d.state = None, daemon.Level(), "working"
    d.tools, d.brain = Runtime(tmp_path / "t.sock"), Brain()
    d.tools.new_turn(daemon.SESSION)
    asyncio.run(d.cancel())
    assert d.brain.interrupted == 1 and d.state == "idle"
    assert "cancelled" in d.tools.call(daemon.SESSION, "system_info", {"what": "time"})[0]
    asyncio.run(d.cancel())                              # idle: nothing to stop
    assert d.brain.interrupted == 1


def test_brain_mode_command_warms_or_stops_claude(tmp_path, monkeypatch):
    import asyncio

    _config_in(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, *a, **k: None)

    async def run():
        d = _Quiet()
        d.brain, d.busy = _FakeBrain(), asyncio.Lock()
        assert (await d.command("brain gpt")).startswith("usage:")
        assert await d.command("brain codex") == "brain codex"
        await asyncio.sleep(0.05)
        return d

    d = asyncio.run(run())
    assert d.brain.mode == "codex" and d.brain.warmed == 1 and _saved(tmp_path)["brain"] == "codex"


def test_a_cancel_before_the_brain_starts_still_stops_the_request(tmp_path, monkeypatch):
    import asyncio

    from auren import brain as b
    from auren.tools import Runtime

    answered, records = [], []
    monkeypatch.setattr(b.ClaudeBrain, "start", lambda self: setattr(self, "proc", type("P", (), {"poll": lambda s: None})()))
    monkeypatch.setattr(b.ClaudeBrain, "_ask", lambda self, text: answered.append(text) or iter([("say", "done")]))
    monkeypatch.setattr(daemon.Daemon, "_history", lambda self, record: records.append(record))
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": None)

    def set_state(self, state, detail="", **k):
        self.state = state
        if state == "thinking":                     # the user cancels right as "thinking" shows
            self.tools.cancel(daemon.SESSION)
            self.brain.interrupt()                  # nothing is being asked yet: a no-op

    monkeypatch.setattr(daemon.Daemon, "set_state", set_state)

    async def run():
        d = _Quiet()
        d.busy, d.tools, d.listening = asyncio.Lock(), Runtime(tmp_path / "t.sock"), None
        d.brain = b.Brain(d.tools, daemon.SESSION)
        await d.respond("close everything", source="voice")

    asyncio.run(run())
    assert answered == [] and records[-1]["reply"] == "(cancelled)"


def test_actions_show_on_the_osd_with_the_mascot_up_but_other_news_does_not(tmp_path, monkeypatch, _no_notices):
    import asyncio

    from auren import notice
    from auren.tools import Runtime

    monkeypatch.setattr(notice, "CONFIG_DIR", tmp_path / "config")      # no mascot.json: the bubble is on
    monkeypatch.setattr(notice, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(notice, "_mascot_running", lambda: True)
    monkeypatch.setattr(daemon.Daemon, "_history", lambda self, record: None)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": None)
    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, state, detail="", **k: setattr(self, "state", state))

    class Brain:
        using_backup = False

        def ask(self, text):
            yield ("tool", "click_at", {"target": "Send"})
            yield ("tool", "system_info", {"what": "time"})            # a quick read: no notice
            yield ("tool", "launch_app", {"app": "WhatsApp"})
            yield ("say", "done")

    async def say(self, text):
        pass

    monkeypatch.setattr(daemon.Daemon, "say", say)

    async def run():
        d = _Quiet()
        d.busy, d.tools, d.listening, d.brain = asyncio.Lock(), Runtime(tmp_path / "t.sock"), None, Brain()
        d.loop = asyncio.get_running_loop()
        await d.respond("send it", source="voice")

    asyncio.run(run())
    shown = [cmd[cmd.index("-m") + 1] for cmd in _no_notices]
    assert shown == ["Clicking Send", "Opening WhatsApp"]           # not "Thinking…": the bubble has that


def test_a_panel_change_keeps_edits_made_to_the_file(tmp_path, monkeypatch):
    import asyncio

    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    d.settings = settings.load()
    d.brain = _FakeBrain()
    settings.set_value("brain.mode", "codex")             # edited, not reloaded yet
    settings.set_value("integrations.browser", "false")
    assert asyncio.run(d.command("voice off")) == "voice off"
    saved = settings.load()
    assert saved["brain"] == "codex" and saved["integrations.browser"] is False and saved["voice"] is False


def test_the_talk_key_stops_an_answer_in_progress(tmp_path, monkeypatch, _no_notices):
    import asyncio

    from auren.tools import Runtime

    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, state, detail="", **k: setattr(self, "state", state))
    pressed = []

    async def press(self, fresh=False):
        pressed.append(fresh)
        return "listening"

    monkeypatch.setattr(daemon.Daemon, "press", press)

    class Brain:
        interrupted = 0

        def interrupt(self):
            self.interrupted += 1

    d = _Quiet()
    d.listening, d.level, d.voice = None, daemon.Level(), None
    d.tools, d.brain = Runtime(tmp_path / "t.sock"), Brain()
    for key, state in (("talk", "working"), ("talk-fresh", "thinking")):
        d.state = state
        assert asyncio.run(d.command(key)) == "stopped"
        assert d.state == "idle"
    assert d.brain.interrupted == 2 and pressed == []
    assert any("Stopped" in cmd for cmd in _no_notices)
    assert asyncio.run(d.command("talk")) == "listening" and pressed == [False]     # idle: talk as before


def test_reload_picks_up_new_vocabulary(tmp_path, monkeypatch):
    import asyncio

    _config_in(tmp_path, monkeypatch)
    words = [["herdr"]]
    monkeypatch.setattr(daemon, "vocabulary", lambda profile: words[0])

    async def run():
        d = _Quiet()
        d.settings = settings.load()
        d.brain, d.voice = _FakeBrain(), _FakeVoice()
        assert await d.command("reload") == "Auren picked up the new words"
        assert d.vocab == ["herdr"]
        assert await d.command("reload") == "no changes"
        words[0] = ["Zorblax", "herdr"]
        assert await d.command("reload") == "Auren picked up the new words"
        return d

    assert asyncio.run(run()).vocab == ["Zorblax", "herdr"]
