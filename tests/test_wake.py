import asyncio
import hashlib
import io
import json
import subprocess
import tarfile

import pytest

from auren import daemon, ears, settings, wake
from auren.tools import Runtime


@pytest.mark.parametrize("text, request_", [
    ("Auren, open Firefox.", "Open Firefox."),
    ("Hey Auren what time is it", "What time is it"),
    ("Um, Oren. Close it.", "Close it."),
    ("okay auren", ""),
])
def test_the_name_in_front_is_addressing_auren(text, request_):
    assert wake.addressed(text) and wake.strip_wake(text) == request_


@pytest.mark.parametrize("text", ["Lauren went home", "so now in Auren we", "Auren's voice is nice", "Aren't you", ""])
def test_the_name_elsewhere_is_not(text):
    assert not wake.addressed(text)
    assert wake.strip_wake(text) == text.strip()


def test_mentions_cover_sound_alikes_for_the_echo_guard():
    assert wake.mentions("I'm Auren, your assistant.") and wake.mentions("Lauren called.")
    assert not wake.mentions("Opening Firefox.") and not wake.mentions("")


def test_settings_choices_match_the_spotter():
    assert set(settings.SENSITIVITIES) == set(wake.SENSITIVITY)
    assert settings.defaults()["handsfree"] is False


def test_the_chime_is_short_and_quiet():
    s = wake.chime_samples()
    assert 0.1 < len(s) / 44100 < 0.3 and abs(s).max() <= 0.25


def _archive(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:bz2") as t:
        for name, data in files.items():
            info = tarfile.TarInfo("sherpa-onnx-kws/" + name)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _serve(monkeypatch, data: bytes):
    monkeypatch.setattr(wake.urllib.request, "urlopen", lambda url, timeout: io.BytesIO(data))


def test_model_download_keeps_only_the_files_it_needs(tmp_path, monkeypatch):
    data = _archive({**{src: src.encode() for src in wake.MODEL_FILES}, "bpe.model": b"x", "encoder-big.onnx": b"y"})
    monkeypatch.setattr(wake, "MODELS_DIR", tmp_path)
    monkeypatch.setattr(wake, "MODEL_SHA256", hashlib.sha256(data).hexdigest())
    _serve(monkeypatch, data)
    folder = wake.download_model(log=lambda m: None)
    assert sorted(p.name for p in folder.iterdir()) == sorted(wake.MODEL_FILES.values())
    assert wake.model_dir() == folder
    assert (folder / "encoder.onnx").read_bytes().startswith(b"encoder-")


def test_a_model_that_doesnt_match_its_checksum_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(wake, "MODELS_DIR", tmp_path)
    _serve(monkeypatch, _archive({"tokens.txt": b"x"}))
    with pytest.raises(wake.WakeModelMissing, match="checksum"):
        wake.download_model(log=lambda m: None)
    assert wake.model_dir() is None and list(tmp_path.iterdir()) == []


def _pactl(monkeypatch, outputs, sources=()):
    def run(cmd, **kw):
        data = outputs if cmd[-1] == "source-outputs" else list(sources)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(data), "")
    monkeypatch.setattr(wake.subprocess, "run", run)


def test_mic_busy_counts_other_apps_but_not_auren_or_monitors(monkeypatch):
    ours = {"source": 1, "properties": {"node.name": "auren-mic"}}
    meter = {"source": 9, "properties": {"node.name": "recorder"}}
    other = {"source": 1, "properties": {"node.name": "dictation"}}
    _pactl(monkeypatch, [ours, meter], [{"index": 9, "name": "speakers.monitor"}])
    assert not wake.mic_busy()
    _pactl(monkeypatch, [ours, other])
    assert wake.mic_busy()
    _pactl(monkeypatch, [{**other, "corked": True}])
    assert not wake.mic_busy()


class _Spotter:
    def __init__(self, hits):
        self.hits = list(hits)

    def feed(self, samples):
        return self.hits.pop(0) if self.hits else None


def test_hands_free_passes_audio_on_and_reports_the_name(tmp_path, monkeypatch):
    """A fake pw-record: three chunks, the second has the name in it."""
    async def fake_mic(self):
        self.mic = await asyncio.create_subprocess_exec(
            "python3", "-c", "import sys,time\nfor i in range(3): sys.stdout.buffer.write(bytes([i])*3200); "
            "sys.stdout.flush(); time.sleep(0.05)\ntime.sleep(5)", stdout=asyncio.subprocess.PIPE)

    monkeypatch.setattr(wake.HandsFree, "_open_mic", fake_mic)

    async def run():
        heard, audio = [], []
        hf = wake.HandsFree(heard.append, on_audio=audio.append, pause_when_busy=False,
                            spotter=_Spotter([None, "AUREN", None]))
        await hf.start()
        for _ in range(40):
            if heard and len(b"".join(audio)) >= 9600:
                break
            await asyncio.sleep(0.05)
        hf.stop()
        return heard, audio, hf.preroll()

    heard, audio, preroll = asyncio.run(run())
    assert heard == ["AUREN"] and len(b"".join(audio)) == 9600 and b"".join(preroll) == b"".join(audio)


