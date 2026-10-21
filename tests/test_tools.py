"""The tool runtime: sessions, turns, bound confirmations, cancel, and the MCP adapter in front of it."""

import json

import pytest

from auren import tools
from auren.hands import browser as web
from auren.hands import desktop


@pytest.fixture
def rt(tmp_path):
    r = tools.Runtime(tmp_path / "tools.sock")
    r.new_turn("daemon")
    return r


def _ask(rt, name, **args) -> str:
    out = rt.call("daemon", name, args)
    assert len(out) == 1
    return out[0]


def test_every_tool_has_a_description_and_resolved_types():
    for name, t in tools.TOOLS.items():
        assert t.fn.__doc__, name
        for p in tools.signature(name).parameters.values():
            assert not isinstance(p.annotation, str), (name, p.name)


def test_close_window_is_bound_to_the_window_asked_about(rt, monkeypatch):
    calc = {"address": "0x1", "class": "omacalc", "title": "Calc", "workspace": {"name": "1"}}
    monkeypatch.setattr(desktop, "find_window", lambda q: calc)
    monkeypatch.setattr(desktop, "dispatch", lambda lua: None)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    open_windows = [calc]
    monkeypatch.setattr(desktop, "windows", lambda: list(open_windows))

    asked = json.loads(_ask(rt, "close_window", window="calc"))
    assert asked["status"] == "needs_confirmation"
    assert not rt.acted("daemon")                     # asking isn't acting
    assert "hasn't answered" in _ask(rt, "confirm_action", token=asked["token"])

    asked = json.loads(_ask(rt, "close_window", window="calc"))
    rt.new_turn("daemon")
    open_windows.clear()                              # the user closed it themselves meanwhile
    assert "already closed" in _ask(rt, "confirm_action", token=asked["token"])


def test_a_session_cant_redeem_anothers_confirmation(rt):
    asked = json.loads(_ask(rt, "lock_screen"))
    rt.new_turn("chat-1")
    rt.new_turn("chat-1")
    assert "unknown" in rt.call("chat-1", "confirm_action", {"token": asked["token"]})[0]
    assert "token" in rt.summary("daemon") and rt.summary("chat-1") == ""


class _Page:
    """A fake page: numbered fields (renumbered by each read), pins that stay on their element."""

    def __init__(self, monkeypatch, fields):
        self.fields, self.pins, self.typed = list(fields), {}, []
        self.where = {"browser": "chrome", "tab": 7, "url": "https://chat.example/room"}
        monkeypatch.setattr(web, "page", lambda: dict(self.where))
        monkeypatch.setattr(web, "pin", self.pin)
        monkeypatch.setattr(web, "field_of", self.field_of)
        monkeypatch.setattr(web, "browser_type", self.type)

    def element(self, target):
        if target.startswith("pin:"):
            return self.pins.get(target)
        n = int(target) - 1
        return self.fields[n] if 0 <= n < len(self.fields) else None

    def pin(self, target, is_field=False):
        el = self.element(target)
        if el is None:
            return None
        self.pins[f"pin:{len(self.pins)}"] = el
        return f"pin:{len(self.pins) - 1}"

    def field_of(self, target):
        el = self.element(target)
        return None if el is None or el not in self.fields else {"label": el, "search": el == "Search"}

    def type(self, target, text, submit=False):
        self.typed.append((self.element(target), text, submit))
        self.typed_on = self.where["tab"]
        return "typed"


def test_submitting_a_search_box_runs_but_other_fields_ask(rt, monkeypatch):
    p = _Page(monkeypatch, ["Search", "Message"])
    assert "typed" in _ask(rt, "browser_type", target="1", text="weather", submit=True)
    assert p.typed == [("Search", "weather", True)]

    asked = json.loads(_ask(rt, "browser_type", target="2", text="hello there", submit=True))
    assert asked["status"] == "needs_confirmation" and "Message" in asked["ask_user"]
    assert len(p.typed) == 1
    assert "typed" in _ask(rt, "browser_type", target="2", text="draft only")      # no Enter: no question

    rt.new_turn("daemon")
    p.where["url"] = "https://chat.example/other-room"
    assert "page changed" in _ask(rt, "confirm_action", token=asked["token"])
    assert p.typed[-1] == ("Message", "draft only", False)


