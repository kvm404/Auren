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
    monkeypatch.setattr(screen, "_sh", lambda cmd, timeout=10: calls.append(cmd) or '{"class": "foot"}')
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "_clicked", {"address": "0xabc", "at": screen.time.time()})
    monkeypatch.setattr(screen, "_active_window", lambda: "0xother")      # focus followed the mouse away
    screen.type_text("hi")                                                # a terminal: typed, not pasted
    assert 'address:0xabc' in calls[0][-1] and calls[-1] == ["wtype", "--", "hi"]
    calls.clear()
    monkeypatch.setattr(screen, "_active_window", lambda: "0xabc")        # still focused: no refocus
    screen.press_keys("Enter")
    assert len(calls) == 1 and calls[0][0] == "wtype"


def test_drag_rejects_unknown_hold_keys():
    with pytest.raises(screen.ScreenError, match="can only hold"):
        screen.drag(1, 2, 3, 4, hold="shift+banana")


class _Clipboard:
    """wl-copy / wl-paste stand-ins: one clipboard, a log of what was pasted."""

    def __init__(self, content=None):
        self.content, self.pasted, self.cleared = content, [], False

    def run(self, cmd, input=None, **kw):
        out = b"" if "text" not in kw else ""
        if cmd[:2] == ["wl-paste", "--list-types"]:
            out = "" if self.content is None else f"{self.content[0]}\ntext/plain\n"
        elif cmd[0] == "wl-paste":
            out = self.content[1]
        elif cmd == ["wl-copy", "--clear"]:
            self.content, self.cleared = None, True
        elif cmd[0] == "wl-copy":
            self.content = (cmd[cmd.index("--type") + 1], input)
        return screen.subprocess.CompletedProcess(cmd, 0, out, "")


def _paste_env(monkeypatch, board, window="chrome-discord.com__channels_@me-Default"):
    keys = []

    def sh(cmd, timeout=10):
        if cmd[:2] == ["hyprctl", "activewindow"]:
            return screen.json.dumps({"class": window, "address": "0xabc"})
        if cmd[0] == "wtype":
            keys.append(cmd)
            if cmd[1] == "-M":
                board.pasted.append(board.content[1].decode())
        return ""
    monkeypatch.setattr(screen, "_sh", sh)
    monkeypatch.setattr(screen.subprocess, "run", board.run)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "_clicked", None)
    return keys


def test_text_is_pasted_in_one_go_and_the_clipboard_put_back(monkeypatch):
    board = _Clipboard(("text/plain;charset=utf-8", b"what the user had copied"))
    keys = _paste_env(monkeypatch, board)
    text = "This is from Auren.\nThe permissions are updated."
    assert screen.type_text(text) == f"pasted {len(text)} characters"
    assert board.pasted == [text]                                         # whole, newline included: no Enter key
    assert keys == [["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]]
    assert board.content == ("text/plain;charset=utf-8", b"what the user had copied")


def test_an_empty_clipboard_is_left_empty(monkeypatch):
    board = _Clipboard()
    _paste_env(monkeypatch, board)
    screen.type_text("hi")
    assert board.pasted == ["hi"] and board.cleared and board.content is None


def test_terminals_are_typed_into(monkeypatch):
    board = _Clipboard(("text/plain", b"x"))
    keys = _paste_env(monkeypatch, board, window="Alacritty")
    assert screen.type_text("ls") == "typed 2 characters"
    assert keys == [["wtype", "--", "ls"]] and board.pasted == []

