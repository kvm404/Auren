"""A listen that the user forgets to send: the reminder, the send by itself, and auto-send."""

import asyncio

import pytest

from auren import daemon, ears, settings
from auren.tools import Runtime

# --- when to remind and when to send ---------------------------------------------------------------

@pytest.mark.parametrize(("quiet", "heard", "autosend", "after", "what"), [
    (1.0, True, False, 5, ""),            # a short pause: keep listening
    (3.0, True, False, 5, "remind"),      # auto-send off: remind after 3 s
    (11.9, True, False, 5, "remind"),
    (12.0, True, False, 5, "send"),       # ... and send after 12 s
    (4.9, True, True, 5, ""),             # auto-send on: no reminder, its own delay
    (5.0, True, True, 5, "send"),
    (2.0, True, True, 2, "send"),
    (11.0, False, False, 5, ""),          # nothing said yet: room to think, no reminder
    (11.0, False, True, 2, ""),           # ... even with auto-send on
    (12.0, False, True, 2, "send"),       # nothing said in 12 s: stop (it ends as "didn't catch that")
])
def test_what_a_quiet_listen_does(quiet, heard, autosend, after, what):
    assert daemon.after_quiet(quiet, heard, autosend, after) == what


def test_auto_send_is_off_by_default_and_waits_five_seconds():
    values = settings.defaults()
    assert values["autosend"] is False and values["autosendAfter"] == 5


# --- counting words -----------------------------------------------------------------------------------

class _Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ears.time, "perf_counter", c)
    return c


def _heard(session, partial="", final=None):
    """A Turn message: live text, or (final=) the end of the turn."""
    if final is not None:
        session.finals.append(final)
        session.partial = ""
        session._turn_ended(final)
    else:
        session.partial = partial
        session._heard_live(partial)