# --- the daemon ---------------------------------------------------------------------------------------


class _Session:
    """A Listening that's already been answered."""
    external = True

    def __init__(self, finals=(), hands="wake", check=False):
        self.finals, self.hands, self.check = list(finals), hands, check
        self.turn, self.speaking, self.cancelled = asyncio.Event(), False, False
        self.error, self.held_seconds = None, 0.1

    async def cancel(self):
        self.cancelled = True

    async def finish(self):
        return " ".join(self.finals)


class _HF:
    paused = False
    status = "on"

    def preroll(self):
        return [b"before", b"auren"]


def _daemon(monkeypatch, state="idle"):
    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, s, detail="", **k: setattr(self, "state", s)
                        or setattr(self, "detail", detail))
    d = daemon.Daemon.__new__(daemon.Daemon)
    d.settings = {**settings.defaults(), "handsfree": True}
    d.state, d.detail, d.listening, d.voice = state, "", None, None
    d.handsfree, d.level, d._spoken = _HF(), daemon.Level(), __import__("collections").deque(maxlen=2)
    d._waking = False
    return d


def test_the_name_from_idle_listens_with_the_audio_before_it(monkeypatch):
    d = _daemon(monkeypatch)
    pressed = []

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False):
        pressed.append((hands_free, list(preroll), check))

    monkeypatch.setattr(daemon.Daemon, "press", press)

    async def run():
        d._wake("AUREN")
        d._wake("AUREN")                        # a second hit while the first starts: ignored
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert pressed == [("wake", [b"before", b"auren"], True)]


def test_the_name_while_listening_or_in_auren_s_own_voice_is_ignored(monkeypatch):
    d = _daemon(monkeypatch, "speaking")
    monkeypatch.setattr(daemon.Daemon, "press", lambda *a, **k: pytest.fail("pressed"))
    d._spoken.append("Hi, I'm Auren.")
    d._wake("AUREN")
    d.state, d.listening = "listening", _Session()
    d._wake("AUREN")
    assert not d._waking


def test_the_name_while_working_stops_it_and_listens(tmp_path, monkeypatch):
    d = _daemon(monkeypatch, "working")
    d.tools = Runtime(tmp_path / "t.sock")
    calls = []

    class Brain:
        def interrupt(self):
            calls.append("interrupt")

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False):
        calls.append((self.state, hands_free, list(preroll), check))

    d.brain = Brain()
    monkeypatch.setattr(daemon.Daemon, "press", press)

    async def run():
        d._wake("OREN")
        await asyncio.sleep(0.05)

    asyncio.run(run())
    assert calls == ["interrupt", ("idle", "wake", [], False)]       # no audio before it, no check


def _ended(d, session, monkeypatch, wait=0.3):
    released = []

    async def release(self):
        released.append(" ".join(session.finals))
        self.listening = None

    monkeypatch.setattr(daemon.Daemon, "release", release)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": setattr(self, "detail", detail))
    d.listening = session

    async def run():
        task = asyncio.create_task(d._end_on_pause(session, wait))
        return task

    return released, run


def test_a_pause_after_the_request_sends_it(monkeypatch):
    d = _daemon(monkeypatch)
    s = _Session(check=True)
    released, start = _ended(d, s, monkeypatch)

    async def run():
        task = await start()
        s.finals.append("Auren.")              # the name alone: keep listening
        s.turn.set()
        await asyncio.sleep(0.05)
        assert not released and d.listening is s and not s.check
        s.finals.append("Open Firefox.")
        s.turn.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert released == ["Auren. Open Firefox."]


def test_a_transcript_without_the_name_in_front_is_dropped(monkeypatch):
    d = _daemon(monkeypatch)
    s = _Session(check=True)
    released, start = _ended(d, s, monkeypatch)

    async def run():
        task = await start()
        s.finals.append("I told Lauren about it.")
        s.turn.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert released == [] and s.cancelled and d.listening is None and d.detail == "not for me"


def test_silence_after_the_name_goes_back_to_waiting(monkeypatch):
    d = _daemon(monkeypatch)
    s = _Session()
    released, start = _ended(d, s, monkeypatch, wait=0.1)

    async def run():
        await asyncio.wait_for(await start(), 1)

    asyncio.run(run())
    assert released == [] and s.cancelled and d.detail == "heard nothing"


