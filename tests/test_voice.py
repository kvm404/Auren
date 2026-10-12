
from auren import daemon, settings
from auren.voice import tuning


def test_tuning_defaults_are_decision_0001():
    assert tuning() == {"speed": 1.05, "steps": 5, "volume": 1.0, "pause": 0.0}


def test_tuning_clamps_rounds_and_ignores_junk():
    t = tuning({"speed": "2", "steps": "4.6", "volume": "loud", "pause": -1, "other": 3})
    assert t == {"speed": 1.6, "steps": 5, "volume": 1.0, "pause": 0.0}


class _Quiet(daemon.Daemon):
    """Only what the tts command touches: no voice model, no brain."""

    def __init__(self):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", **tuning()}
        self.voice = None
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


def test_hindi_sentences_split_on_the_danda_and_pick_hindi_mode():
    from auren.voice import language_of, sentences

    text = "ठीक है, मैं अभी Firefox खोल रही हूँ। बस दो second। Done?"
    assert sentences(text) == ["ठीक है, मैं अभी Firefox खोल रही हूँ।", "बस दो second।", "Done?"]
    assert [language_of(s) for s in sentences(text)] == ["hi", "hi", "en"]
    assert language_of("Okay, fresh start.") == "en"


def test_reply_language_note():
    from auren.brain import prompt

    assert prompt.reply_language("en") == ""
    female, male = prompt.reply_language("hi", "female"), prompt.reply_language("hi", "male")
    assert "Devanagari" in female and "रही हूँ" in female and "रहा हूँ" in male
    msg = prompt.user_message("open whatsapp", "time: now", "hi", "male")
    assert msg.index("<reply_language>") < msg.index("Transcript: open whatsapp")
    assert "<reply_language>" not in prompt.user_message("open whatsapp", "time: now")


class _LangBrain(_FakeBrain):
    def __init__(self):
        super().__init__()
        self.set = []

    def set_language(self, language, speaker="female"):
        self.set.append((language, speaker))


def test_language_command_follows_the_voice_gender(tmp_path, monkeypatch):
    _config_in(tmp_path, monkeypatch)
    d = _Quiet()
    d.settings["language"] = "en"
    d.brain = _LangBrain()
    assert d._refresh_state() is None
    import asyncio
    assert asyncio.run(d.command("language fr")).startswith("usage:")
    assert asyncio.run(d.command("language hi")) == "language hi"
    d.settings["voiceName"] = "M3"
    assert asyncio.run(d.command("language hi")) == "language hi"
    assert d.brain.set == [("hi", "female"), ("hi", "male")]
    assert _saved(tmp_path)["language"] == "hi"


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
    efforts = []

    async def set_claude(self, effort=None, model=None):
        efforts.append((effort, model))

    monkeypatch.setattr(daemon.Daemon, "_set_claude", set_claude)

    async def run():
        d = _Quiet()
        d.settings = settings.load()
        d.brain, d.voice = _LangBrain(), _FakeVoice()
        assert await d.command("reload") == "no changes"
        for path, value in (("voice.name", "M3"), ("voice.speed", "1.3"), ("voice.enabled", "false"),
                            ("brain.mode", "codex"), ("brain.effort", "high"), ("language", "hi"),
                            ("brain.claude_model", "sonnet"), ("brain.codex_effort", "medium")):
            settings.set_value(path, value)
        assert await d.command("reload") == "Auren picked it up"
        await asyncio.sleep(0)
        return d

    d = asyncio.run(run())
    assert d.voice.name == "M3" and d.voice.tuned["speed"] == 1.3 and d.voice.stopped
    assert d.brain.mode == "codex" and d.brain.set == [("hi", "male")] and efforts == [("high", "sonnet")]
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