def test_a_confirmed_message_goes_to_the_field_asked_about(rt, monkeypatch):
    p = _Page(monkeypatch, ["Search", "Message to Alice"])
    asked = json.loads(_ask(rt, "browser_type", target="2", text="private text", submit=True))
    rt.new_turn("daemon")
    p.fields = ["Message to Alice", "Search"]          # read again: renumbered, field 2 is now the search box
    assert "typed" in _ask(rt, "confirm_action", token=asked["token"])
    assert p.typed == [("Message to Alice", "private text", True)]

    asked = json.loads(_ask(rt, "browser_type", target="1", text="private text", submit=True))
    rt.new_turn("daemon")
    p.fields = ["Message to Bob", "Search"]            # the chat switched without a new address
    assert "isn't there any more" in _ask(rt, "confirm_action", token=asked["token"])
    assert len(p.typed) == 1


def test_a_confirmation_is_checked_and_run_without_another_browser_call_between(rt, monkeypatch):
    import threading

    p = _Page(monkeypatch, ["Message"])
    asked = json.loads(_ask(rt, "browser_type", target="1", text="hi", submit=True))
    rt.new_turn("daemon")
    checking, may_switch = threading.Event(), threading.Event()
    real_field_of = p.field_of

    def slow_field_of(target):
        checking.set()
        may_switch.wait(0.3)          # give the other call every chance to switch tabs here
        return real_field_of(target)

    monkeypatch.setattr(web, "field_of", slow_field_of)
    monkeypatch.setattr(web, "browser_open", lambda url, browser="": p.where.update(tab=8) or "opened")
    confirm = threading.Thread(target=lambda: rt.call("daemon", "confirm_action", {"token": asked["token"]}))
    confirm.start()
    checking.wait(1)
    other = threading.Thread(target=lambda: rt.call("daemon", "browser_open", {"url": "x.com"}))
    other.start()
    may_switch.set()
    confirm.join()
    other.join()
    assert p.typed == [("Message", "hi", True)] and p.typed_on == 7 and p.where["tab"] == 8


def test_a_confirmed_shortcut_goes_to_the_window_asked_about(rt, monkeypatch):
    from auren.hands import screen

    target = {"window": "0xA"}
    monkeypatch.setattr(screen, "key_target", lambda: target["window"])
    monkeypatch.setattr(screen, "press_keys", lambda keys: f"pressed {keys} in {target['window']}")
    asked = json.loads(_ask(rt, "press_keys", keys="ctrl+w"))
    rt.new_turn("daemon")
    target["window"] = "0xB"
    assert "different window" in _ask(rt, "confirm_action", token=asked["token"])


def test_cancel_refuses_further_tools_until_the_next_message(rt, monkeypatch):
    monkeypatch.setattr(desktop, "system_info", lambda what: "battery 80%")
    rt.cancel("daemon")
    assert "cancelled" in _ask(rt, "system_info", what="battery")
    rt.new_turn("daemon")
    assert _ask(rt, "system_info", what="battery") == "battery 80%"


def test_acted_counts_only_what_shouldnt_be_repeated(rt, monkeypatch):
    monkeypatch.setattr(desktop, "system_info", lambda what: "ok")
    monkeypatch.setattr(desktop, "notify", lambda text: "shown")
    _ask(rt, "system_info", what="time")
    assert not rt.acted("daemon")
    _ask(rt, "notify", text="hi")
    assert rt.acted("daemon")
    rt.new_turn("daemon")
    assert not rt.acted("daemon")


def test_bad_calls_come_back_as_errors(rt):
    assert _ask(rt, "no_such_tool").startswith("error: no tool")
    assert _ask(rt, "notify", wrong=1).startswith("error: wrong arguments")


