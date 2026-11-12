"""Windows and apps on macOS: Quartz's window list, the Accessibility API and NSWorkspace.

Windows are described with the same keys Hyprland's `hyprctl clients -j` uses, so the code that reads
them (find_window, Excalidraw, the screen tools) works unchanged:

    {"address": "1234",            the CGWindowID, as text
     "class": "Safari",            the app's name
     "initialClass": "com.apple.Safari",
     "title": "...",               needs the Screen Recording permission, else ""
     "pid": 555, "at": [x, y], "size": [w, h],    points, from the top left of the main display
     "workspace": {"id": 1, "name": "1"},           macOS has no API for Spaces: always the current one
     "mapped": True, "floating": False, "focusHistoryID": 0}    front to back

Focusing, raising and closing a particular window go through the Accessibility API (the app's AX
windows matched to the CG window by title and frame), which needs the Accessibility permission.
"""

from __future__ import annotations

import os
import time

from ..config import RUNTIME_DIR

MIN_SIDE = 40        # smaller windows are tooltips, status items and the like


def _mascot_pids() -> set[int]:
    """The mascot's own windows are left out (it writes its pid on start)."""
    try:
        return {int((RUNTIME_DIR / "mascot.pid").read_text().strip())}
    except (OSError, ValueError):
        return set()


def _bundle_ids() -> dict[int, str]:
    from AppKit import NSWorkspace
    return {int(a.processIdentifier()): str(a.bundleIdentifier() or "")
            for a in NSWorkspace.sharedWorkspace().runningApplications()}


def list_windows(on_screen: bool = True) -> list[dict]:
    """Normal app windows, front to back."""
    import Quartz as Q

    opts = Q.kCGWindowListExcludeDesktopElements | (Q.kCGWindowListOptionOnScreenOnly if on_screen
                                                    else Q.kCGWindowListOptionAll)
    infos = Q.CGWindowListCopyWindowInfo(opts, Q.kCGNullWindowID) or []
    skip = _mascot_pids() | {os.getpid()}
    ids = _bundle_ids()
    out = []
    for info in infos:
        if int(info.get("kCGWindowLayer", 1)) != 0 or float(info.get("kCGWindowAlpha", 1)) <= 0:
            continue
        pid = int(info.get("kCGWindowOwnerPID", 0))
        b = info.get("kCGWindowBounds") or {}
        w, h = int(b.get("Width", 0)), int(b.get("Height", 0))
        if pid in skip or w < MIN_SIDE or h < MIN_SIDE:
            continue
        out.append({
            "address": str(int(info["kCGWindowNumber"])), "class": str(info.get("kCGWindowOwnerName") or ""),
            "initialClass": ids.get(pid, ""), "title": str(info.get("kCGWindowName") or ""), "pid": pid,
            "at": [int(b.get("X", 0)), int(b.get("Y", 0))], "size": [w, h],
            "workspace": {"id": 1, "name": "1"}, "mapped": bool(info.get("kCGWindowIsOnscreen", on_screen)),
            "floating": False, "focusHistoryID": len(out),
        })
    return out


def frontmost_pid() -> int | None:
    from AppKit import NSWorkspace
    app = NSWorkspace.sharedWorkspace().frontmostApplication()
    return int(app.processIdentifier()) if app else None


def active_window() -> dict | None:
    """The front window of the frontmost app."""
    pid = frontmost_pid()
    return next((w for w in list_windows() if w["pid"] == pid), None)


def by_address(address: str) -> dict | None:
    return next((w for w in list_windows(on_screen=False) if w["address"] == str(address)), None)


def at(x: float, y: float) -> dict | None:
    """The topmost window under a point (points, global)."""
    for w in list_windows():
        (wx, wy), (ww, wh) = w["at"], w["size"]
        if wx <= x < wx + ww and wy <= y < wy + wh:
            return w
    return None


# --- the Accessibility API ----------------------------------------------------------------------

def trusted(prompt: bool = False) -> bool:
    """Does this process have the Accessibility permission? prompt: ask macOS to show its dialog."""
    from ApplicationServices import AXIsProcessTrustedWithOptions, kAXTrustedCheckOptionPrompt
    return bool(AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: bool(prompt)}))


