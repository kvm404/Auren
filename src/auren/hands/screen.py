"""Screen control: let the brain see the screen and use the mouse and keyboard.

The fallback for things no other tool reaches: browser permission popups
("Download multiple files: Allow"), app dialogs ("Close all tabs?"), apps with
no command line.

- look(): a JPEG of the focused monitor, scaled to IMAGE_WIDTH pixels wide. Image
  pixel coordinates are what click() takes; they are mapped back to Hyprland's
  layout coordinates (monitor position + scale).
- click(): moves the cursor with Hyprland (`hl.dsp.cursor.move`, exact, layout
  coordinates) and clicks with a small virtual mouse made through /dev/uinput
  (python-evdev; the user can write to /dev/uinput). The cursor stays there:
  Omarchy has focus-follows-mouse, so moving it back would move the keyboard focus
  away from what was just clicked.
- drag(): press at one point, move to another, release (draw shapes, move sliders and items,
  select text), optionally holding shift/ctrl/alt (Shift makes a perfect circle in Excalidraw).
  Cursor warps while a button is held don't give apps any motion, so the move is real
  relative motion from the virtual mouse, steered by reading the cursor back (pointer
  acceleration applies to it too). Held keys come from a small virtual keyboard: `wtype`
  releases its keys when it exits, so it can't hold one across a drag.
- type_text() / press_keys(): `wtype` (Wayland virtual keyboard), into the window
  that was last clicked (focused again first if focus has moved since).
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from pathlib import Path

IMAGE_WIDTH = 1024

_lock = threading.Lock()
_mouse = None
_keyboard = None
_last_view: dict | None = None       # geometry of the last look(), to map image coords back
_clicked: dict | None = None         # the window the last click landed in, for typing into it


class ScreenError(RuntimeError):
    pass


def _sh(cmd: list[str], timeout: float = 10) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise ScreenError(f"{cmd[0]} failed: {(p.stderr or p.stdout).strip()[:200]}")
    return p.stdout


def _focused_monitor() -> dict:
    return next(m for m in json.loads(_sh(["hyprctl", "monitors", "-j"])) if m.get("focused"))


def look() -> tuple[bytes, str]:
    """Screenshot of the focused monitor. Returns (jpeg bytes, a note on how to address it)."""
    global _last_view
    m = _focused_monitor()
    lw, lh = round(m["width"] / m["scale"]), round(m["height"] / m["scale"])     # layout (logical) size
    factor = IMAGE_WIDTH / lw              # grim's -s scales from the monitor's layout size, not physical pixels
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        _sh(["grim", "-o", m["name"], "-s", f"{factor:.4f}", f.name])
        data, iw, ih = _with_grid(Path(f.name))
    _last_view = {"x": m["x"], "y": m["y"], "lw": lw, "lh": lh, "iw": iw, "ih": ih, "at": time.time()}
    note = (f"Screenshot of monitor {m['name']}, {iw}x{ih} pixels, with a coordinate grid: thin lines every "
            f"{GRID} pixels, numbered along the top (x) and left (y) edges. Read the target's position from those "
            "numbers (don't estimate pixels from the image size) and pass it to click_at.")
    return data, note


GRID = 50


def _with_grid(png: Path) -> tuple[bytes, int, int]:
    """Draw a light, labelled grid so the model reads coordinates off the image instead of estimating them.
    Returns (jpeg, width, height) as actually captured."""
    import io

    from PIL import Image as PILImage
    from PIL import ImageDraw
    img = PILImage.open(png).convert("RGB")
    w, h = img.size
    overlay = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    for x in range(GRID, w, GRID):
        d.line([(x, 0), (x, h)], fill=(255, 0, 255, 70 if x % (GRID * 2) else 120), width=1)
    for y in range(GRID, h, GRID):
        d.line([(0, y), (w, y)], fill=(255, 0, 255, 70 if y % (GRID * 2) else 120), width=1)
    for x in range(GRID * 2, w, GRID * 2):
        d.rectangle([x - 14, 0, x + 14, 11], fill=(0, 0, 0, 200))
        d.text((x - 12, -1), str(x), fill=(255, 255, 0, 255))
    for y in range(GRID * 2, h, GRID * 2):
        d.rectangle([0, y - 6, 24, 6 + y], fill=(0, 0, 0, 200))
        d.text((1, y - 7), str(y), fill=(255, 255, 0, 255))
    img = PILImage.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=75)
    return buf.getvalue(), w, h


def _to_layout(x: float, y: float) -> tuple[int, int]:
    v = _last_view
    if v is None or time.time() - v["at"] > 300:
        raise ScreenError("look at the screen first (look_at_screen), then click using that image's coordinates")
    if not (0 <= x <= v["iw"] and 0 <= y <= v["ih"]):
        raise ScreenError(f"({x}, {y}) is outside the {v['iw']}x{v['ih']} screenshot")
    return round(v["x"] + x * v["lw"] / v["iw"]), round(v["y"] + y * v["lh"] / v["ih"])


def _device():
    """A virtual mouse (buttons + wheel). Kept open: a new input device takes a moment to be picked up."""
    global _mouse
    if _mouse is None:
        from evdev import UInput
        from evdev import ecodes as e
        try:
            _mouse = UInput({e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
                             e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL]}, name="auren-virtual-mouse")
        except (OSError, PermissionError) as err:
            raise ScreenError(f"can't create a virtual mouse via /dev/uinput: {err}") from None
        time.sleep(0.6)
    return _mouse


def _cursor() -> tuple[int, int]:
    x, y = _sh(["hyprctl", "cursorpos"]).strip().split(",")
    return int(x), int(y)


def _move(x: int, y: int) -> None:
    out = _sh(["hyprctl", "dispatch", f"hl.dsp.cursor.move({{ x = {x}, y = {y} }})"]).strip()
    if out != "ok":
        raise ScreenError(f"couldn't move the cursor: {out}")


def _active_window() -> str:
    try:
        return json.loads(_sh(["hyprctl", "activewindow", "-j"])).get("address", "")
    except (ScreenError, json.JSONDecodeError):
        return ""


def _refocus() -> None:
    """Type into the window auren last clicked, even if the focus moved since (the user's mouse moved,
    focus follows it)."""
    if not _clicked or time.time() - _clicked["at"] > 120 or not _clicked["address"]:
        return
    if _active_window() != _clicked["address"]:
        _sh(["hyprctl", "dispatch", f'hl.dsp.focus({{ window = "address:{_clicked["address"]}" }})'])
        time.sleep(0.1)


def click(x: float, y: float, button: str = "left", double: bool = False, restore: bool = False) -> str:
    from evdev import ecodes as e
    btn = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}.get(button)
    if btn is None:
        raise ScreenError("button must be left, right or middle")
    lx, ly = _to_layout(x, y)
    with _lock:
        dev = _device()
        before = _cursor()
        _move(lx, ly)
        time.sleep(0.12)                      # let the app see the pointer arrive (hover states)
        for _ in range(2 if double else 1):
            dev.write(e.EV_KEY, btn, 1); dev.syn()
            time.sleep(0.05)
            dev.write(e.EV_KEY, btn, 0); dev.syn()
            time.sleep(0.08)
        time.sleep(0.1)
        global _clicked
        _clicked = {"address": _active_window(), "at": time.time()}
        if restore:
            time.sleep(0.25)
            _move(*before)
    return f"{'double-' if double else ''}{button}-clicked at screen ({lx}, {ly})"


_HOLD = {"shift": "KEY_LEFTSHIFT", "ctrl": "KEY_LEFTCTRL", "control": "KEY_LEFTCTRL", "alt": "KEY_LEFTALT",
         "super": "KEY_LEFTMETA"}


def _modifiers():
    """A virtual keyboard with only the modifier keys, for holding them during a drag."""
    global _keyboard
    if _keyboard is None:
        from evdev import UInput
        from evdev import ecodes as e
        try:
            _keyboard = UInput({e.EV_KEY: [getattr(e, k) for k in sorted(set(_HOLD.values()))]},
                               name="auren-virtual-modifiers")
        except (OSError, PermissionError) as err:
            raise ScreenError(f"can't create a virtual keyboard via /dev/uinput: {err}") from None
        time.sleep(0.6)
    return _keyboard


def _glide(dev, tx: int, ty: int, steps: int, duration: float) -> None:
    """Move the pointer to layout (tx, ty) with relative motion while a button is held, in `steps` legs.
    Each leg aims at the next point on the straight line, from where the cursor really is; the gain
    (pixels moved per unit sent, which acceleration changes) is learned as it goes."""
    from evdev import ecodes as e
    sx, sy = _cursor()
    gain = 1.0
    for i in range(1, steps + 1):
        px, py = sx + (tx - sx) * i / steps, sy + (ty - sy) * i / steps
        cx, cy = _cursor()
        dx, dy = round((px - cx) / gain), round((py - cy) / gain)
        if dx or dy:
            dev.write(e.EV_REL, e.REL_X, dx); dev.write(e.EV_REL, e.REL_Y, dy); dev.syn()
            time.sleep(max(0.004, duration / steps))
            nx, ny = _cursor()
            sent, moved = abs(dx) + abs(dy), abs(nx - cx) + abs(ny - cy)
            if sent >= 3 and moved:
                gain = max(0.3, min(3.0, 0.5 * gain + 0.5 * moved / sent))
    for _ in range(12):                       # settle exactly on the target
        cx, cy = _cursor()
        if abs(tx - cx) <= 1 and abs(ty - cy) <= 1:
            break
        dx, dy = round((tx - cx) / gain) or (tx > cx) - (tx < cx), round((ty - cy) / gain) or (ty > cy) - (ty < cy)
        dev.write(e.EV_REL, e.REL_X, dx); dev.write(e.EV_REL, e.REL_Y, dy); dev.syn()
        time.sleep(0.01)


def drag(x1: float, y1: float, x2: float, y2: float, hold: str = "", button: str = "left",
         duration: float = 0.5) -> str:
    """Press at image (x1, y1), move to (x2, y2), release. hold: '', 'shift', 'ctrl', 'alt' or 'super'
    (several with '+'), held during the whole drag."""
    from evdev import ecodes as e
    btn = {"left": e.BTN_LEFT, "right": e.BTN_RIGHT, "middle": e.BTN_MIDDLE}.get(button)
    if btn is None:
        raise ScreenError("button must be left, right or middle")
    keys = [p.strip().lower() for p in hold.split("+") if p.strip()]
    unknown = [k for k in keys if k not in _HOLD]
    if unknown:
        raise ScreenError(f"can only hold shift, ctrl, alt or super, not {', '.join(unknown)}")
    lx1, ly1 = _to_layout(x1, y1)
    lx2, ly2 = _to_layout(x2, y2)
    with _lock:
        dev = _device()
        kb = _modifiers() if keys else None
        codes = [getattr(e, _HOLD[k]) for k in keys]
        _move(lx1, ly1)
        time.sleep(0.12)
        try:
            for c in codes:
                kb.write(e.EV_KEY, c, 1); kb.syn()
            dev.write(e.EV_KEY, btn, 1); dev.syn()
            time.sleep(0.08)
            steps = max(8, min(60, int((abs(lx2 - lx1) + abs(ly2 - ly1)) / 12)))
            _glide(dev, lx2, ly2, steps, duration)
            time.sleep(0.08)
        finally:                               # never leave a button or a modifier held down
            dev.write(e.EV_KEY, btn, 0); dev.syn()
            for c in reversed(codes):
                kb.write(e.EV_KEY, c, 0); kb.syn()
        time.sleep(0.1)
        global _clicked
        _clicked = {"address": _active_window(), "at": time.time()}
        end = _cursor()
    held = f" holding {hold}" if keys else ""
    return f"dragged from screen ({lx1}, {ly1}) to ({end[0]}, {end[1]}){held}"


def scroll(x: float, y: float, direction: str = "down", amount: int = 3) -> str:
    from evdev import ecodes as e
    lx, ly = _to_layout(x, y)
    with _lock:
        dev = _device()
        _move(lx, ly)
        time.sleep(0.1)
        step = -1 if direction == "down" else 1
        for _ in range(max(1, min(amount, 20))):
            dev.write(e.EV_REL, e.REL_WHEEL, step); dev.syn()
            time.sleep(0.03)
    return f"scrolled {direction} {amount} at screen ({lx}, {ly})"


def type_text(text: str) -> str:
    _refocus()
    _sh(["wtype", "--", text])
    return f"typed {len(text)} characters"


_KEY_NAMES = {"enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
              "space": "space", "backspace": "BackSpace", "delete": "Delete", "up": "Up", "down": "Down",
              "left": "Left", "right": "Right", "home": "Home", "end": "End", "pageup": "Prior", "pagedown": "Next"}
_MODS = {"ctrl": "ctrl", "control": "ctrl", "shift": "shift", "alt": "alt", "super": "logo", "win": "logo"}


def press_keys(combo: str) -> str:
    """A key or shortcut such as 'Enter', 'Escape', 'ctrl+s', 'ctrl+shift+t', 'alt+F4'."""
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        raise ScreenError("no key given")
    mods = [_MODS[p.lower()] for p in parts[:-1] if p.lower() in _MODS]
    unknown = [p for p in parts[:-1] if p.lower() not in _MODS]
    if unknown:
        raise ScreenError(f"unknown modifier(s): {', '.join(unknown)}")
    key = _KEY_NAMES.get(parts[-1].lower(), parts[-1])
    _refocus()
    cmd = ["wtype"]
    for m in mods:
        cmd += ["-M", m]
    cmd += ["-k", key]
    for m in reversed(mods):
        cmd += ["-m", m]
    _sh(cmd)
    return f"pressed {combo}"