def test_a_failed_tool_is_logged_with_why(rt, monkeypatch, caplog):
    # the brain only said "my clicks keep failing": the log has to keep the error and what was asked
    from auren.hands import screen

    def broken(*a):
        return undefined_name  # noqa: F821
    monkeypatch.setattr(screen, "click", broken)
    with caplog.at_level("INFO", logger="auren.tools"):
        assert _ask(rt, "click_at", x=10, y=20, target="the search box").startswith("error: unexpected NameError")
    assert "NameError" in caplog.text and '"x": 10' in caplog.text and "the search box" in caplog.text
    assert "Traceback" in caplog.text


def test_what_was_typed_stays_out_of_the_log(rt, monkeypatch, caplog):
    # a password typed into a field that's gone must not end up in daemon.log
    from auren.hands import screen
    monkeypatch.setattr(screen, "type_text", lambda text: (_ for _ in ()).throw(screen.ScreenError("no window")))
    with caplog.at_level("INFO", logger="auren.tools"):
        assert _ask(rt, "type_text", text="hunter2-secret").startswith("error: no window")
    assert "no window" in caplog.text and "<14 characters>" in caplog.text and "hunter2" not in caplog.text


def test_an_approved_action_that_fails_is_logged(rt, monkeypatch, caplog):
    def broken():
        raise desktop.DesktopError("no screen locker")
    monkeypatch.setattr(desktop, "lock_screen", broken)
    asked = json.loads(_ask(rt, "lock_screen"))
    rt.new_turn("daemon", same_task=True)
    with caplog.at_level("INFO", logger="auren.tools"):
        [outcome] = rt.answer("daemon", True)
    assert not outcome.ok and asked["token"]
    assert "approved action failed" in caplog.text and "no screen locker" in caplog.text


def test_the_mcp_adapter_forwards_text_and_images(rt, monkeypatch):
    from auren import server
    from auren.hands import screen

    monkeypatch.setattr(screen, "look", lambda: (b"\xff\xd8jpeg", "a note"))
    monkeypatch.setattr(desktop, "system_info", lambda what: f"{what}: fine")
    rt.start()
    try:
        monkeypatch.setattr(server, "SOCKET", str(rt.socket))
        assert server.forward("system_info", {"what": "disk"}) == ["disk: fine"]
        note, image = server.forward("look_at_screen", {})
        assert note == "a note" and image.data == b"\xff\xd8jpeg"
        assert (rt.socket.stat().st_mode & 0o777) == 0o600
    finally:
        rt.stop()
    assert "aren't reachable" in server.forward("system_info", {"what": "disk"})[0]


def test_backup_brain_doesnt_start_over_after_an_action(rt, monkeypatch):
    from auren import brain as b

    class Limited:
        def ask(self, text):
            rt.call("daemon", "notify", {"text": "half done"})
            raise b.UsageLimit("usage limit reached")
            yield

    class Backup:
        asked = []

        def ask(self, text):
            self.asked.append(text)
            yield ("say", "codex did it")

    monkeypatch.setattr(desktop, "notify", lambda text: "shown")
    brain = b.Brain.__new__(b.Brain)
    brain.runtime, brain.session, brain.mode, brain.limited_since = rt, "daemon", "auto", None
    brain.primary, brain.backup = Limited(), Backup()
    events = list(brain.ask("do two things"))
    assert Backup.asked == [] and "stopped there" in events[-1][1]

    rt.new_turn("daemon")
    brain.limited_since = None

    class LimitedAtOnce(Limited):
        def ask(self, text):
            raise b.UsageLimit("usage limit reached")
            yield

    brain.primary = LimitedAtOnce()
    events = list(brain.ask("what time is it"))
    assert events[0][0] == "switch" and events[-1] == ("say", "codex did it")


# --- approval levels, batches and answers ----------------------------------------------------------------

