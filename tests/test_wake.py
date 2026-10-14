import asyncio
import hashlib
import io
import json
import subprocess

import numpy as np
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


def _serve(monkeypatch, files: dict[str, bytes]):
    def urlopen(url, timeout):
        name = url.rsplit("/", 1)[1]
        if name not in files:
            raise OSError(f"404 {name}")
        return io.BytesIO(files[name])
    monkeypatch.setattr(wake.urllib.request, "urlopen", urlopen)


def _model(monkeypatch, files: dict[str, bytes]):
    """Serve these model files, with checksums pinned to them."""
    monkeypatch.setattr(wake, "MODEL_FILES", {src: (name, hashlib.sha256(files[src]).hexdigest())
                                              for src, (name, _) in wake.MODEL_FILES.items()})
    _serve(monkeypatch, files)


def test_model_download_saves_each_file_and_removes_the_old_model(tmp_path, monkeypatch):
    monkeypatch.setattr(wake, "MODELS_DIR", tmp_path)
    (tmp_path / "kws-gigaspeech").mkdir()
    _model(monkeypatch, {src: src.encode() for src in wake.MODEL_FILES})
    folder = wake.download_model(log=lambda m: None)
    assert sorted(p.name for p in folder.iterdir()) == sorted(name for name, _ in wake.MODEL_FILES.values())
    assert wake.model_dir() == folder and sorted(p.name for p in tmp_path.iterdir()) == [wake.MODEL]
    assert (folder / "encoder.onnx").read_bytes().startswith(b"encoder-")


def test_a_model_that_doesnt_match_its_checksum_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(wake, "MODELS_DIR", tmp_path)
    _serve(monkeypatch, {src: b"tampered" for src in wake.MODEL_FILES})
    with pytest.raises(wake.WakeModelMissing, match="checksum"):
        wake.download_model(log=lambda m: None)
    assert wake.model_dir() is None and list(tmp_path.iterdir()) == []


def test_a_failed_download_leaves_nothing_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(wake, "MODELS_DIR", tmp_path)
    _model(monkeypatch, {src: b"x" for src in wake.MODEL_FILES})
    _serve(monkeypatch, {"tokens.txt": b"x"})                  # the rest are missing
    with pytest.raises(wake.WakeModelMissing, match="download"):
        wake.download_model(log=lambda m: None)
    assert list(tmp_path.iterdir()) == []


# --- finding the name in the local model's words -----------------------------------------------------


def _words(text: str, gaps: dict[int, float] | None = None) -> list[wake.Word]:
    """Words 0.3 s apart; gaps: extra seconds of silence before word i."""
    out, t = [], 0.0
    for i, w in enumerate(text.split()):
        t += 0.3 + (gaps or {}).get(i, 0)
        out.append(wake.Word(w, t, t + 0.1))
    return out


def test_words_are_put_together_from_word_pieces():
    words = wake.words_of(["'", "RE", " IN", " OR", "IN"], [0.1, 0.2, 0.5, 1.0, 1.2], start=5.0)
    assert [(w.text, w.at, w.last) for w in words] == [("'RE", 5.1, 5.2), ("IN", 5.5, 5.5), ("ORIN", 6.0, 6.2)]
    assert wake.spelling("'RE  in") == "RE IN"


@pytest.mark.parametrize("text, sensitivity, found", [
    ("ORIN OPEN FIREFOX", "low", (0, 1, True)),
    ("OR IN WHAT TIME IS IT", "medium", (0, 2, True)),
    ("OR IN WHAT TIME IS IT", "low", None),           # two words only from medium on
    ("HEY ORAN FIREFOX", "medium", (1, 2, True)),
    ("O K OR IN PLAY MUSIC", "medium", (2, 4, True)),
    ("'RE IN WHAT TIME IS IT", "medium", (0, 2, True)),
    ("HEYORN OPEN FIREFOX", "high", (0, 1, True)),
    ("HEYORN OPEN FIREFOX", "medium", None),
    ("HELLOORIN WHAT TIME IS IT", "low", (0, 1, True)),  # the model joined the greeting to the name
    ("ARE IN STOP", "high", (0, 2, True)),
    ("ARE IN STOP", "medium", None),
    ("SHOULD WE GO NOW OR IN THE MORNING", "high", None),   # "or in" inside a sentence isn't the name
    ("I THINK ORIN SAID IT", "medium", (2, 3, False)),   # one-word spellings anywhere: they can stop Auren mid-reply
])
def test_find_name(text, sensitivity, found):
    assert wake.find_name(_words(text), sensitivity) == found


