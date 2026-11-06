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
    if final is not None:
        session.finals.append(final)
        session.partial = ""
    else:
        session.partial = partial
    session._count_words()


def test_quiet_counts_from_the_start_until_the_first_word(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    clock.now += 4
    assert s.quiet_seconds == 4 and s.words == 0
    _heard(s, "open")
    clock.now += 1
    assert s.quiet_seconds == 1 and s.words == 1


def test_only_new_words_reset_the_quiet(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    _heard(s, "open spotify and play")
    clock.now += 2
    _heard(s, "open Spotify and")                  # a partial being revised isn't speech
    _heard(s, final="Open Spotify and play.")      # the final of what was already heard
    assert s.quiet_seconds == 2
    _heard(s, "something")                         # the next turn starts
    assert s.quiet_seconds == 0 and s.text == "Open Spotify and play. something"


def test_a_final_written_shorter_than_its_partial_is_not_speech(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    _heard(s, "set a timer for twenty five minutes")
    clock.now += 3
    _heard(s, final="Set a timer for 25 minutes.")
    assert s.quiet_seconds == 3 and s.words == 7


def test_the_mic_still_speaking_holds_the_clock_until_its_words_come(clock):
    # The last words of a sentence often arrive only with the final transcript.
    s = ears.Listening([])
    s.opened_at = clock.now
    s._hear_level(0.1)                             # the room
    _heard(s, "are you able to do")
    clock.now += 1.5
    s._hear_level(0.6)                             # "it or not?", not written yet
    clock.now += 1
    s._hear_level(0.1)
    assert s.quiet_seconds == 1


def test_noise_holds_the_clock_for_a_moment_at_most(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    s._hear_level(0.1)
    _heard(s, "play music")
    for _ in range(10):                            # a loud fan or music, for 10 s after the last word
        clock.now += 1
        s._hear_level(0.5)
    assert s.quiet_seconds == 10 - ears.TAIL


def test_sound_before_the_first_word_is_not_talking(clock):
    s = ears.Listening([])
    s.opened_at = clock.now
    s._hear_level(0.1)
    clock.now += 4
    s._hear_level(0.7)
    assert s.quiet_seconds == 4


# --- the daemon -------------------------------------------------------------------------------------

class _Session:
    """A listen that goes through `steps`, (quiet seconds, words) per check; after the last the key is pressed."""

    def __init__(self, d, steps):
        self.d, self.steps = d, iter(steps)
        self.words, self.held_seconds, self.text, self.quiet = 0, 1.0, "open spotify", 0.0

    @property
    def quiet_seconds(self):
        try:
            self.quiet, self.words = next(self.steps)
        except StopIteration:
            self.d.listening = None                  # the user pressed the key (the last check sees no change)
        return self.quiet


class _Harness(daemon.Daemon):
    def __init__(self, tmp_path, autosend=False, after=5):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", "safety": "ask",
                         "autosend": autosend, "autosendAfter": after}
        self.tools = Runtime(tmp_path / "tools.sock")
        self.voice = self.profile = None
        self.listening = None
        self.state, self.shown, self.released, self.cancelled = "idle", [], 0, 0

    def set_state(self, state, detail="", **extra):
        self.state = state
        self.shown.append((state, detail, extra))

    def _refresh_state(self):
        pass

    async def release(self):
        self.released += 1
        self.listening = None
        self.state = "thinking"
        return "heard"

    async def cancel(self):
        self.cancelled += 1


def _watch(tmp_path, monkeypatch, steps, autosend=False, after=5):
    monkeypatch.setattr(daemon, "QUIET_TICK", 0)

    async def go():
        d = _Harness(tmp_path, autosend, after)
        session = d.listening = _Session(d, steps)
        await d._watch_quiet(session, fresh=False)
        return d

    return asyncio.run(go())


def test_off_it_reminds_once_and_then_sends(tmp_path, monkeypatch, _no_notices):
    d = _watch(tmp_path, monkeypatch, [(1, 2), (3, 2), (5, 2), (9, 2), (12, 2)])
    reminders = [s for s in d.shown if s[2].get("remind")]
    assert reminders == [("listening", "open spotify", {"fresh": False, "remind": True})]
    assert d.released == 1
    assert any("Press Super+A to send" in cmd for cmd in _no_notices)


def test_talking_again_takes_the_reminder_away_and_it_can_come_back(tmp_path, monkeypatch):
    d = _watch(tmp_path, monkeypatch, [(3, 2), (0.5, 4), (3.2, 4)])
    assert [s[2].get("remind", False) for s in d.shown] == [True, False, True]
    assert d.released == 0


def test_on_it_sends_after_its_delay_without_a_reminder(tmp_path, monkeypatch):
    d = _watch(tmp_path, monkeypatch, [(1, 2), (3, 2), (4.9, 2), (5, 2)], autosend=True, after=5)
    assert d.shown == [] and d.released == 1


def test_nothing_said_stops_after_twelve_seconds_without_a_reminder(tmp_path, monkeypatch):
    d = _watch(tmp_path, monkeypatch, [(3, 0), (8, 0), (12, 0)], autosend=True, after=2)
    assert d.shown == [] and d.released == 1


def test_the_two_minute_cap_still_sends(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon, "MAX_LISTEN", 0.5)
    d = _watch(tmp_path, monkeypatch, [(0, 5)], autosend=True)
    assert d.released == 1


def test_the_key_pressed_just_after_a_send_by_itself_does_nothing(tmp_path):
    async def go():
        d = _Harness(tmp_path)
        d.state = "thinking"
        d._sent_by_itself = daemon.time.monotonic()
        return d, await d.command("talk")

    d, reply = asyncio.run(go())
    assert reply == "already sent" and d.cancelled == 0 and d.listening is None


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
