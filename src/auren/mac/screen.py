"""Screen control on macOS: what hands/screen.py does with grim, uinput and wtype, done with
`screencapture` and Quartz events.

- look(): the display with the front window (else the main one), captured with `screencapture -R`
  in points and scaled to IMAGE_WIDTH, with the same grid. Coordinates map back to global points
  (top left of the main display), which is what Quartz events take.
- the mouse: Quartz mouse events posted at absolute points. Drags send dragged events along the path,
  so there is no acceleration to correct for.
- keys: Quartz key events by virtual key code (the US layout's positions). Shortcuts use Cmd where Linux
  uses Ctrl: press_keys sends exactly what it's given, and the brain is told to use cmd.
- typing: pasted (Cmd+V) and the clipboard put back, as on Linux; terminals get the text typed as Unicode
  key events instead, with Return for a line break.

Needs the Accessibility permission (posting events) and Screen Recording (seeing other apps' windows).
"""

from __future__ import annotations

import io
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .. import notice
from ..hands import clipboard
from ..hands.screen import GRID, IMAGE_WIDTH, ScreenError, _to_image, _to_layout, _with_grid
from . import windows as macwin

__all__ = ["look", "snapshot", "_cursor", "_move", "active_window", "_active_window", "window", "window_at",
           "_refocus", "focus", "hover", "click", "click_layout", "drag", "drag_layout", "scroll", "TERMINALS",
           "press_code", "type_text", "press_keys", "MOD", "_HOLD"]

MOD = "cmd"          # the shortcut modifier: Cmd+C, Cmd+V, Cmd+A