def test_two_word_spellings_count_after_a_pause_too():
    assert wake.find_name(_words("THAT IS IT OR IN OPEN IT"), "medium") is None
    assert wake.find_name(_words("THAT IS IT OR IN OPEN IT", gaps={3: 0.6}), "medium") == (3, 5, True)


def test_a_name_at_the_end_waits_until_the_word_is_finished():
    assert wake.find_name(_words("OR IN"), "medium") is None              # might still become "OR INSIDE"
    assert wake.find_name(_words("OR IN"), "medium", done=True) == (0, 2, True)
    assert wake.find_name(_words("OR IN OPEN"), "medium", after=2) is None  # already reported


def test_learned_spellings_count_where_a_phrase_starts():
    assert wake.find_name(_words("MORN OPEN FIREFOX"), "low") is None
    assert wake.find_name(_words("MORN OPEN FIREFOX"), "low", mine=["morn"]) == (0, 1, True)
    assert wake.find_name(_words("GOOD MORN TO YOU"), "low", mine=["morn"]) is None


# --- Learn my voice --------------------------------------------------------------------------------------


def test_learner_keeps_how_the_name_sounds_minus_common_words():
    learner = wake.Learner("medium")
    assert "1 of 4" in learner.state()["prompt"]
    learner.heard("OR IN")
    learner.heard("I WAS SAYING SOMETHING ELSE ENTIRELY")   # too long: not counted
    assert learner.state()["prompt"].startswith("Just the name") and learner.samples == ["OR IN"]
    for text in ("MORN", "OR", "HEY ORIN"):
        learner.heard(text)
    assert learner.step == "other" and learner.samples == ["OR IN", "MORN", "OR", "ORIN"]
    assert learner.caught == 2                                # OR IN and ORIN were heard before
    learner.heard("GOOD MORN TO YOU ALL")                     # "morn" is in a normal sentence: dropped
    assert learner.step == "done" and learner.learned == ["or in", "orin"]
    assert "2 of 4" in learner.state()["prompt"]


def test_learner_keeps_only_what_could_be_the_name():
    # How a weaker model once wrote one user's "Auren": words that aren't the name at all must not stay.
    assert [f for f in ("MODERED", "ART", "MARRIED", "PARDON", "AUTUMN", "MORE IN", "ORDIN", "AU RED")
            if wake.namelike(f)] == ["MORE IN", "ORDIN", "AU RED"]
    learner = wake.Learner("medium")
    for text in ("ART", "MARRIED", "ORDIN", "PARDON"):
        learner.heard(text)
    learner.heard("WHAT'S THE WEATHER TODAY")
    assert learner.learned == ["ordin"]


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
    def __init__(self, events):
        self.events = list(events)

    def feed(self, samples):
        return self.events.pop(0) if self.events else []


