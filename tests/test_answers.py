"""The daemon reads a plain yes or no itself: no brain turn, and only for what was asked last."""

import asyncio
from pathlib import Path

from auren import daemon, settings
from auren.tools import Runtime

SESSION = daemon.SESSION


class _Brain:
    """Records what it is asked; says "ok"."""
    mode, using_backup = "auto", False

    def __init__(self):
        self.asked = []
        self.forgot = 0

    def forget(self):
        self.forgot += 1

    def ask(self, text):
        self.asked.append(text)
        yield ("say", "ok")


class _Harness(daemon.Daemon):
    """The daemon without a voice, ears or real brain: what it says, shows and records is kept."""

    def __init__(self, tmp_path: Path):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", "safety": "ask"}
        self.tools = Runtime(tmp_path / "tools.sock")
        self.brain = _Brain()
        self.voice = self.profile = self.listening = None
        self.vocab = []
        self.said, self.history, self.shown = [], [], []
        self.ran = []
        self._request = "Close the calculator."          # what the user asked when the brain asked its question

    async def say(self, text):
        self.said.append(text)

    def set_state(self, state, detail="", **extra):
        self.state = state
        self.shown.append((state, detail, extra))

    def _history(self, record):
        self.history.append(record)

    def _refresh_state(self):
        pass

    def ask(self, question, kinds=(), say=None, result="ok"):
        """The brain's tool asked `question` in the current message. say: what to say if the result needs no brain."""
        turn = self.tools._session(SESSION).turn
        return self.tools.confirmations.request(SESSION, turn, question, lambda: self.ran.append(question) or result,
                                                kinds=kinds, say=say)["token"]


def _run(tmp_path, scenario, level="ask"):
    async def go():
        d = _Harness(tmp_path)
        d.busy = asyncio.Lock()
        d.loop = asyncio.get_running_loop()
        d.tools.set_level(level)
        d.tools.new_turn(SESSION)                     # the user's request, in which the brain asked
        await scenario(d)
        return d

    return asyncio.run(go())


def test_a_plain_yes_runs_what_was_asked_without_the_brain(tmp_path):
    async def scenario(d):
        d.ask("Close Calc?", {"window"}, say=lambda r: "Closed Calc.")
        await d.respond("Yes, please.", source="voice", heard_s=0.3)

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close Calc?"] and d.said == ["Closed Calc."] and d.brain.asked == []
    assert d.history[-1]["brain"] == "none" and d.history[-1]["transcript"] == "Yes, please."
    assert d.history[-1]["tools"] == ["confirm_action"] and d.tools.batch(SESSION) == []


def test_the_brain_hears_about_it_with_the_next_message(tmp_path):
    async def scenario(d):
        d.ask("Close Calc?", {"window"}, say=lambda r: "Closed Calc.")
        await d.respond("yes", source="voice")
        await d.respond("what time is it", source="voice")
        await d.respond("and now?", source="voice")

    d = _run(tmp_path, scenario)
    first, second = d.brain.asked
    assert first.startswith("(You asked 'Close Calc?', the user said yes and it was done.)\n")
    assert first.endswith("what time is it") and second == "and now?"


def test_a_plain_no_drops_it_and_says_so_to_the_brain_later(tmp_path):
    async def scenario(d):
        d.ask("Delete the recordings?", {"files"})
        await d.respond("no, don't", source="voice")
        await d.respond("ok, show my windows", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == [] and d.said == ["Okay, I won't.", "ok"] and d.tools.batch(SESSION) == []
    assert len(d.brain.asked) == 1 and "the user said no, so none of it was done" in d.brain.asked[0]


def test_one_yes_answers_every_question_waiting(tmp_path):
    async def scenario(d):
        d.ask("Close A?", {"window"}, say=lambda r: "Closed A.")
        d.ask("Close B?", {"window"}, say=lambda r: "Closed B.")
        await d.respond("yeah go ahead", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close A?", "Close B?"] and d.said == ["Closed A. Closed B."]


def test_when_the_brain_has_more_to_do_it_is_told_what_happened(tmp_path):
    async def scenario(d):
        d.ask("Run `rm a`?", {"files"}, result="exit 0")
        d.ask("Run `rm b`?", {"files"}, result="error: no such file")
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario, level="task")
    assert d.said == ["ok"] and len(d.brain.asked) == 1
    told = d.brain.asked[0]
    assert "The user said yes" in told and "Run `rm a`? -> done: exit 0" in told
    assert "Run `rm b`? -> FAILED: error: no such file" in told
    assert d.history[-1]["tools"] == ["confirm_action"] and d.history[-1]["transcript"] == "yes"


def test_an_action_whose_result_needs_the_brain_is_not_announced_as_done(tmp_path):
    async def scenario(d):
        d.ask("Close Brave?", {"window"}, result="asked brave to close, but it is still open: a dialog",
              say=lambda r: "Closed Brave." if r.startswith("closed") else None)
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario)
    assert "Closed Brave." not in d.said and d.said == ["ok"]
    assert "still open" in d.brain.asked[0]                # the brain tells the user what really happened


def test_the_task_goes_on_after_a_yes_and_ends_with_the_next_message(tmp_path):
    async def scenario(d):
        d.ask("Run `rm a`?", {"files"})
        await d.respond("yes", source="voice")
        d.granted_after_yes = d.tools.confirmations.granted(SESSION)
        await d.respond("something else", source="voice")
        d.granted_after_next = d.tools.confirmations.granted(SESSION)

    d = _run(tmp_path, scenario, level="task")
    assert d.granted_after_yes == {"files"} and d.granted_after_next == frozenset()