@pytest.fixture
def shell_log(rt, monkeypatch):
    ran = []
    monkeypatch.setattr(tools, "_run_command", lambda command, *a: ran.append(command) or f"ran {command}")
    return ran


def _asked(out: str) -> bool:
    return out.startswith("{") and json.loads(out).get("status") == "needs_confirmation"


def test_ask_level_asks_every_time(rt, shell_log):
    for command in ("rm a", "rm b"):
        assert _asked(_ask(rt, "run_command", command=command))
    assert shell_log == [] and not rt.acted("daemon")


def test_free_level_is_full_access_but_sending_secrets_and_wiping_ask(rt, shell_log, monkeypatch):
    rt.set_level("free")
    locked = []
    monkeypatch.setattr(desktop, "lock_screen", lambda: locked.append(1) or "locked")
    assert _ask(rt, "run_command", command="rm a") == "ran rm a"
    assert _ask(rt, "run_command", command="sudo pacman -S foo") == "ran sudo pacman -S foo"
    assert _ask(rt, "lock_screen") == "locked" and locked == [1]
    assert rt.acted("daemon")                                     # it ran: a backup brain mustn't redo it
    for asks in ("cat ~/.ssh/id_rsa", "git push origin main", "rm .env", "rm -rf ~", "auren-ctl yes"):
        assert _asked(_ask(rt, "run_command", command=asks)), asks
    assert shell_log == ["rm a", "sudo pacman -S foo"]


def test_free_level_still_asks_before_buying_and_sending(rt, monkeypatch):
    rt.set_level("free")
    monkeypatch.setattr(web, "label_of", lambda target: target)
    monkeypatch.setattr(web, "pin", lambda target, is_field=False: "pin:1")
    monkeypatch.setattr(web, "page", lambda: {"url": "https://shop.example", "tab": 1})
    monkeypatch.setattr(web, "browser_click", lambda target, *a: f"clicked {target}")
    assert _asked(_ask(rt, "browser_click", target="Send"))
    assert _asked(_ask(rt, "browser_click", target="Buy now"))


def test_task_level_one_yes_covers_the_rest_of_the_task_for_the_same_kinds(rt, shell_log):
    rt.set_level("task")
    assert _asked(_ask(rt, "run_command", command="rm a"))
    rt.new_turn("daemon")                                         # the user's yes
    assert [o.ok for o in rt.answer("daemon", True)] == [True] and shell_log == ["rm a"]
    assert _ask(rt, "run_command", command="rm b") == "ran rm b"  # same task, same kind: no question
    assert _asked(_ask(rt, "run_command", command="sudo rm c"))    # the floor is never covered
    assert _asked(_ask(rt, "lock_screen"))                         # a kind the yes didn't cover
    rt.new_turn("daemon", same_task=True)                        # the daemon went on with the same task
    assert _ask(rt, "run_command", command="rm d") == "ran rm d"
    rt.new_turn("daemon")                                          # the user said something else: a new task
    assert _asked(_ask(rt, "run_command", command="rm e"))


def test_a_yes_through_the_brain_covers_the_task_too(rt, shell_log):
    rt.set_level("task")
    token = json.loads(_ask(rt, "run_command", command="rm a"))["token"]
    rt.new_turn("daemon")
    assert _ask(rt, "confirm_action", token=token) == "ran rm a"
    assert _ask(rt, "run_command", command="rm b") == "ran rm b"


def test_changing_the_level_ends_what_a_yes_covered(rt, shell_log):
    rt.set_level("task")
    _ask(rt, "run_command", command="rm a")
    rt.new_turn("daemon")
    rt.answer("daemon", True)
    rt.set_level("task")
    assert _asked(_ask(rt, "run_command", command="rm b"))
    with pytest.raises(ValueError):
        rt.set_level("yolo")


