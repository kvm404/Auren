
from pathlib import Path

from auren import daemon, settings
from auren.tools import Runtime
from auren.voice import tuning


def test_tuning_defaults_are_decision_0001():
    assert tuning() == {"speed": 1.05, "steps": 5, "volume": 1.0, "pause": 0.0}


def test_tuning_clamps_rounds_and_ignores_junk():
    t = tuning({"speed": "2", "steps": "4.6", "volume": "loud", "pause": -1, "other": 3})
    assert t == {"speed": 1.6, "steps": 5, "volume": 1.0, "pause": 0.0}


class _Quiet(daemon.Daemon):
    """Only what the tts command touches: no voice model, no brain."""

    def __init__(self):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", "safety": "ask", **tuning()}
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
    assert d._tts(["speed", "1.2", "pause", "0.3"]) == "tts speed 1.2 steps 5 volume 1.0 pause 0.3"
    saved = _saved(tmp_path)
    assert saved["speed"] == 1.2 and saved["pause"] == 0.3 and saved["voiceName"] == "F2"
    assert d._tts(["reset"]).startswith("tts speed 1.05 steps 5")
    assert d.refreshed == 2


def test_tts_command_rejects_bad_input(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    for words in ([], ["speed"], ["pitch", "2"]):
        assert d._tts(words).startswith("usage:")
    assert not (tmp_path / "config.toml").exists()


class _FakeBrain:
    EFFORTS = daemon.Brain.EFFORTS
    mode, using_backup = "auto", False

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
    d.set_state("idle")
    state = json.loads((tmp_path / "state.json").read_text())
    status = json.loads(asyncio.run(d.command("status")))
    assert state["state"] == status["state"] == "idle"
    assert state["voiceName"] == "F2"
    assert "language" not in state and "language" not in status


def test_voice_synthesizes_every_sentence_in_english(monkeypatch):
    import io
    from types import SimpleNamespace

    import numpy as np

    from auren import voice

    calls = []

    class TTS:
        sample_rate = 44100

        def __init__(self, **kwargs):
            pass

        def get_voice_style(self, **kwargs):
            return "style"

        def synthesize(self, **kwargs):
            calls.append(kwargs)
            return np.zeros(100, dtype=np.float32), None

    monkeypatch.setattr("supertonic.TTS", TTS)
    monkeypatch.setattr(voice, "model_dir", lambda: "fake-model")
    monkeypatch.setattr(voice.subprocess, "Popen", lambda *a, **k: SimpleNamespace(stdin=io.BytesIO(), wait=lambda: 0))
    monkeypatch.setattr(voice.Voice, "_follow", lambda self: None)
    # Unexpected non-English text must never switch the engine into Hindi mode.
    assert voice.Voice().speak("Firefox is open. नमस्ते! Done?")
    assert [c["text"] for c in calls] == ["Firefox is open.", "नमस्ते!", "Done?"]
    assert all(c["lang"] == "en" for c in calls)


class _FakeVoice:
    name = "F2"

    def __init__(self):
        self.tuned, self.stopped = None, False

    def set_voice(self, name):
        if name == "M9":
            raise ValueError("no voice M9")
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
        for path, value in (("voice.name", "M3"), ("voice.speed", "1.3"), ("voice.enabled", "false"),
                            ("brain.mode", "codex"), ("brain.effort", "high"),
                            ("brain.claude_model", "sonnet"), ("brain.codex_effort", "medium")):
            settings.set_value(path, value)
        assert await d.command("reload") == "Auren picked it up"
        await asyncio.sleep(0)
        return d

    d = asyncio.run(run())
    assert d.voice.name == "M3" and d.voice.tuned["speed"] == 1.3 and d.voice.stopped
    assert d.brain.mode == "codex" and efforts == [("high", "sonnet")]
    assert d.brain.codex == ("gpt-6.1-sol", "medium")
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