def test_hands_free_passes_audio_on_and_reports_the_name(tmp_path, monkeypatch):
    """A fake pw-record: three chunks, the second has the name in it."""
    async def fake_mic(self):
        self.mic = await asyncio.create_subprocess_exec(
            "python3", "-c", "import sys,time\nfor i in range(5): sys.stdout.buffer.write(bytes([i])*1600); "
            "sys.stdout.flush(); time.sleep(0.05)\ntime.sleep(5)", stdout=asyncio.subprocess.PIPE)

    monkeypatch.setattr(wake.HandsFree, "_open_mic", fake_mic)

    async def run():
        heard, said, audio = [], [], []
        # the name, said 0.06 s before the end of chunk 4: the pre-roll starts LEAD before that
        hf = wake.HandsFree(lambda w, at, clear: heard.append((w, at, clear)), on_audio=audio.append,
                            pause_when_busy=False, on_said=lambda t, at: said.append((t, at)),
                            spotter=_Spotter([[], [], [], [("name", "ORIN", 0.06, True)], [("said", "ORIN", 0.12)]]))
        await hf.start()
        for _ in range(40):
            if said and len(b"".join(audio)) >= 5 * wake.CHUNK:
                break
            await asyncio.sleep(0.05)
        hf.stop()
        return heard, said, audio, hf

    heard, said, audio, hf = asyncio.run(run())
    first = 4 - int((0.06 + wake.LEAD) // wake.STEP)
    assert heard == [("ORIN", first, True)] and said == [("ORIN", 3)] and len(b"".join(audio)) == 5 * wake.CHUNK
    assert b"".join(hf.preroll()) == b"".join(audio)
    assert hf.preroll(3) == audio[2:]                          # from the chunk the name starts in


# --- the daemon ---------------------------------------------------------------------------------------


class _Session:
    """A Listening that's already been answered."""
    external = True

    def __init__(self, finals=(), hands="wake", check=False, clear=False):
        self.finals, self.hands, self.check, self.clear = list(finals), hands, check, clear
        self.turn, self.speaking, self.cancelled, self.closed = asyncio.Event(), False, False, False
        self.error, self.held_seconds = None, 0.1

    async def cancel(self):
        self.cancelled = True

    async def finish(self):
        return " ".join(self.finals)


class _HF:
    paused = False
    status = "on"

    position = 0
    words_after_name = 0

    def preroll(self, at=None):
        return [b"before", b"auren"] if at is None else [b"auren"]

    def learn(self, mine):
        self.mine = list(mine)


def _daemon(monkeypatch, state="idle"):
    monkeypatch.setattr(daemon.Daemon, "set_state", lambda self, s, detail="", **k: setattr(self, "state", s)
                        or setattr(self, "detail", detail))
    d = daemon.Daemon.__new__(daemon.Daemon)
    d.settings = {**settings.defaults(), "handsfree": True}
    d.state, d.detail, d.listening, d.voice, d.brain = state, "", None, None, None
    d.handsfree, d.level, d._spoken = _HF(), daemon.Level(), __import__("collections").deque(maxlen=2)
    d._waking = False
    return d


def test_the_name_from_idle_listens_with_the_audio_before_it(monkeypatch):
    d = _daemon(monkeypatch)
    pressed = []

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False, clear=False):
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
    now = daemon.time.monotonic()
    d._spoken.append((now, "Hi, I'm Auren."))
    d._wake("AUREN")
    d._spoken.append((now - 0.2, "How can I help?"))         # just after it: the echo may still come in
    d._wake("AUREN")
    d.state, d.listening = "listening", _Session()
    d._wake("AUREN")
    d.state, d.listening = "idle", None
    d.handsfree.paused = True                                 # a late hit after a pause (or after turning it off)
    d._wake("AUREN")
    d.handsfree = None
    d._wake("AUREN")
    assert not d._waking


def test_the_name_in_a_later_sentence_still_interrupts(monkeypatch):
    d = _daemon(monkeypatch, "speaking")
    pressed = []

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False, clear=False):
        pressed.append((hands_free, list(preroll), check))

    monkeypatch.setattr(daemon.Daemon, "press", press)
    now = daemon.time.monotonic()
    d._spoken.extend([(now - 5, "Hi, I'm Auren."), (now - 3, "This is a long second sentence.")])

    async def run():
        d._wake("AUREN")
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert pressed == [("wake", [], False)]                   # Auren's own voice isn't sent


def test_the_name_while_working_stops_it_and_listens(tmp_path, monkeypatch):
    d = _daemon(monkeypatch, "working")
    d.tools = Runtime(tmp_path / "t.sock")
    calls = []

    class Brain:
        def interrupt(self):
            calls.append("interrupt")

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False, clear=False):
        calls.append((d.tools.cancelled(daemon.SESSION), hands_free, list(preroll), check))

    d.brain = Brain()
    d.tools.new_turn(daemon.SESSION)
    monkeypatch.setattr(daemon.Daemon, "press", press)

    async def run():
        d._wake("OREN")
        await asyncio.sleep(0.05)

    asyncio.run(run())
    # the turn is cancelled first, the listen starts with the audio before the name, then the brain stops
    assert calls == [(True, "wake", [b"before", b"auren"], True), "interrupt"]


def _ended(d, session, monkeypatch, wait=0.3):
    released = []

    async def release(self):
        released.append(" ".join(session.finals))
        self.listening = None

    monkeypatch.setattr(daemon.Daemon, "release", release)
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": setattr(self, "detail", detail))
    d.listening, d.state = session, "listening"

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
    assert released == ["Open Firefox."]                      # the name's turn is set aside