def _ax(element, attribute: str):
    from ApplicationServices import AXUIElementCopyAttributeValue
    err, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if err == 0 else None


def _ax_point(value) -> tuple[float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGPointType
    ok, p = AXValueGetValue(value, kAXValueCGPointType, None)
    return (p.x, p.y) if ok else None


def _ax_size(value) -> tuple[float, float] | None:
    from ApplicationServices import AXValueGetValue, kAXValueCGSizeType
    ok, s = AXValueGetValue(value, kAXValueCGSizeType, None)
    return (s.width, s.height) if ok else None


def ax_window(w: dict):
    """The AX element of a window from list_windows: same title, else the closest frame."""
    from ApplicationServices import AXUIElementCreateApplication
    app = AXUIElementCreateApplication(w["pid"])
    best, best_d = None, None
    for el in _ax(app, "AXWindows") or []:
        pos, size = _ax(el, "AXPosition"), _ax(el, "AXSize")
        p = _ax_point(pos) if pos is not None else None
        s = _ax_size(size) if size is not None else None
        d = 0.0
        if p and s:
            d = abs(p[0] - w["at"][0]) + abs(p[1] - w["at"][1]) + abs(s[0] - w["size"][0]) + abs(s[1] - w["size"][1])
        if w.get("title") and str(_ax(el, "AXTitle") or "") == w["title"]:
            d -= 10000
        if best_d is None or d < best_d:
            best, best_d = el, d
    return best


def activate(pid: int) -> bool:
    """Bring an app to the front. macOS 14 often refuses a background process that asks with NSRunningApplication
    (cooperative activation), so Auren first sets the app's AXFrontmost, which its Accessibility permission allows."""
    from AppKit import NSApplicationActivateAllWindows, NSRunningApplication
    from ApplicationServices import AXUIElementCreateApplication, AXUIElementSetAttributeValue
    ok = AXUIElementSetAttributeValue(AXUIElementCreateApplication(pid), "AXFrontmost", True) == 0
    app = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is not None:
        ok = bool(app.activateWithOptions_(NSApplicationActivateAllWindows)) or ok
    return ok


def focus(w: dict) -> None:
    """Bring a window to the front: its app first, then the window itself."""
    from ApplicationServices import AXUIElementPerformAction, AXUIElementSetAttributeValue
    activate(w["pid"])
    el = ax_window(w)
    if el is not None:
        AXUIElementSetAttributeValue(el, "AXMain", True)
        AXUIElementPerformAction(el, "AXRaise")
    for _ in range(10):                    # activation is asynchronous
        if frontmost_pid() == w["pid"]:
            break
        time.sleep(0.05)


def close(w: dict) -> bool:
    """Press the window's close button. False if it has none the API can reach."""
    from ApplicationServices import AXUIElementPerformAction
    el = ax_window(w)
    button = _ax(el, "AXCloseButton") if el is not None else None
    return button is not None and AXUIElementPerformAction(button, "AXPress") == 0


def minimized(w: dict) -> bool:
    el = ax_window(w)
    return bool(el is not None and _ax(el, "AXMinimized"))


# --- displays -----------------------------------------------------------------------------------

def main_display() -> dict:
    """The display with the menu bar: its size in points and its scale (2 on Retina)."""
    import Quartz as Q
    d = Q.CGMainDisplayID()
    b = Q.CGDisplayBounds(d)
    mode = Q.CGDisplayCopyDisplayMode(d)
    px = Q.CGDisplayModeGetPixelWidth(mode) if mode else b.size.width
    return {"id": int(d), "x": int(b.origin.x), "y": int(b.origin.y), "width": int(b.size.width),
            "height": int(b.size.height), "scale": (px / b.size.width) if b.size.width else 1.0}


def display_at(x: float, y: float) -> dict:
    """The display under a point (the main one if none is)."""
    import Quartz as Q
    err, ids, n = Q.CGGetDisplaysWithPoint(Q.CGPointMake(x, y), 4, None, None)
    if err == 0 and n:
        b = Q.CGDisplayBounds(ids[0])
        return {"id": int(ids[0]), "x": int(b.origin.x), "y": int(b.origin.y), "width": int(b.size.width),
                "height": int(b.size.height)}
    return main_display()