def test_one_yes_runs_a_batch_in_order_and_a_no_drops_it(rt, shell_log):
    for command in ("rm a", "rm b"):
        assert _asked(_ask(rt, "run_command", command=command))
    assert [p.question.split("`")[1] for p in rt.batch("daemon")] == ["rm a", "rm b"]
    assert rt.answerable("daemon")
    rt.new_turn("daemon")
    assert [o.result for o in rt.answer("daemon", True)] == ["ran rm a", "ran rm b"]
    assert rt.batch("daemon") == []

    _ask(rt, "run_command", command="rm c")
    rt.new_turn("daemon")
    assert [o.ok for o in rt.answer("daemon", False)] == [False]
    assert shell_log == ["rm a", "rm b"] and rt.batch("daemon") == []


def test_a_failing_step_stops_the_rest_of_the_batch(rt, monkeypatch):
    ran = []

    def run(command, *a):
        ran.append(command)
        return "error: no such file" if command == "rm b" else f"ran {command}"

    monkeypatch.setattr(tools, "_run_command", run)
    for command in ("rm a", "rm b", "rm c"):
        _ask(rt, "run_command", command=command)
    rt.new_turn("daemon")
    outcomes = rt.answer("daemon", True)
    assert [o.ok for o in outcomes] == [True, False] and ran == ["rm a", "rm b"]
    assert rt.batch("daemon") == []                               # "rm c" was dropped: ask again


def test_confirm_action_takes_several_tokens(rt, shell_log):
    tokens = [json.loads(_ask(rt, "run_command", command=c))["token"] for c in ("rm a", "rm b")]
    assert "hasn't answered" in _ask(rt, "confirm_action", token=" ".join(tokens))
    asked = [json.loads(_ask(rt, "run_command", command=c))["token"] for c in ("rm c", "rm d")]
    rt.new_turn("daemon")
    out = _ask(rt, "confirm_action", token=", ".join(asked))
    assert "ran rm c" in out and "ran rm d" in out and shell_log == ["rm c", "rm d"]
    assert _ask(rt, "confirm_action", token="  ").startswith("error:")


def test_a_yes_is_only_a_plain_answer_to_the_last_thing_asked(rt, shell_log):
    assert not rt.answerable("daemon")
    _ask(rt, "run_command", command="rm a")
    assert rt.answerable("daemon")
    rt.new_turn("daemon")                                          # the user said something else instead
    assert not rt.answerable("daemon")
    assert rt.batch("daemon")                                      # still waiting: a button can answer it


def test_answers_never_run_in_the_turn_that_asked(rt, shell_log):
    _ask(rt, "run_command", command="rm a")
    assert "hasn't answered" in rt.answer("daemon", True)[0].result
    assert shell_log == []


def test_the_brain_cant_answer_for_the_user(rt):
    """`answer` is the daemon's: it isn't a tool, so no brain can call it."""
    assert "answer" not in tools.TOOLS and "run_confirmed" not in tools.TOOLS
    assert "error: no tool" in _ask(rt, "answer", yes=True)


def test_a_batch_answer_is_checked_against_each_target(rt, monkeypatch):
    calc = {"address": "0x1", "class": "omacalc", "title": "Calc", "workspace": {"name": "1"}}
    monkeypatch.setattr(desktop, "find_window", lambda q: calc)
    monkeypatch.setattr(desktop, "dispatch", lambda lua: None)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "windows", lambda: [])          # it closed on its own
    _ask(rt, "close_window", window="calc")
    rt.new_turn("daemon")
    out = rt.answer("daemon", True)
    assert not out[0].ok and "already closed" in out[0].result and out[0].say is None


def test_a_close_is_only_announced_when_the_window_really_closed(rt, monkeypatch):
    calc = {"address": "0x1", "class": "omacalc", "title": "Calc", "workspace": {"name": "1"}}
    monkeypatch.setattr(desktop, "find_window", lambda q: calc)
    closes = []
    open_windows = [calc]
    monkeypatch.setattr(desktop, "dispatch", lambda lua: open_windows.clear() if closes else None)
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "windows", lambda: list(open_windows))
    _ask(rt, "close_window", window="calc")
    rt.new_turn("daemon")
    shown_a_dialog = rt.answer("daemon", True)[0]                 # the app answers with its own dialog: it stays open
    assert shown_a_dialog.ok and "still open" in shown_a_dialog.result and shown_a_dialog.say is None
    _ask(rt, "close_window", window="calc")
    rt.new_turn("daemon")
    closes.append(1)                                              # now it really closes
    assert rt.answer("daemon", True)[0].say == "Closed omacalc."