@pytest.mark.parametrize("first, then, released_", [
    ("All right.", "Open Firefox.", ["Open Firefox."]),       # a clear call, misheard: taken for the name
    ("All right, open Firefox please.", None, ["All right, open Firefox please."]),   # long: goes as it is
    ("Lauren went to the store.", None, []),                  # another name: not a call
])
def test_a_clear_call_doesnt_need_the_name_written(monkeypatch, first, then, released_):
    d = _daemon(monkeypatch)
    s = _Session(check=True, clear=True)
    released, start = _ended(d, s, monkeypatch)

    async def run():
        task = await start()
        s.finals.append(first)
        s.turn.set()
        await asyncio.sleep(0.05)
        if then:
            assert not released and d.listening is s
            s.finals.append(then)
            s.turn.set()
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert released == released_ and (released_ or d.detail == "not for me")


def test_for_me(monkeypatch):
    d = _daemon(monkeypatch)
    clear, unclear = _Session(clear=True), _Session()
    assert d._for_me(unclear, "Auren, open it.") == "Open it."
    assert d._for_me(unclear, "All right.") is None
    assert d._for_me(clear, "All right.") == ""                # the local model heard the name alone
    assert d._for_me(clear, "Or in what time is it") == "What time is it"
    for other in ("Warren called.", "Hey Lauren, open Firefox.", "Auren's voice is very nice."):
        assert d._for_me(clear, other) is None
    d.handsfree.words_after_name = 2                           # ...or with words after it, in one breath
    assert d._for_me(clear, "Open Firefox.") == "Open Firefox."
    assert d._for_me(clear, "Orange stop.") == "Orange stop."


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


def test_a_connection_that_ends_is_reported_without_waiting(monkeypatch):
    d = _daemon(monkeypatch)
    s = _Session()
    s.speaking = True                       # words were coming when it failed
    released, start = _ended(d, s, monkeypatch, wait=60)

    async def run():
        task = await start()
        s.closed = True
        await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert released == [""]


def test_a_reply_that_comes_after_a_stop_is_not_spoken(tmp_path, monkeypatch):
    d = _daemon(monkeypatch)
    d.tools = Runtime(tmp_path / "t.sock")
    said, records = [], []

    class Brain:
        using_backup = False

        def ask(self, text):
            d.tools.cancel(daemon.SESSION)          # the name was heard as the reply came in
            yield ("say", "Closed it.")

    async def say(self, text):
        said.append(text)

    monkeypatch.setattr(daemon.Daemon, "say", say)
    monkeypatch.setattr(daemon.Daemon, "_history", lambda self, r: records.append(r))
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": setattr(self, "detail", detail))
    d.brain = Brain()

    async def run():
        d.busy, d.loop = asyncio.Lock(), asyncio.get_running_loop()
        await d.respond("close it", source="voice")

    asyncio.run(run())
    assert said == [] and records[-1]["reply"] == "(cancelled)" and d.detail == "cancelled"


def test_the_talk_key_keeps_its_own_mic_with_hands_free_on(monkeypatch):
    d = _daemon(monkeypatch)
    made = []

    class Listening:
        def __init__(self, vocab, external=False, turn_silence=None, **kw):
            made.append((external, turn_silence))

        async def start(self):
            pass

    monkeypatch.setattr(daemon, "Listening", Listening)
    monkeypatch.setattr(daemon.notice, "show", lambda *a, **k: None)
    monkeypatch.setattr(daemon.wake, "chime", lambda: None)
    d.vocab = []

    async def run():
        await d.press()                          # Super+A: its own pw-record, ended by the key
        d.listening = None
        await d.press(hands_free="wake")         # the name: the open mic, ended by a pause
        d.listening = None

    asyncio.run(run())
    assert made == [(False, None), (True, (500, 1200))]


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

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False, clear=False):
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