def test_answering_goes_on_with_the_task(tmp_path):
    async def scenario(d):
        d.ask("Run `rm a`?", {"files"})
        await d.respond("yes", source="voice")                 # covers files for the task
        d.ask("Run `sudo ls`?", {"system"})                    # the floor still asks
        await d.respond("yes", source="voice")
        d.granted = d.tools.confirmations.granted(SESSION)

    d = _run(tmp_path, scenario, level="task")
    assert d.ran == ["Run `rm a`?", "Run `sudo ls`?"] and d.granted == {"files"}


def test_a_yes_to_something_else_is_not_an_answer(tmp_path):
    async def scenario(d):
        d.ask("Close Calc?", {"window"})
        d.tools.new_turn(SESSION)                     # the user moved on to another request first
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == [] and d.brain.asked == ["yes"]   # the brain, who knows the context, decides


def test_more_than_a_plain_yes_goes_to_the_brain(tmp_path):
    async def scenario(d):
        d.ask("Close Calc?", {"window"})
        await d.respond("yes but close the other one too", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == [] and d.brain.asked == ["yes but close the other one too"]


def test_yes_and_no_without_waiting_questions_go_to_the_brain(tmp_path):
    async def scenario(d):
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario)
    assert d.brain.asked == ["yes"]


def test_the_buttons_answer_even_after_something_else_was_said(tmp_path):
    async def scenario(d):
        assert await d.command("yes") == "nothing is waiting for an answer"
        d.ask("Close Calc?", {"window"}, say=lambda r: "Closed Calc.")
        d.tools.new_turn(SESSION)                     # the bubble still shows the question
        assert await d.command("yes") == "approved 1"
        await asyncio.sleep(0.05)

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close Calc?"] and d.said == ["Closed Calc."] and d.history[-1]["transcript"] == "yes"


def test_the_no_button_drops_the_questions(tmp_path):
    async def scenario(d):
        d.ask("Close Calc?", {"window"})
        assert await d.command("no") == "dropped 1"
        await asyncio.sleep(0.05)

    d = _run(tmp_path, scenario)
    assert d.ran == [] and d.said == ["Okay, I won't."]


def test_idle_shows_every_question_that_one_answer_covers(tmp_path):
    async def scenario(d):
        d.ask("Close A?", {"window"})
        d.idle()
        d.ask("Close B?", {"window"})
        d.idle()

    d = _run(tmp_path, scenario)
    first, last = d.shown[0], d.shown[-1]
    assert first[2]["awaiting"] and first[2]["question"] == "Close A?"
    assert last[2]["question"] == "2 things, one answer:\n• Close A?\n• Close B?" and last[2]["until"] > 0


def test_the_safety_command_sets_and_saves_the_level(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")

    async def scenario(d):
        assert (await d.command("safety nonsense")).startswith("usage: safety ask|task|free")
        assert await d.command("safety free") == "safety free"

    d = _run(tmp_path, scenario)
    assert d.tools.level == "free" and d.settings["safety"] == "free"
    assert settings.read((tmp_path / "config.toml").read_text())[0]["safety"] == "free"


def test_a_request_with_more_parts_goes_back_to_the_brain_after_the_yes(tmp_path):
    async def scenario(d):
        d._request = "Close the calculator and then open Spotify."
        d.ask("Close Calc?", {"window"}, say=lambda r: "Closed Calc.")
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close Calc?"] and d.said == ["ok"] and "The user said yes" in d.brain.asked[0]


def test_a_second_click_on_yes_does_not_approve_what_was_asked_after_the_first(tmp_path):
    async def scenario(d):
        a = d.ask("Close A?", {"window"})
        shown = {t for t, _ in d.tools.confirmations.batch(SESSION)}
        assert shown == {a}
        await d.answer(True, "button", shown)
        d.ask("Close B?", {"window"})                     # the continuation asked something new
        await d.answer(True, "button", shown)             # a queued second click, for A only
        d.after = [p.question for p in d.tools.batch(SESSION)]

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close A?"] and d.after == ["Close B?"]


def test_two_quick_yes_commands_run_it_once(tmp_path):
    async def scenario(d):
        d.ask("Close A?", {"window"}, say=lambda r: "Closed A.")
        assert await d.command("yes") == "approved 1"
        assert await d.command("yes") == "approved 1"     # both saw the same question
        await asyncio.sleep(0.1)

    d = _run(tmp_path, scenario)
    assert d.ran == ["Close A?"]


def test_a_fresh_conversation_forgets_what_was_asked_in_the_old_one(tmp_path):
    async def scenario(d):
        d.ask("Run `rm a`?", {"files"})
        await d.forget(say=False)
        await d.respond("yes", source="voice")

    d = _run(tmp_path, scenario)
    assert d.ran == [] and d.brain.forgot == 1 and d.brain.asked == ["yes"] and d.tools.batch(SESSION) == []


def test_the_replay_guard_survives_the_brain_going_on_after_a_yes(tmp_path):
    async def scenario(d):
        d.ask("Run `rm a`?", {"files"})
        await d.respond("yes", source="voice")
        d.acted = d.tools.acted(SESSION)

    d = _run(tmp_path, scenario, level="task")
    assert d.acted                                       # a backup brain must not start this request over
