"""What Auren shows on screen when the mascot isn't there (notice.py), and when it shows it."""

import json

import pytest

from auren import daemon, notice
from auren.live import NO_NOTICE, notice_kind
from auren.tools import TOOLS


def test_a_status_gets_its_own_icon_never_the_volume_one(_no_notices):
    notice.show("Clicking “mouth”", "click")
    cmd = _no_notices[-1]
    assert cmd[0] == "omarchy-osd" and cmd[cmd.index("-i") + 1] == notice.ICONS["click"]
    notice.show("Doing something", "no-such-kind")
    assert _no_notices[-1][_no_notices[-1].index("-i") + 1] == notice.ICONS["auren"]
    assert all(len(g) == 1 and 0xF0000 <= ord(g) <= 0xF1AF0 for g in notice.ICONS.values())   # Nerd Font glyphs


def test_replies_and_errors_are_notifications_to_read(_no_notices):
    notice.notify("Auren", "It's 5 pm and your battery is at 55%.")
    notice.notify("Auren couldn't hear you", "no microphone", "error")
    reply, error = _no_notices
    assert reply[0] == "omarchy-notification-send" and reply[-1] == "It's 5 pm and your battery is at 55%."
    assert notice.ICONS["error"] in error and "normal" in error


@pytest.mark.parametrize("prefs, place, shown", [({}, {}, False), ({"bubble": False}, {}, True),
                                                 ({}, {"hidden": True}, True)])
def test_nothing_extra_while_the_mascot_shows_it(tmp_path, monkeypatch, _no_notices, prefs, place, shown):
    monkeypatch.setattr(notice, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(notice, "STATE_DIR", tmp_path / "state")
    for d, data in ((tmp_path / "config", prefs), (tmp_path / "state", place)):
        d.mkdir()
        (d / "mascot.json").write_text(json.dumps(data))
    monkeypatch.setattr(notice, "_mascot_running", lambda: True)
    notice.show("Thinking…", "thinking")
    notice.notify("Auren", "hello")
    assert len(_no_notices) == (2 if shown else 0)


def test_an_action_shows_even_while_the_bubble_is_up(tmp_path, monkeypatch, _no_notices):
    monkeypatch.setattr(notice, "CONFIG_DIR", tmp_path / "config")      # no mascot.json: the bubble is on
    monkeypatch.setattr(notice, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(notice, "_mascot_running", lambda: True)
    notice.show("Thinking…", "thinking")                     # the bubble has it
    assert _no_notices == []
    notice.show("Clicking", "click", always=True)            # an action: the OSD too
    assert len(_no_notices) == 1 and _no_notices[0][0] == "omarchy-osd"


def test_auren_closes_its_own_note_before_a_screenshot(monkeypatch):
    closed = []
    monkeypatch.setattr(notice.subprocess, "run", lambda cmd, **k: closed.append(cmd))
    monkeypatch.setattr(notice.time, "sleep", lambda s: None)
    notice.clear()
    assert closed == []                                    # nothing of Auren's open: leave the OSD alone
    notice.show("Clicking", "click")
    notice.clear()
    assert closed == [["omarchy-shell", "-q", "osd", "close"]]


def test_which_actions_get_a_notice():
    assert notice_kind("click_at") == "click" and notice_kind("type_text") == "keyboard"
    assert notice_kind("drag_at") == "mouse" and notice_kind("launch_app") == "auren"
    assert notice_kind("look_at_screen") is None            # it would be in its own screenshot
    assert notice_kind("system_info") is None and notice_kind("browser_read") is None    # quick reads
    assert notice_kind("volume") is None                    # Omarchy shows its own volume OSD
    assert notice_kind("no_such_tool") is None
    assert NO_NOTICE <= set(TOOLS)                           # no stale names


def test_the_daemon_never_calls_the_osd_without_an_icon():
    import inspect

    from auren import tools
    for module in (daemon, tools):
        assert "omarchy-osd" not in inspect.getsource(module)
