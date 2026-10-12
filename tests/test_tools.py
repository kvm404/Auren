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
