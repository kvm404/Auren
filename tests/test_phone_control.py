"""Phone control: the phone tools, their safety questions, and the round trip through the phone server."""

import asyncio
import json

import pytest

from auren import daemon, phone_server, tools
from auren.hands import android

SESSION = daemon.SESSION

SCREEN = {
    "ok": True,
    "screen": "WhatsApp\n[1] Search (field, search)\n[2] Mom\n[3] Message (field)\n[4] Send",
    "items": [{"n": 1, "label": "Search", "field": True, "search": True}, {"n": 2, "label": "Mom"},
              {"n": 3, "label": "Message", "field": True}, {"n": 4, "label": "Send"}],
}


class Phone:
    """A stand-in phone: records each action and answers with SCREEN."""

    def __init__(self):
        self.sent: list[dict] = []

    def __call__(self, action: dict, timeout: float) -> dict:
        self.sent.append(action)
        return SCREEN


@pytest.fixture
def ph():
    p = Phone()
    android.attach(p)
    android._items.clear()
    yield p
    android.attach(None)
    android._items.clear()


@pytest.fixture
def rt(tmp_path):
    r = tools.Runtime(tmp_path / "tools.sock")
    r.new_turn(SESSION)
    return r


def call(rt, name, args=None) -> str:
    return rt.call(SESSION, name, args or {})[0]


def test_without_the_daemon_the_tools_say_so(rt):
    android.attach(None)
    assert call(rt, "phone_screen").startswith("error: phone control isn't available")


def test_the_screen_is_untrusted_and_its_items_can_be_named_by_words(rt, ph):
    out = call(rt, "phone_screen")
    assert out.startswith("<untrusted") and "[2] Mom" in out
    call(rt, "phone_tap", {"target": "mom"})
    assert ph.sent[-1] == {"action": "tap", "n": 2, "long": False, "expect": "Mom"}
    assert call(rt, "phone_tap", {"target": "Dad"}).startswith("error: nothing called 'Dad'")


def test_words_name_a_text_field_before_other_items_that_mention_them():
    android._items.clear()
    android._items.update({"8": {"label": "Photo · reaction on previous message", "field": False, "search": False},
                           "12": {"label": "Message box", "field": True, "search": False}})
    assert android.item("message")[0] == "12"
    android._items.clear()


def test_a_risky_tap_asks_first_and_checks_the_item_is_still_there(rt, ph):
    rt.set_level("ask")
    call(rt, "phone_screen")
    asked = json.loads(call(rt, "phone_tap", {"target": "4"}))
    assert asked["status"] == "needs_confirmation" and "Send" in asked["ask_user"]
    assert [a["action"] for a in ph.sent] == ["screen"]          # nothing tapped yet


def test_full_access_taps_without_asking(rt, ph):
    rt.set_level("never")
    call(rt, "phone_screen")
    assert call(rt, "phone_tap", {"target": "Send"}).startswith("<untrusted")
    assert ph.sent[-1]["action"] == "tap" and ph.sent[-1]["expect"] == "Send"


def test_submitting_a_message_asks_but_a_search_runs(rt, ph):
    rt.set_level("ask")
    call(rt, "phone_screen")
    assert call(rt, "phone_type", {"target": "1", "text": "mom", "submit": True}).startswith("<untrusted")
    assert ph.sent[-1] == {"action": "type", "n": 1, "text": "mom", "submit": True, "expect": "Search"}
    asked = json.loads(call(rt, "phone_type", {"target": "3", "text": "hi", "submit": True}))
    assert asked["status"] == "needs_confirmation" and ph.sent[-1]["n"] == 1
    assert call(rt, "phone_type", {"target": "2", "text": "x"}).startswith("error: item 2 (Mom) isn't a text field")


def test_keys_and_scrolls_are_checked_before_they_go(rt, ph):
    assert call(rt, "phone_key", {"key": "power"}).startswith("error: no key 'power'")
    call(rt, "phone_key", {"key": "Quick settings"})
    assert ph.sent[-1] == {"action": "key", "key": "quick_settings"}
    assert call(rt, "phone_scroll", {"direction": "sideways"}).startswith("error: direction")


def test_an_error_from_the_phone_reaches_the_brain(rt):
    android.attach(lambda a, t: {"ok": False, "error": "no app called 'Foo': close matches: Food"})
    assert call(rt, "phone_open_app", {"app": "Foo"}) == "error: no app called 'Foo': close matches: Food"
    android.attach(None)


# --- the round trip through the phone server ---------------------------------------------------------

class _Daemon:
    def _origin(self):
        return None


def _conn(dev_id: str) -> phone_server.Conn:
    return phone_server.Conn(None, {"id": dev_id, "name": dev_id})


def test_an_action_goes_to_the_phone_with_control_on_and_its_result_comes_back():
    async def go():
        hub = phone_server.Hub(_Daemon())
        off, on = _conn("a"), _conn("b")
        hub.conns |= {off, on}
        await hub.request(on, {"type": "control.ready", "on": True})
        task = asyncio.create_task(hub.control({"action": "screen"}, 5))
        msg = await asyncio.wait_for(on.out.get(), 1)
        assert off.out.empty() and msg["type"] == "control" and msg["action"] == "screen"
        await hub.request(on, {"type": "control.result", "id": msg["id"], "ok": True, "screen": "Home"})
        return await task

    assert (asyncio.run(go()))["screen"] == "Home"


def test_without_a_phone_that_can_the_action_says_why():
    async def go():
        hub = phone_server.Hub(_Daemon())
        none = await hub.control({"action": "screen"}, 1)
        hub.conns.add(_conn("a"))
        off = await hub.control({"action": "screen"}, 1)
        c = _conn("b")
        hub.conns.add(c)
        await hub.request(c, {"type": "control.ready", "on": True})
        silent = await hub.control({"action": "screen"}, 0.05)
        return none, off, silent

    none, off, silent = asyncio.run(go())
    assert "isn't connected" in none["error"] and "Phone control is off" in off["error"].replace("p", "P", 1)
    assert silent == {"ok": False, "error": "the phone didn't answer in 0 s"}