def test_release_takes_the_name_off_and_checks_it(monkeypatch):
    d = _daemon(monkeypatch)
    asked = []

    async def respond(self, text, source, heard_s=None):
        asked.append(text)

    monkeypatch.setattr(daemon.Daemon, "respond", respond)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": setattr(self, "detail", detail))

    async def run():
        d.listening = _Session(["Hey Auren, what's the time?"], check=True)
        assert await d.release() == "heard: What's the time?"
        d.listening = _Session(["Warren Buffett bought more."], check=True)
        assert await d.release() == "not for me"
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert asked == ["What's the time?"]


@pytest.mark.parametrize("text", ["Stop.", "never mind", "Thank you!", "cancel that please"])
def test_stop_words_go_no_further(text, monkeypatch):
    d = _daemon(monkeypatch)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": setattr(self, "detail", detail))
    d.busy = None                                   # the brain's lock: never reached
    asyncio.run(d.respond(text, source="voice"))
    assert d.detail == "cancelled"


def test_hands_free_can_be_turned_off_and_on_by_voice(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")
    d = _daemon(monkeypatch)
    said = []
    monkeypatch.setattr(daemon.Daemon, "_start_handsfree", lambda self: None)
    monkeypatch.setattr(daemon.Daemon, "_stop_handsfree", lambda self: None)
    monkeypatch.setattr(daemon.Daemon, "_refresh_state", lambda self: None)

    async def say(self, text):
        said.append(text)

    monkeypatch.setattr(daemon.Daemon, "say", say)
    asyncio.run(d.respond("Turn off hands-free mode.", source="voice"))
    assert d.settings["handsfree"] is False and settings.load()["handsfree"] is False
    asyncio.run(d.respond("turn on hands free", source="voice"))
    assert d.settings["handsfree"] is True and "on" in said[-1]


def test_a_question_is_answered_without_the_name(tmp_path, monkeypatch):
    d = _daemon(monkeypatch)
    d.tools, d.busy, d.loop = Runtime(tmp_path / "t.sock"), None, None
    pressed = []

    class Brain:
        using_backup = False

        def ask(self, text):
            yield ("say", "Which one, the first or the second?")

    async def say(self, text):
        self.state = "idle"
        return True

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False):
        pressed.append(hands_free)

    monkeypatch.setattr(daemon.Daemon, "say", say)
    monkeypatch.setattr(daemon.Daemon, "press", press)
    monkeypatch.setattr(daemon.Daemon, "_history", lambda self, r: None)
    d.brain = Brain()

    async def run():
        d.busy, d.loop = asyncio.Lock(), asyncio.get_running_loop()
        await d.respond("close the tab", source="voice")
        d.settings["handsfree.follow_up"] = "off"
        await d.respond("close the tab", source="voice")

    asyncio.run(run())
    assert pressed == ["follow-up"]


def test_handsfree_command_saves_and_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")
    d = _daemon(monkeypatch)
    d.settings["handsfree"] = False
    started = []
    monkeypatch.setattr(daemon.Daemon, "_start_handsfree", lambda self: started.append("start"))
    monkeypatch.setattr(daemon.Daemon, "_stop_handsfree", lambda self: started.append("stop"))
    monkeypatch.setattr(daemon.Daemon, "_refresh_state", lambda self: None)
    assert d._handsfree_command(["toggle"]) == "handsfree on" and started == ["start"]
    assert d._handsfree_command(["sensitivity", "high"]) == "handsfree sensitivity high"
    assert started == ["start", "stop", "start"]                  # a new sensitivity restarts the spotter
    assert d._handsfree_command(["follow-up", "loud"]).startswith("usage:")
    assert d._handsfree_command([]).startswith("usage:")
    saved = settings.load()
    assert saved["handsfree"] is True and saved["handsfree.sensitivity"] == "high"


# --- the ears -----------------------------------------------------------------------------------------


def test_an_external_listen_takes_pushed_audio_and_returns_at_once_after_a_pause():
    async def run():
        s = ears.Listening([], external=True, turn_silence=(500, 1200))
        s.push(b"\0" * 3200)
        assert s.queue.qsize() == 1
        s.finals = ["Open Firefox."]                 # the service ended the turn on a pause
        text = await asyncio.wait_for(s.finish(), 0.5)
        s.push(b"\0" * 3200)                         # after the release: not sent
        return text, s.queue.qsize()

    text, queued = asyncio.run(run())
    assert text == "Open Firefox." and queued == 2   # the chunk and the end-of-audio marker