def test_a_dropped_listen_ends_the_session_and_leaves_no_tasks(monkeypatch):
    """A fake speech service: the listen is cancelled (a false start); it must say Terminate and clean up."""
    import websockets

    got = []

    async def service(ws):
        await ws.send(json.dumps({"type": "Begin"}))
        async for msg in ws:
            got.append(msg if isinstance(msg, str) else "audio")

    async def run():
        async with websockets.serve(service, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(ears, "URL", f"ws://127.0.0.1:{port}")
            monkeypatch.setattr(ears, "secret", lambda name: "key")
            s = ears.Listening([], external=True, turn_silence=(500, 1200))
            await s.start()
            s.push(b"\0" * 3200)
            for _ in range(50):
                if got:
                    break
                await asyncio.sleep(0.02)
            await s.cancel()
            await asyncio.sleep(0.1)
            return s

    s = asyncio.run(run())
    assert any("Terminate" in m for m in got if m != "audio")
    assert s._sender.done() and all(t.done() for t in s._tasks)


def test_a_cancel_detaches_the_listen_before_waiting_on_it(monkeypatch):
    d = _daemon(monkeypatch, "listening")
    seen = []

    class Slow(_Session):
        async def cancel(self):
            seen.append(d.listening)             # what _end_on_pause would see meanwhile
            await asyncio.sleep(0.01)

    d.listening = Slow(["Open Firefox."])
    asyncio.run(d.cancel())
    assert seen == [None] and d.listening is None


def test_a_wake_queued_before_hands_free_went_off_does_nothing(monkeypatch):
    d = _daemon(monkeypatch)
    monkeypatch.setattr(daemon.Daemon, "press", lambda *a, **k: pytest.fail("pressed"))

    async def run():
        d._wake("AUREN")                         # scheduled...
        d.handsfree = None                       # ...then turned off before it ran
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert not d._waking


def test_dropping_an_old_listen_doesnt_undo_a_newer_one(monkeypatch):
    d = _daemon(monkeypatch, "listening")
    monkeypatch.setattr(daemon.Daemon, "idle", lambda self, detail="": pytest.fail("idle over a newer listen"))
    old, new = _Session(), _Session()

    async def cancel():
        d.listening, d.state = new, "listening"  # a new listen starts while the old one closes

    old.cancel = cancel
    d.listening = old
    asyncio.run(d._drop(old, "heard nothing"))
    assert d.listening is new


def test_the_echo_guard_lasts_a_moment_after_auren_stops_talking(monkeypatch):
    d = _daemon(monkeypatch)
    monkeypatch.setattr(daemon.Daemon, "press", lambda *a, **k: pytest.fail("pressed"))
    now = daemon.time.monotonic()
    d._spoken.append((now - 2, "Just say Auren."))
    d._spoke_until = now - 0.3
    d._wake("AUREN")
    assert not d._waking
    d._spoke_until = now - 5                     # long ago: the name is the user's
    d._spoken.clear()
    assert not d._saying_the_name()


def test_a_service_that_closes_first_ends_the_listen(monkeypatch):
    import websockets

    async def service(ws):
        await ws.send(json.dumps({"type": "Begin"}))
        await ws.close()                         # no Termination, and no audio comes after it

    async def run():
        async with websockets.serve(service, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            monkeypatch.setattr(ears, "URL", f"ws://127.0.0.1:{port}")
            monkeypatch.setattr(ears, "secret", lambda name: "key")
            s = ears.Listening([], external=True, turn_silence=(500, 1200))
            await s.start()
            for _ in range(50):
                if s.closed:
                    break
                await asyncio.sleep(0.02)
            closed = s.closed
            await s.cancel()
            return closed

    assert asyncio.run(run())


def test_a_listen_started_during_a_cancel_is_left_alone(monkeypatch):
    d = _daemon(monkeypatch, "listening")
    new = _Session()

    class Old(_Session):
        async def cancel(self):
            d.listening, d.state = new, "listening"     # the key pressed again while the old one closes

    d.listening = Old()
    asyncio.run(d.cancel())
    assert d.listening is new and d.state == "listening"


def test_the_name_s_audio_starts_where_the_spotter_heard_it(monkeypatch):
    d = _daemon(monkeypatch)
    pressed = []

    async def press(self, fresh=False, hands_free=None, preroll=(), check=False, clear=False):
        pressed.append(list(preroll))

    monkeypatch.setattr(daemon.Daemon, "press", press)

    async def run():
        d._wake("ORIN", 7)
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert pressed == [[b"auren"]]


def _learning(monkeypatch, d):
    saved = []
    monkeypatch.setattr(daemon.Daemon, "_save", lambda self, c: saved.append(c) or self.settings.update(c))
    monkeypatch.setattr(wake, "chime", lambda: None)
    monkeypatch.setattr(daemon.notice, "show", lambda *a, **k: None)
    return saved


def test_learn_my_voice_saves_how_the_name_sounds(monkeypatch):
    d = _daemon(monkeypatch)
    saved = _learning(monkeypatch, d)
    monkeypatch.setattr(daemon.Daemon, "press", lambda *a, **k: pytest.fail("the name started a listen"))

    async def run():
        assert d._handsfree_command(["learn"]) == "handsfree learn"
        for text in ("MORN", "OR IN", "MORN", "MORNE"):
            d._said(text)
            d._wake("OR IN", 1)                     # while learning, the name doesn't start a listen
        assert d._learner.step == "other"
        d._said("WHAT IS THE WEATHER TODAY")
        assert d._learner.step == "done"
        state = d._learner.state()
        await asyncio.sleep(0)
        return state

    state = asyncio.run(run())
    assert saved == [{"handsfree.heard_as": ["morn", "or in", "morne"]}]
    assert d.handsfree.mine == ["morn", "or in", "morne"] and "1 of 4" in state["prompt"]


def test_learn_my_voice_turns_hands_free_on_first(monkeypatch):
    d = _daemon(monkeypatch)
    d.settings["handsfree"], d.handsfree = False, None
    started = []
    monkeypatch.setattr(daemon.Daemon, "_save", lambda self, c: self.settings.update(c))
    monkeypatch.setattr(daemon.Daemon, "_start_handsfree", lambda self: started.append(True))
    assert d._handsfree_command(["learn"]) == "handsfree learn: starting"
    assert d._learn_pending and started and d.settings["handsfree"] is True
    assert d._handsfree_command(["learn", "stop"]) == "handsfree learn stopped" and not d._learn_pending


def test_learn_my_voice_waits_for_idle_and_the_key_ends_it(monkeypatch):
    d = _daemon(monkeypatch, "speaking")
    _learning(monkeypatch, d)
    assert d._handsfree_command(["learn"]).startswith("busy")
    d.state = "idle"

    async def run():
        d._handsfree_command(["learn"])
        d._said("ORIN")
        assert d._learner.samples == ["ORIN"]
        d.listening = _Session()                    # stands in for an open listen: press() stops before it
        await d.press()

    asyncio.run(run())
    assert d._learner is None


def test_forget_voice_clears_the_learned_spellings(monkeypatch):
    d = _daemon(monkeypatch)
    d.settings["handsfree.heard_as"] = ["morn"]
    _learning(monkeypatch, d)
    assert d._handsfree_command(["forget-voice"]) == "handsfree forget-voice"
    assert d.settings["handsfree.heard_as"] == [] and d.handsfree.mine == []


def test_learning_again_can_drop_a_spelling_learned_before():
    learner = wake.Learner("medium", mine=["morn", "or in"])
    for text in ("MORN", "MORN", "ORIN", "MORN"):
        learner.heard(text)
    learner.heard("GOOD MORN TO YOU")
    assert learner.learned == ["or in", "orin"] and learner.added == 1


def test_phrases_from_before_learning_and_other_work_dont_count(monkeypatch):
    d = _daemon(monkeypatch)
    _learning(monkeypatch, d)
    d.handsfree.position = 100

    async def run():
        d._handsfree_command(["learn"])
        d._said("ORIN", 90)                          # began before Learn my voice
        assert d._learner.samples == []
        d._said("ORIN", 101)
        assert d._learner.samples == ["ORIN"]

    asyncio.run(run())


def test_other_work_ends_learning(tmp_path, monkeypatch):
    d = _daemon(monkeypatch)
    _learning(monkeypatch, d)
    monkeypatch.undo()                               # the real set_state, writing to a temp state file
    monkeypatch.setattr(daemon, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(daemon, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(daemon.notice, "show", lambda *a, **k: None)
    d.brain = type("B", (), {"using_backup": False, "mode": "auto", "primary": type("P", (), {"effort": "low"})()})()
    d.speak_enabled, d._hf_problem = False, ""

    async def run():
        d._handsfree_command(["learn"])
        assert json.loads((tmp_path / "state.json").read_text())["handsfreeLearn"]["step"] == "name"
        d.set_state("thinking", "a typed request")
        return json.loads((tmp_path / "state.json").read_text())["handsfreeLearn"]

    assert asyncio.run(run()) is None and d._learner is None


def test_the_key_only_stops_learning(monkeypatch):
    d = _daemon(monkeypatch)
    _learning(monkeypatch, d)

    async def run():
        d._handsfree_command(["learn"])
        return await d.press()

    assert asyncio.run(run()) == "stopped learning" and d.listening is None and d._learner is None


def test_learn_retries_a_failed_start(monkeypatch):
    d = _daemon(monkeypatch)
    d.handsfree, started = None, []
    monkeypatch.setattr(daemon.Daemon, "_start_handsfree", lambda self: started.append(True))
    assert d._handsfree_command(["learn"]) == "handsfree learn: starting" and started and d._learn_pending


class _Recognizer:
    """Stands in for sherpa's OnlineRecognizer: each feed, the next step of `script`: (text, steps the
    model ran, phrase ended?). Words are 0.3 s apart."""

    def __init__(self, script):
        self.script, self.resets, self.streams = list(script), 0, 0
        self.now = ("", 0, False)

    def create_stream(self):
        self.streams += 1
        return type("Stream", (), {"accept_waveform": lambda self, rate, samples: None})()

    def is_ready(self, stream):
        if self.steps:
            self.steps -= 1
            return True
        return False

    def decode_stream(self, stream):
        pass

    def get_result_all(self, stream):
        words = self.now[0].split()
        return type("R", (), {"text": self.now[0], "tokens": [" " + w for w in words],
                              "timestamps": [0.3 * i for i in range(len(words))], "start_time": 0.0})()

    def is_endpoint(self, stream):
        return self.now[2]

    def reset(self, stream):
        self.resets += 1

    def step(self):
        self.now = self.script.pop(0)
        self.steps = self.now[1]


def _spotter(script, mine=()):
    sp = wake.Spotter.__new__(wake.Spotter)
    sp.rec, sp.sensitivity, sp.mine, sp.after_name = _Recognizer(script), "medium", tuple(mine), 0
    sp.restart()
    return sp


def _run(sp, n):
    out = []
    for _ in range(n):
        sp.rec.step()
        out += [e[:2] for e in sp.feed(np.zeros(800, np.float32))]
    return out


def test_a_last_word_counts_only_after_a_model_step_left_it_unchanged():
    # "MORN" appears; feeds without a model step don't make it final; the next step makes it "MORNING"
    sp = _spotter([("MORN", 1, False), ("MORN", 0, False), ("MORN", 0, False), ("MORNING", 1, False)], mine=["morn"])
    assert _run(sp, 4) == []
    sp = _spotter([("MORN", 1, False), ("MORN", 0, False), ("MORN", 1, False)], mine=["morn"])
    assert _run(sp, 3) == [("name", "MORN")]


def test_words_after_the_name_are_counted_until_the_pause():
    sp = _spotter([("OR IN OPEN", 1, False), ("OR IN OPEN FIREFOX", 1, False), ("OR IN OPEN FIREFOX", 1, True),
                   ("HELLO", 1, False)])
    assert _run(sp, 3) == [("name", "OR IN"), ("said", "OR IN OPEN FIREFOX")] and sp.after_name == 2
    _run(sp, 1)
    assert sp.after_name == 2                                 # a later phrase without the name: unchanged


def test_a_gap_in_the_audio_starts_the_model_over(monkeypatch):
    """Chunks skipped while paused: the spotter gets a fresh stream for the next one."""
    hf = wake.HandsFree(lambda *a: None, pause_when_busy=False)
    hf.spotter = _spotter([("", 0, False)] * 3)
    hf._running, hf._loop = True, None
    hf._work.put((1, bytes(wake.CHUNK), False))
    hf._work.put((5, bytes(wake.CHUNK), True))
    hf._work.put(None)
    hf.spotter.rec.step = lambda: None
    hf.spotter.rec.steps = 0
    hf._spot()
    assert hf.spotter.rec.streams == 2


def test_events_after_stop_are_dropped():
    calls = []
    hf = wake.HandsFree(lambda *a: calls.append(a), pause_when_busy=False)
    hf._running = True
    hf._call(hf.on_wake, "ORIN", 1, True)
    hf._running = False
    hf._call(hf.on_wake, "ORIN", 2, True)
    assert calls == [("ORIN", 1, True)]
