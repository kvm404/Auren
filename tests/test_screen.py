import pytest

from auren.hands import screen


def test_image_to_layout_coordinates(monkeypatch):
    # 1920x1080 panel at 125 % scale = 1536x864 layout; screenshot 1024x576
    monkeypatch.setattr(screen, "_last_view", {"x": 0, "y": 0, "lw": 1536, "lh": 864, "iw": 1024, "ih": 576,
                                               "at": screen.time.time()})
    assert screen._to_layout(512, 345) == (768, 518)       # the button the brain clicked in the live test
    assert screen._to_layout(0, 0) == (0, 0)
    with pytest.raises(screen.ScreenError, match="outside"):
        screen._to_layout(2000, 10)


def test_second_monitor_offset(monkeypatch):
    monkeypatch.setattr(screen, "_last_view", {"x": 1536, "y": 0, "lw": 2560, "lh": 1440, "iw": 1024, "ih": 576,
                                               "at": screen.time.time()})
    assert screen._to_layout(512, 288) == (1536 + 1280, 720)


def test_must_look_first(monkeypatch):
    monkeypatch.setattr(screen, "_last_view", None)
    with pytest.raises(screen.ScreenError, match="look at the screen first"):
        screen._to_layout(10, 10)


def test_typing_goes_back_to_the_clicked_window(monkeypatch):
    calls = []
    monkeypatch.setattr(screen, "_sh", lambda cmd, timeout=10: calls.append(cmd) or "")
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "_clicked", {"address": "0xabc", "at": screen.time.time()})
    monkeypatch.setattr(screen, "_active_window", lambda: "0xother")      # focus followed the mouse away
    screen.type_text("hi")
    assert 'address:0xabc' in calls[0][-1] and calls[1] == ["wtype", "--", "hi"]
    calls.clear()
    monkeypatch.setattr(screen, "_active_window", lambda: "0xabc")        # still focused: no refocus
    screen.press_keys("Enter")
    assert len(calls) == 1 and calls[0][0] == "wtype"


def test_drag_rejects_unknown_hold_keys():
    with pytest.raises(screen.ScreenError, match="can only hold"):
        screen.drag(1, 2, 3, 4, hold="shift+banana")
