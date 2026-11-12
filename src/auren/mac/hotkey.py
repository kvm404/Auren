"""The talk keys on macOS: a Quartz event tap in the daemon, standing in for Hyprland's bindings.

    stop = start({"talk": "ctrl+alt+a", "talk-fresh": "ctrl+alt+shift+a"}, on_press)

The tap sees every key press in the session; one that matches a binding exactly (the same modifiers, no
others) is swallowed and `on_press(command)` is called on a separate thread. Auto-repeat is ignored, so
holding the keys doesn't start and stop listening. The tap needs the Accessibility permission, which
Auren's screen control needs anyway; without it start() returns None and `auren doctor` says why.

macOS turns a tap off when its callback is slow or the user types in a secure field; the callback turns
it back on.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from .screen import _HOLD, KEYS

log = logging.getLogger("auren.hotkey")

MOD_NAMES = ("cmd", "shift", "alt", "ctrl")


def parse(combo: str) -> tuple[frozenset[str], int]:
    """'ctrl+alt+a' -> ({'ctrl', 'alt'}, key code of A)."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if len(parts) < 2 or parts[-1] not in KEYS or any(p not in _HOLD for p in parts[:-1]):
        raise ValueError(f"not a shortcut: {combo!r}")
    return frozenset(_HOLD[p] for p in parts[:-1]), KEYS[parts[-1]]


def _mods(flags: int) -> frozenset[str]:
    import Quartz as Q
    masks = {"cmd": Q.kCGEventFlagMaskCommand, "shift": Q.kCGEventFlagMaskShift, "alt": Q.kCGEventFlagMaskAlternate,
             "ctrl": Q.kCGEventFlagMaskControl}
    return frozenset(m for m, bit in masks.items() if flags & bit)


class Hotkeys:
    def __init__(self, bindings: dict[str, str], on_press: Callable[[str], None]):
        self.keys = {parse(combo): command for command, combo in bindings.items()}
        self.on_press = on_press
        self.tap = None
        self.source = None
        self.loop = None
        self._down: set[int] = set()        # key codes whose press was swallowed: swallow their release too

    def _callback(self, proxy, kind, event, refcon):
        import Quartz as Q
        if kind in (Q.kCGEventTapDisabledByTimeout, Q.kCGEventTapDisabledByUserInput):
            Q.CGEventTapEnable(self.tap, True)
            return event
        code = Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventKeycode)
        if kind == Q.kCGEventKeyUp:
            if code in self._down:
                self._down.discard(code)
                return None
            return event
        command = self.keys.get((_mods(Q.CGEventGetFlags(event)), code))
        if command is None:
            return event
        self._down.add(code)
        if not Q.CGEventGetIntegerValueField(event, Q.kCGKeyboardEventAutorepeat):
            threading.Thread(target=self._fire, args=(command,), daemon=True).start()
        return None

    def _fire(self, command: str) -> None:
        try:
            self.on_press(command)
        except Exception:  # noqa: BLE001 - a failed press must not kill the tap
            log.exception("talk key %s failed", command)

    def run(self, ready: threading.Event) -> None:
        import Quartz as Q
        mask = Q.CGEventMaskBit(Q.kCGEventKeyDown) | Q.CGEventMaskBit(Q.kCGEventKeyUp)
        self.tap = Q.CGEventTapCreate(Q.kCGSessionEventTap, Q.kCGHeadInsertEventTap, Q.kCGEventTapOptionDefault,
                                      mask, self._callback, None)
        if self.tap is None:
            ready.set()
            return
        self.source = Q.CFMachPortCreateRunLoopSource(None, self.tap, 0)
        self.loop = Q.CFRunLoopGetCurrent()
        Q.CFRunLoopAddSource(self.loop, self.source, Q.kCFRunLoopCommonModes)
        Q.CGEventTapEnable(self.tap, True)
        ready.set()
        Q.CFRunLoopRun()

    def stop(self) -> None:
        """Turn the tap off and free it: a tap that's left on with no run loop stalls the keyboard."""
        import Quartz as Q
        if self.tap is not None:
            Q.CGEventTapEnable(self.tap, False)
            if self.loop is not None and self.source is not None:
                Q.CFRunLoopRemoveSource(self.loop, self.source, Q.kCFRunLoopCommonModes)
            Q.CFMachPortInvalidate(self.tap)
        if self.loop is not None:
            Q.CFRunLoopStop(self.loop)


def start(bindings: dict[str, str], on_press: Callable[[str], None]) -> Hotkeys | None:
    """Listen for the talk keys on a thread of its own. None if macOS didn't allow the tap."""
    try:
        keys = Hotkeys(bindings, on_press)
    except ValueError as e:
        log.error("talk keys: %s", e)
        return None
    ready = threading.Event()
    threading.Thread(target=keys.run, args=(ready,), name="auren-hotkeys", daemon=True).start()
    ready.wait(5)
    if keys.tap is None:
        log.error("can't listen for the talk keys: allow Auren under Privacy & Security → Accessibility, then "
                  "restart it (auren doctor)")
        return None
    log.info("talk keys: %s", ", ".join(f"{combo} → {cmd}" for cmd, combo in bindings.items()))
    return keys