def _listen(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    return s


def test_quiet_counts_from_the_start_until_the_first_word(clock):
    s = _listen(clock)
    clock.now += 4
    assert s.quiet_seconds == 4 and s.words == 0
    _heard(s, "open")
    clock.now += 1
    assert s.quiet_seconds == 1 and s.words == 1


def test_only_new_words_reset_the_quiet(clock):
    s = _listen(clock)
    _heard(s, "open spotify and play")
    clock.now += 2
    _heard(s, "open Spotify and")                  # a partial being revised isn't speech
    _heard(s, final="Open Spotify and play.")      # the final of what was already heard
    assert s.quiet_seconds == 2
    _heard(s, "something")                         # the next turn starts
    assert s.quiet_seconds == 0 and s.text == "Open Spotify and play. something"


def test_words_only_the_final_has_were_said_before_the_silence(clock):
    # Measured on a real stream: the live text stopped at 23 words, and the final, 10.5 s later (the
    # turn-silence end), had 26. Counting those 3 as talking pushed the 12 s send to ~22 s.
    s = _listen(clock)
    _heard(s, "and tell me are you able to do")
    clock.now += 10.5
    _heard(s, final="And tell me, are you able to do it or not?")
    assert s.quiet_seconds == 10.5 and s.words == 11
    _heard(s, "thanks")                            # talking again after it
    assert s.quiet_seconds == 0 and s.words == 12


def test_a_final_written_shorter_than_its_partial_is_not_speech(clock):
    s = _listen(clock)
    _heard(s, "set a timer for twenty five minutes")
    clock.now += 3
    _heard(s, final="Set a timer for 25 minutes.")
    assert s.quiet_seconds == 3 and s.words == 6
    _heard(s, "please")                            # the next turn's first word counts, though the final was shorter
    assert s.quiet_seconds == 0


def test_the_mic_still_speaking_holds_the_clock_until_its_words_come(clock):
    # The last words of a sentence often arrive only with the final transcript.
    s = _listen(clock)
    s._hear_level(0.1)                             # the room
    _heard(s, "are you able to do")
    clock.now += 1.5
    s._hear_level(0.6)                             # "it or not?", not written yet
    clock.now += 1
    s._hear_level(0.1)
    assert s.quiet_seconds == 1


def test_noise_holds_the_clock_for_a_moment_at_most(clock):
    s = _listen(clock)
    s._hear_level(0.1)
    _heard(s, "play music")
    for _ in range(10):                            # a loud fan or music, for 10 s after the last word
        clock.now += 1
        s._hear_level(0.5)
    assert s.quiet_seconds == 10 - ears.TAIL


def test_digital_silence_is_not_the_room(clock):
    # pw-record can start with exact zeros; taken as the room's level, any hum would count as speech.
    s = _listen(clock)
    s._hear_level(0.0)
    s._hear_level(0.15)                            # the room
    _heard(s, "open spotify")
    clock.now += 1
    s._hear_level(0.25)                            # a little louder: not speech (8 dB over the room is)
    clock.now += 1
    assert s.floor == 0.15 and s.quiet_seconds == 2


def test_sound_before_the_first_word_is_not_talking(clock):
    s = _listen(clock)
    s._hear_level(0.1)
    clock.now += 4
    s._hear_level(0.7)
    assert s.quiet_seconds == 4


# --- the daemon -------------------------------------------------------------------------------------

class _Session:
    """A listen. With `steps`, (quiet seconds, words) per check of the quiet watch; after the last, the key is
    pressed. Without, the test sets quiet_seconds and words itself."""

    def __init__(self, d, steps=None, quiet=0.0, words=0):
        self.d, self.steps = d, iter(steps) if steps is not None else None
        self.words, self.held_seconds, self.text, self.quiet = words, 1.0, "open spotify", quiet
        self.cancelled = 0

    @property
    def quiet_seconds(self):
        if self.steps is not None:
            try:
                self.quiet, self.words = next(self.steps)
            except StopIteration:
                self.d.listening = None              # the user pressed the key (the last check sees no change)
        return self.quiet

    @quiet_seconds.setter
    def quiet_seconds(self, value):
        self.quiet = value

    async def cancel(self):
        self.cancelled += 1


class _Level:
    def set(self, value):
        pass


class _Harness(daemon.Daemon):
    def __init__(self, tmp_path, autosend=False, after=5):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", "safety": "ask",
                         "autosend": autosend, "autosendAfter": after}
        self.tools = Runtime(tmp_path / "tools.sock")
        self.voice = self.profile = None
        self.listening = None
        self.level = _Level()
        self.state, self.shown, self.released, self.cancelled, self.pressed = "idle", [], 0, 0, 0
        self.fail_on = None                          # "remind" or "release": that step raises

    def set_state(self, state, detail="", **extra):
        if self.fail_on == "remind" and extra.get("remind"):
            raise OSError("disk full")
        self.state = state
        self.shown.append((state, detail, extra))

    def _refresh_state(self):
        pass

    async def release(self):
        self.listening = None
        if self.fail_on == "release":
            raise RuntimeError("speech service broke")
        self.released += 1
        self.state = "thinking"
        return "heard"

    async def cancel(self):
        self.cancelled += 1
        self.listening = None

    async def press(self, fresh=False):
        self.pressed += 1
        return "listening"


def _watch(tmp_path, monkeypatch, steps, autosend=False, after=5, fail_on=None):
    monkeypatch.setattr(daemon, "QUIET_TICK", 0)

    async def go():
        d = _Harness(tmp_path, autosend, after)
        d.fail_on = fail_on
        session = d.listening = _Session(d, steps)
        await d._watch_quiet(session, fresh=False)
        return d, session

    return asyncio.run(go())


def test_off_it_reminds_once_and_then_sends(tmp_path, monkeypatch, _no_notices):
    d, _ = _watch(tmp_path, monkeypatch, [(1, 2), (3, 2), (5, 2), (9, 2), (12, 2)])
    reminders = [s for s in d.shown if s[2].get("remind")]
    assert reminders == [("listening", "open spotify", {"fresh": False, "remind": True})]
    assert d.released == 1
    assert any("Press Super+A to send" in cmd for cmd in _no_notices)


def test_talking_again_takes_the_reminder_away_and_it_can_come_back(tmp_path, monkeypatch):
    d, _ = _watch(tmp_path, monkeypatch, [(3, 2), (0.5, 4), (3.2, 4)])
    assert [s[2].get("remind", False) for s in d.shown] == [True, False, True]
    assert d.released == 0


def test_a_revised_partial_keeps_the_reminder(tmp_path):
    # AssemblyAI re-sends the live text without new words (seen on a real stream): the bubble must keep the
    # reminder, and lose it once new words come.
    d = _Harness(tmp_path)
    s = d.listening = _Session(d, quiet=4, words=2)
    d._show_listening(s, fresh=False)
    assert d.shown[-1] == ("listening", "open spotify", {"fresh": False, "remind": True}) and d._reminding
    s.quiet_seconds = 0.1
    d._show_listening(s, fresh=False)
    assert d.shown[-1] == ("listening", "open spotify", {"fresh": False}) and not d._reminding