def test_stop_prevents_the_rest_of_a_batch(rt, monkeypatch):
    ran = []

    def run(command, *a):
        ran.append(command)
        rt.cancel("daemon")                               # the user pressed Stop during the first
        return f"ran {command}"

    monkeypatch.setattr(tools, "_run_command", run)
    for command in ("rm a", "rm b"):
        _ask(rt, "run_command", command=command)
    rt.new_turn("daemon")
    outcomes = rt.answer("daemon", True)
    assert ran == ["rm a"] and [o.ok for o in outcomes] == [True, False]
    assert "cancelled" in outcomes[1].result and rt.batch("daemon") == []


def test_a_command_that_exits_with_an_error_is_a_failed_step(rt, monkeypatch):
    ran = []

    def run(command, *a):
        ran.append(command)
        return json.dumps({"status": "finished", "exit_code": 1 if command == "rm a" else 0, "output_tail": ""})

    monkeypatch.setattr(tools, "_run_command", run)
    rt.set_level("task")
    for command in ("rm a", "rm b"):
        _ask(rt, "run_command", command=command)
    rt.new_turn("daemon")
    outcomes = rt.answer("daemon", True)
    assert ran == ["rm a"] and [o.ok for o in outcomes] == [False]
    assert rt.confirmations.granted("daemon") == frozenset()      # a failed step covers nothing
    assert tools._failed('{"exit_code": 0}') is False and tools._failed("plain text") is False
    assert tools._failed('{"status": "running"}') is False


def test_a_yes_still_running_cant_grant_after_the_task_ended(rt, monkeypatch):
    def run(command, *a):
        rt.set_level("task")                              # the level changed while it ran: a new task
        return f"ran {command}"

    monkeypatch.setattr(tools, "_run_command", run)
    rt.set_level("task")
    _ask(rt, "run_command", command="rm a")
    rt.new_turn("daemon")
    rt.answer("daemon", True)
    assert rt.confirmations.granted("daemon") == frozenset()


def test_only_the_questions_the_user_was_shown_are_answered(rt, shell_log):
    first = json.loads(_ask(rt, "run_command", command="rm a"))["token"]
    _ask(rt, "run_command", command="rm b")                       # joined the batch after the user looked at it
    rt.new_turn("daemon")
    out = rt.answer("daemon", True, {first})
    assert [o.result for o in out] == ["ran rm a"] and shell_log == ["rm a"]
    assert [p.question.split("`")[1] for p in rt.batch("daemon")] == ["rm b"]      # still waiting, not approved
    assert rt.answer("daemon", True, {first}) == []


def test_one_answer_covers_at_most_a_few_questions(rt, shell_log):
    for i in range(6):
        assert _asked(_ask(rt, "run_command", command=f"rm {i}"))
    seventh = _ask(rt, "run_command", command="rm 6")
    assert seventh.startswith("error:") and "6 questions are already waiting" in seventh
    assert len(rt.batch("daemon")) == 6


def test_a_fresh_conversation_drops_questions_and_grants(rt, shell_log):
    rt.set_level("task")
    _ask(rt, "run_command", command="rm a")
    rt.new_turn("daemon")
    rt.answer("daemon", True)
    token = json.loads(_ask(rt, "run_command", command="sudo ls"))["token"]
    rt.forget("daemon")
    assert rt.batch("daemon") == [] and rt.confirmations.granted("daemon") == frozenset()
    rt.new_turn("daemon")
    assert "unknown" in _ask(rt, "confirm_action", token=token)
