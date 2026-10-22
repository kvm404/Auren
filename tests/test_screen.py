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


class _Mouse:
    """The virtual mouse: buttons pressed, and relative motion that moves the cursor."""

    def __init__(self, at):
        self.at, self.buttons = at, []

    def write(self, kind, code, value):
        from evdev import ecodes as e
        if kind == e.EV_KEY:
            self.buttons.append((code, value))
        elif kind == e.EV_REL and code in (e.REL_X, e.REL_Y):
            self.at[code == e.REL_Y] += value

    def syn(self):
        pass


def _mouse_env(monkeypatch):
    at = [0, 0]
    mouse = _Mouse(at)
    monkeypatch.setattr(screen, "_device", lambda: mouse)
    monkeypatch.setattr(screen, "_cursor", lambda: tuple(at))
    monkeypatch.setattr(screen, "_move", lambda x, y: at.__setitem__(slice(None), [x, y]))
    monkeypatch.setattr(screen, "_active_window", lambda: "0xabc")
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "_last_view", {"x": 0, "y": 0, "lw": 1920, "lh": 1080, "iw": 1024, "ih": 576,
                                               "at": screen.time.time()})
    return mouse


def test_click_and_drag_report_screenshot_pixels(monkeypatch):
    # every click_at clicked, then failed with "NameError: name 'x' is not defined", so the brain said its
    # clicks were broken and clicked again
    mouse = _mouse_env(monkeypatch)
    assert screen.click(512, 288) == "left-clicked at (512, 288) in the screenshot"
    assert mouse.at == [960, 540] and len(mouse.buttons) == 2
    assert screen.click(100, 50, double=True) == "double-left-clicked at (100, 50) in the screenshot"
    assert screen.drag(10, 10, 100, 50) == "dragged from (10, 10) to (100, 50) in the screenshot"


def test_layout_clicks_and_drags_work_without_a_screenshot(monkeypatch):
    # Excalidraw focuses its canvas and pans with these, in screen coordinates
    mouse = _mouse_env(monkeypatch)
    monkeypatch.setattr(screen, "_last_view", None)
    screen.click_layout(700, 400)
    screen.drag_layout(700, 400, 900, 500, button="middle")
    assert mouse.at == [900, 500] and len(mouse.buttons) == 4


def test_drag_rejects_unknown_hold_keys():
    with pytest.raises(screen.ScreenError, match="can only hold"):
        screen.drag(1, 2, 3, 4, hold="shift+banana")


class _Clipboard:
    """wl-copy / wl-paste stand-ins: one clipboard, a log of what was pasted and whether each copy was sensitive."""

    def __init__(self, *types, data=b""):
        self.types, self.data, self.pasted, self.sensitive = list(types), data, [], []

    def run(self, cmd, input=None, **kw):
        out = b""
        if cmd[:2] == ["wl-paste", "--list-types"]:
            out = "\n".join(self.types)
        elif cmd[0] == "wl-paste":
            asked = cmd[cmd.index("--type") + 1] if "--type" in cmd else self.types[0] if self.types else None
            out = self.data if asked in self.types else b""
        elif cmd == ["wl-copy", "--clear"]:
            self.types, self.data = [], b""
        elif cmd[0] == "wl-copy":
            self.types = [cmd[cmd.index("--type") + 1] if "--type" in cmd else "text/plain;charset=utf-8"]
            self.data = input
            self.sensitive.append("--sensitive" in cmd)
        return screen.subprocess.CompletedProcess(cmd, 0, out, "")


def _paste_env(monkeypatch, board, window="chrome-discord.com__channels_@me-Default", meanwhile=lambda: None):
    keys = []

    def sh(cmd, timeout=10):
        if cmd[:2] == ["hyprctl", "activewindow"]:
            return screen.json.dumps({"class": window, "address": "0xabc"})
        if cmd[0] == "wtype":
            keys.append(cmd)
            if cmd[1] == "-M":
                board.pasted.append(board.data.decode())
                meanwhile()
        return ""
    monkeypatch.setattr(screen, "_sh", sh)
    monkeypatch.setattr(screen.subprocess, "run", board.run)
    monkeypatch.setattr(screen.time, "sleep", lambda s: None)
    monkeypatch.setattr(screen, "_clicked", None)
    return keys


def test_text_is_pasted_in_one_go_and_the_clipboard_put_back(monkeypatch):
    board = _Clipboard("text/plain;charset=utf-8", "text/plain", data=b"what the user had copied")
    keys = _paste_env(monkeypatch, board)
    text = "This is from Auren.\nThe permissions are updated."
    assert screen.type_text(text) == f"pasted {len(text)} characters"
    assert board.pasted == [text]                                         # whole, newline included: no Enter key
    assert keys == [["wtype", "-M", "ctrl", "-k", "v", "-m", "ctrl"]]
    assert board.data == b"what the user had copied"
    assert board.sensitive == [True, False]          # a clipboard history skips what Auren typed, keeps theirs


def test_an_empty_clipboard_is_left_empty(monkeypatch):
    board = _Clipboard()
    _paste_env(monkeypatch, board)
    screen.type_text("hi")
    assert board.pasted == ["hi"] and board.types == []


def test_copied_files_are_put_back_as_files(monkeypatch):
    # a clipboard of only text/uri-list used to be taken for an empty one, and cleared
    board = _Clipboard("text/uri-list", data=b"file:///home/u/report.pdf")
    _paste_env(monkeypatch, board)
    screen.type_text("hi")
    assert board.types == ["text/uri-list"] and board.data == b"file:///home/u/report.pdf"


def test_what_the_user_copies_meanwhile_stays(monkeypatch):
    board = _Clipboard("text/plain", data=b"old")

    def copy_theirs():
        board.types, board.data = ["text/plain"], b"copied during the paste"
    _paste_env(monkeypatch, board, meanwhile=copy_theirs)
    screen.type_text("hi")
    assert board.data == b"copied during the paste"


def test_terminals_are_typed_into(monkeypatch):
    for window in ("Alacritty", "com.mitchellh.ghostty", "kitty"):
        board = _Clipboard("text/plain", data=b"x")
        keys = _paste_env(monkeypatch, board, window=window)
        assert screen.type_text("ls") == "typed 2 characters"
        assert keys == [["wtype", "--", "ls"]] and board.pasted == []


def test_a_web_app_named_like_a_terminal_is_pasted_into(monkeypatch):
    board = _Clipboard()
    _paste_env(monkeypatch, board, window="chrome-footlocker.com__-Default")
    assert screen.type_text("hi") == "pasted 2 characters"


def test_tools_report_screenshot_pixels_not_layout(monkeypatch):
    # the brain saw "clicked at screen (1594, 506)" next to a 1024-wide image and thought clicks were 1.87x off
    monkeypatch.setattr(screen, "_last_view", {"x": 0, "y": 0, "lw": 1920, "lh": 1080, "iw": 1024, "ih": 576,
                                               "at": screen.time.time()})
    assert screen._to_image(*screen._to_layout(850, 270)) == (850, 270)
    monkeypatch.setattr(screen, "_last_view", {"x": 1920, "y": 0, "lw": 1536, "lh": 960, "iw": 1024, "ih": 640,
                                               "at": screen.time.time()})
    assert screen._to_image(*screen._to_layout(100, 600)) == (100, 600)