def test_live_text_from_an_earlier_listen_changes_nothing(tmp_path):
    d = _Harness(tmp_path)
    old = _Session(d, quiet=1, words=2)
    d.listening = _Session(d)
    d._show_listening(old, fresh=False)
    assert d.shown == []


def test_on_it_sends_after_its_delay_without_a_reminder(tmp_path, monkeypatch):
    d, _ = _watch(tmp_path, monkeypatch, [(1, 2), (3, 2), (4.9, 2), (5, 2)], autosend=True, after=5)
    assert d.shown == [] and d.released == 1


def test_nothing_said_stops_after_twelve_seconds_without_a_reminder(tmp_path, monkeypatch):
    d, _ = _watch(tmp_path, monkeypatch, [(3, 0), (8, 0), (12, 0)], autosend=True, after=2)
    assert d.shown == [] and d.released == 1


def test_the_two_minute_cap_still_sends(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "MAX_LISTEN", 0.5)
    d, _ = _watch(tmp_path, monkeypatch, [(0, 5)], autosend=True)
    assert d.released == 1


def test_a_failing_watch_stops_the_listen(tmp_path, monkeypatch, _no_notices):
    # The watch also enforces the 2-minute cap: dying silently would leave the mic open and billing.
    d, _ = _watch(tmp_path, monkeypatch, [(3, 2)], fail_on="remind")
    assert d.cancelled == 1 and d.listening is None
    assert any("Auren stopped listening" in " ".join(map(str, cmd)) for cmd in _no_notices)


def test_a_failing_send_still_closes_the_mic(tmp_path, monkeypatch):
    d, session = _watch(tmp_path, monkeypatch, [(12, 2)], fail_on="release")
    assert session.cancelled == 1 and d.state == "error" and "speech service broke" in d.shown[-1][1]


def test_the_key_pressed_just_after_a_send_by_itself_does_nothing(tmp_path, monkeypatch):
    d, _ = _watch(tmp_path, monkeypatch, [(12, 2)])

    async def press():
        return await d.command("talk")

    assert d.state == "thinking" and asyncio.run(press()) == "already sent"
    assert d.cancelled == 0 and d.pressed == 0


def test_a_slow_final_transcript_does_not_shorten_the_moment(tmp_path, monkeypatch):
    # The send waits for the final transcript (up to seconds): the moment counts from when it's done.
    now = [1000.0]
    monkeypatch.setattr(daemon.time, "monotonic", lambda: now[0])

    class Slow(_Harness):
        async def release(self):
            now[0] += 3
            return await super().release()

    monkeypatch.setattr(daemon, "QUIET_TICK", 0)

    async def go():
        d = Slow(tmp_path)
        d.listening = _Session(d, [(12, 2)])
        await d._watch_quiet(d.listening, fresh=False)
        now[0] += 1
        return d, await d.command("talk")

    d, reply = asyncio.run(go())
    assert reply == "already sent" and d.cancelled == 0 and d.pressed == 0


def test_the_key_talks_again_once_the_moment_is_over(tmp_path):
    d = _Harness(tmp_path)
    d._sent_by_itself = daemon.time.monotonic() - daemon.ALREADY_SENT - 0.1
    assert asyncio.run(d.command("talk")) == "listening" and d.pressed == 1


def test_after_nothing_was_said_the_key_is_a_new_try(tmp_path, monkeypatch):
    d, _ = _watch(tmp_path, monkeypatch, [(12, 0)])
    d.state = "idle"
    assert asyncio.run(d.command("talk")) == "listening" and d.pressed == 1


def test_the_autosend_command_saves_and_checks(tmp_path):
    async def go():
        d = _Harness(tmp_path)
        return d, [await d.command(c) for c in
                   ("autosend on", "autosend after 3", "autosend after 40", "autosend maybe", "autosend toggle")]

    d, replies = asyncio.run(go())
    assert replies[0] == "autosend on, after 5 s"
    assert replies[1] == "autosend on, after 3 s"
    assert replies[2].startswith("autosend after should be from 1 to 15")
    assert replies[3].startswith("usage:")
    assert replies[4] == "autosend off, after 3 s"
    saved, _ = settings.read(settings.CONFIG_FILE.read_text())
    assert saved["autosend"] is False and saved["autosendAfter"] == 3