# Key codes (kVK_*, positions on the US layout)
KEYS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13,
    "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25,
    "7": 26, "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "`": 50,
    "enter": 36, "return": 36, "tab": 48, "space": 49, "backspace": 51, "esc": 53, "escape": 53,
    "delete": 117, "forwarddelete": 117, "home": 115, "end": 119, "pageup": 116, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109,
    "f11": 103, "f12": 111,
}
_HOLD = {"shift": "shift", "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "option": "alt", "opt": "alt",
         "cmd": "cmd", "command": "cmd", "super": "cmd", "win": "cmd", "meta": "cmd"}
_MOD_KEYS = {"cmd": 55, "shift": 56, "alt": 58, "ctrl": 59}

TERMINALS = frozenset({"terminal", "iterm2", "iterm", "ghostty", "alacritty", "kitty", "wezterm", "wezterm-gui",
                       "warp", "hyper", "tabby", "rio"})

_clicked: dict | None = None
_last_view: dict | None = None


def _flag(mod: str) -> int:
    import Quartz as Q
    return {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift, "alt": Q.kCGEventFlagMaskAlternate,
            "ctrl": Q.kCGEventFlagMaskControl}[mod]


def _post(ev) -> None:
    import Quartz as Q
    Q.CGEventPost(Q.kCGHIDEventTap, ev)


def _need_access() -> None:
    if not macwin.trusted():
        raise ScreenError("Auren isn't allowed to control the Mac: turn it on under System Settings → Privacy & "
                          "Security → Accessibility (auren doctor names what to add)")


# --- seeing -------------------------------------------------------------------------------------------

def _display() -> dict:
    w = macwin.active_window()
    if w:
        return macwin.display_at(w["at"][0] + w["size"][0] / 2, w["at"][1] + w["size"][1] / 2)
    return macwin.main_display()


def _capture(d: dict, path: str) -> None:
    try:
        p = subprocess.run(["screencapture", "-x", "-t", "png", f"-R{d['x']},{d['y']},{d['width']},{d['height']}", path],
                           capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as e:
        raise ScreenError(f"screencapture failed: {e}") from None
    if p.returncode != 0 or not Path(path).stat().st_size:
        raise ScreenError(f"screencapture failed: {(p.stderr or p.stdout).strip()[:200]} (is Screen Recording "
                          "allowed for Auren?)")


def _scaled(path: str) -> Path:
    """The capture resized to IMAGE_WIDTH wide (a Retina capture is twice the points)."""
    from PIL import Image as PILImage
    img = PILImage.open(path).convert("RGB")
    w, h = img.size
    img = img.resize((IMAGE_WIDTH, round(h * IMAGE_WIDTH / w)))
    img.save(path, "PNG")
    return Path(path)


def look() -> tuple[bytes, str]:
    global _last_view
    from ..hands import screen as base
    notice.clear()
    d = _display()
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        _capture(d, f.name)
        data, iw, ih = _with_grid(_scaled(f.name))
    _last_view = {"x": d["x"], "y": d["y"], "lw": d["width"], "lh": d["height"], "iw": iw, "ih": ih, "at": time.time()}
    base._last_view = _last_view           # _to_layout and view_id read it there
    note = (f"Screenshot of the display, {iw}x{ih} pixels, with a coordinate grid: thin lines every "
            f"{GRID} pixels, numbered along the top (x) and left (y) edges. Read the target's position from those "
            "numbers (don't estimate pixels from the image size) and pass it to click_at.")
    return data, note


def snapshot(window: str) -> bytes:
    from PIL import Image as PILImage
    w = macwin.by_address(window)
    if w is None:
        raise ScreenError("that window is gone")
    if _active_window() != window:
        macwin.focus(w)
        time.sleep(0.15)
    if _active_window() != window:
        raise ScreenError("that window can't be brought to the front")
    d = macwin.display_at(w["at"][0] + w["size"][0] / 2, w["at"][1] + w["size"][1] / 2)
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        _capture(d, f.name)
        buf = io.BytesIO()
        PILImage.open(_scaled(f.name)).convert("RGB").save(buf, "JPEG", quality=80)
    return buf.getvalue()


# --- windows ------------------------------------------------------------------------------------------

def active_window() -> str:
    return _active_window()


def _active_window() -> str:
    try:
        w = macwin.active_window()
    except Exception:  # noqa: BLE001 - the window server can't be asked: nothing is known to be in front
        return ""
    return w["address"] if w else ""


def window(address: str) -> dict:
    try:
        w = macwin.by_address(address)
    except Exception:  # noqa: BLE001
        return {}
    return {"class": w.get("class") or "", "title": w.get("title") or ""} if w else {}


def window_at(x: float, y: float) -> str:
    try:
        lx, ly = _to_layout(x, y)
        w = macwin.at(lx, ly)
    except (ScreenError, Exception):  # noqa: BLE001
        return ""
    return w["address"] if w else ""


def _key_target() -> str:
    if _clicked and time.time() - _clicked["at"] <= 120 and _clicked["address"]:
        return _clicked["address"]
    return _active_window()


def _refocus() -> None:
    target = _key_target()
    if target and _active_window() != target:
        w = macwin.by_address(target)
        if w:
            macwin.focus(w)
            time.sleep(0.1)


def focus(address: str) -> None:
    global _clicked
    from ..hands import screen as base
    w = macwin.by_address(address)
    if w is None:
        raise ScreenError("that window is gone")
    macwin.focus(w)
    _clicked = base._clicked = {"address": address, "at": time.time()}
    time.sleep(0.15)


def _note_click() -> None:
    global _clicked
    from ..hands import screen as base
    _clicked = base._clicked = {"address": _active_window(), "at": time.time()}


# --- the mouse ----------------------------------------------------------------------------------------

_BUTTONS = {"left": 0, "right": 1, "middle": 2}


def _cursor() -> tuple[int, int]:
    import Quartz as Q
    p = Q.CGEventGetLocation(Q.CGEventCreate(None))
    return round(p.x), round(p.y)


def _mouse(kind: str, x: float, y: float, button: str = "left", clicks: int = 1, flags: int = 0) -> None:
    import Quartz as Q
    b = _BUTTONS[button]
    types = {
        ("down", 0): Q.kCGEventLeftMouseDown, ("up", 0): Q.kCGEventLeftMouseUp, ("drag", 0): Q.kCGEventLeftMouseDragged,
        ("down", 1): Q.kCGEventRightMouseDown, ("up", 1): Q.kCGEventRightMouseUp, ("drag", 1): Q.kCGEventRightMouseDragged,
        ("down", 2): Q.kCGEventOtherMouseDown, ("up", 2): Q.kCGEventOtherMouseUp, ("drag", 2): Q.kCGEventOtherMouseDragged,
    }
    t = Q.kCGEventMouseMoved if kind == "move" else types[(kind, b)]
    ev = Q.CGEventCreateMouseEvent(None, t, Q.CGPointMake(x, y), b)
    if kind in ("down", "up"):
        Q.CGEventSetIntegerValueField(ev, Q.kCGMouseEventClickState, clicks)
    if flags:
        Q.CGEventSetFlags(ev, flags)
    _post(ev)


def _move(x: int, y: int) -> None:
    _need_access()
    _mouse("move", x, y)


def hover(lx: int, ly: int) -> None:
    _move(round(lx), round(ly))
    time.sleep(0.12)


def click(x: float, y: float, button: str = "left", double: bool = False, restore: bool = False) -> str:
    lx, ly = _to_layout(x, y)
    click_layout(lx, ly, button, double, restore)
    return f"{'double-' if double else ''}{button}-clicked at ({round(x)}, {round(y)}) in the screenshot"


def click_layout(lx: int, ly: int, button: str = "left", double: bool = False, restore: bool = False) -> None:
    if button not in _BUTTONS:
        raise ScreenError("button must be left, right or middle")
    _need_access()
    before = _cursor()
    lx, ly = round(lx), round(ly)
    _mouse("move", lx, ly)
    time.sleep(0.12)
    for n in range(1, 3 if double else 2):
        _mouse("down", lx, ly, button, n)
        time.sleep(0.05)
        _mouse("up", lx, ly, button, n)
        time.sleep(0.08)
    time.sleep(0.1)
    _note_click()
    if restore:
        time.sleep(0.25)
        _mouse("move", *before)


def _hold(hold: str) -> list[str]:
    keys = [p.strip().lower() for p in hold.split("+") if p.strip()]
    unknown = [k for k in keys if k not in _HOLD]
    if unknown:
        raise ScreenError(f"can only hold shift, ctrl, alt/option or cmd, not {', '.join(unknown)}")
    return [_HOLD[k] for k in keys]


def drag(x1: float, y1: float, x2: float, y2: float, hold: str = "", button: str = "left",
         duration: float = 0.5) -> str:
    _hold(hold)
    lx1, ly1 = _to_layout(x1, y1)
    lx2, ly2 = _to_layout(x2, y2)
    end = _to_image(*drag_layout(lx1, ly1, lx2, ly2, hold, button, duration))
    held = f" holding {hold}" if hold.strip() else ""
    return f"dragged from ({round(x1)}, {round(y1)}) to ({end[0]}, {end[1]}) in the screenshot{held}"


def drag_layout(lx1: int, ly1: int, lx2: int, ly2: int, hold: str = "", button: str = "left",
                duration: float = 0.5) -> tuple[int, int]:
    if button not in _BUTTONS:
        raise ScreenError("button must be left, right or middle")
    mods = _hold(hold)
    _need_access()
    flags = 0
    for m in mods:
        flags |= _flag(m)
    _mouse("move", lx1, ly1)
    time.sleep(0.12)
    try:
        for m in mods:
            _key(_MOD_KEYS[m], True, flags)
        _mouse("down", lx1, ly1, button, 1, flags)
        time.sleep(0.08)
        steps = max(8, min(60, int((abs(lx2 - lx1) + abs(ly2 - ly1)) / 12)))
        for i in range(1, steps + 1):
            _mouse("drag", lx1 + (lx2 - lx1) * i / steps, ly1 + (ly2 - ly1) * i / steps, button, 1, flags)
            time.sleep(max(0.004, duration / steps))
        time.sleep(0.08)
    finally:                               # never leave a button or a modifier held down
        _mouse("up", lx2, ly2, button, 1, flags)
        for m in reversed(mods):
            _key(_MOD_KEYS[m], False, 0)
    time.sleep(0.1)
    _note_click()
    return _cursor()


def scroll(x: float, y: float, direction: str = "down", amount: int = 3) -> str:
    import Quartz as Q
    lx, ly = _to_layout(x, y)
    _move(lx, ly)
    time.sleep(0.1)
    step = -1 if direction == "down" else 1
    for _ in range(max(1, min(amount, 20))):
        _post(Q.CGEventCreateScrollWheelEvent(None, Q.kCGScrollEventUnitLine, 1, step))
        time.sleep(0.03)
    return f"scrolled {direction} {amount} at ({round(x)}, {round(y)}) in the screenshot"


# --- the keyboard ---------------------------------------------------------------------------------------

def _key(code: int, down: bool, flags: int = 0) -> None:
    import Quartz as Q
    ev = Q.CGEventCreateKeyboardEvent(None, code, down)
    Q.CGEventSetFlags(ev, flags)
    _post(ev)


def _parse(combo: str) -> tuple[list[str], int]:
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ScreenError("no key given")
    unknown = [p for p in parts[:-1] if p.lower() not in _HOLD]
    if unknown:
        raise ScreenError(f"unknown modifier(s): {', '.join(unknown)}")
    key = parts[-1].lower()
    if key not in KEYS:
        raise ScreenError(f"unknown key {parts[-1]!r}")
    return [_HOLD[p.lower()] for p in parts[:-1]], KEYS[key]


def _press(mods: list[str], code: int) -> None:
    _need_access()
    flags = 0
    try:
        for m in mods:
            flags |= _flag(m)
            _key(_MOD_KEYS[m], True, flags)
        _key(code, True, flags)
        time.sleep(0.03)
        _key(code, False, flags)
    finally:                               # never leave a modifier held down
        for m in reversed(mods):
            flags &= ~_flag(m)
            _key(_MOD_KEYS[m], False, flags)


def press_code(combo: str) -> str:
    mods, code = _parse(combo)
    _refocus()
    _press(mods, code)
    return f"pressed {combo}"


def press_keys(combo: str) -> str:
    """A key or shortcut such as 'Enter', 'Escape', 'cmd+s', 'cmd+shift+t'."""
    mods, code = _parse(combo)
    _refocus()
    _press(mods, code)
    return f"pressed {combo}"


def _type_unicode(text: str) -> None:
    import Quartz as Q
    _need_access()
    for line_no, line in enumerate(re.split(r"\r\n|\r|\n", text)):
        if line_no:
            _press([], KEYS["return"])
        for i in range(0, len(line), 16):
            chunk = line[i:i + 16]
            for down in (True, False):
                ev = Q.CGEventCreateKeyboardEvent(None, 0, down)
                Q.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
                _post(ev)
            time.sleep(0.01)


def type_text(text: str) -> str:
    _refocus()
    try:
        w = macwin.active_window()
        app = (w or {}).get("class", "")
    except Exception:  # noqa: BLE001
        app = ""
    if app.lower() in TERMINALS:
        _type_unicode(text)
        return f"typed {len(text)} characters"
    saved = clipboard.save()
    try:
        clipboard.copy(text.encode(), clipboard.TEXT)
    except clipboard.ClipboardError:
        if re.search(r"[\r\n]", text):
            raise ScreenError("can't paste, and typing a line break would press Enter: type one line at a time") from None
        _type_unicode(text)
        return f"typed {len(text)} characters"
    try:
        time.sleep(0.05)
        _press(["cmd"], KEYS["v"])
        time.sleep(0.4)
    finally:
        clipboard.restore(saved, ours=text.encode())
    return f"pasted {len(text)} characters"
