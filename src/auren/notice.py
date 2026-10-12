"""What Auren shows on screen when the mascot isn't there to show it.

The mascot's bubble is the main place: what Auren heard, is doing, and says. When the mascot isn't
running, is hidden, or has its bubble turned off, the same news goes elsewhere:
- short statuses (listening, thinking, clicking...) to Omarchy's on-screen display, with an icon for
  their kind. Without an icon the OSD reads the message as a volume of 0% and draws a muted speaker.
  What Auren *does* (a click, a key press, opening an app...) is shown there even while the bubble is
  up (`always`): the bubble is easy to miss, and this one-line note sits where the eye already is;
- replies and errors, which need reading in full, to a notification (the OSD is one short line).

Before a screenshot, `clear()` closes an OSD Auren opened, so the brain sees the screen and not Auren's
own note on top of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from .config import CONFIG_DIR, STATE_DIR

# Material Design glyphs, in every Nerd Font (Omarchy's fonts are Nerd Fonts).
ICONS = {
    "click": "\U000F0CFD",       # cursor-click
    "mouse": "\U000F037D",       # mouse
    "keyboard": "\U000F030C",    # keyboard
    "listening": "\U000F036C",   # microphone
    "unheard": "\U000F036D",     # microphone-off
    "thinking": "\U000F07F6",    # thought-bubble
    "stopped": "\U000F0666",     # stop-circle
    "error": "\U000F0028",       # alert-circle
    "update": "\U000F06B0",      # update
    "auren": "\U000F0674",       # sparkles: anything else Auren does
}

_seen = [0.0, False]            # when the mascot's process was last looked for, and whether it was running
_osd_at = 0.0                   # when Auren last opened the OSD
_opening: list[subprocess.Popen] = []    # omarchy-osd calls that may not have reached the shell yet
_osd = threading.Lock()                  # show() runs on the daemon's threads, clear() on a tool's


def _spawn(cmd: list[str]) -> subprocess.Popen | None:
    try:
        return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return None              # not on Omarchy: nowhere to show it


def _json(path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _mascot_running() -> bool:
    for pid in os.listdir("/proc"):
        if pid.isdigit():
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    if b"auren/mascot" in f.read().lower():
                        return True
            except OSError:
                pass
    return False


def mascot_showing() -> bool:
    """Is the mascot on screen with its bubble on? Its settings are read each time (they change from its
    menu); whether it runs is looked up at most every 3 s."""
    if time.monotonic() - _seen[0] > 3:
        _seen[:] = [time.monotonic(), _mascot_running()]
    return (_seen[1] and _json(CONFIG_DIR / "mascot.json").get("bubble", True) is not False
            and not _json(STATE_DIR / "mascot.json").get("hidden", False))


def show(message: str, kind: str = "auren", ms: int = 1500, always: bool = False) -> None:
    """A one-line status on Omarchy's OSD, unless the mascot shows it already (`always`: even then)."""
    global _osd_at
    if not always and mascot_showing():
        return
    with _osd:
        _osd_at = time.monotonic()
        _opening[:] = [p for p in _opening if p.poll() is None]
        if p := _spawn(["omarchy-osd", "-i", ICONS.get(kind, ICONS["auren"]), "-m", message, "-d", str(ms)]):
            _opening.append(p)


def notify(headline: str, text: str = "", kind: str = "auren", always: bool = False) -> None:
    """Something to read in full (a reply, an error), as a notification, unless the mascot shows it
    (`always`: news the mascot doesn't show, like an update)."""
    if not always and mascot_showing():
        return
    _spawn(["omarchy-notification-send", "--app-name", "auren", "-g", ICONS.get(kind, ICONS["auren"]),
            *(["-u", "normal"] if kind == "error" else []), headline, *([text] if text else [])])


def clear() -> None:
    """Close the OSD if Auren opened it in the last few seconds (before taking a screenshot)."""
    with _osd:
        if time.monotonic() - _osd_at >= 5:
            return
        for p in _opening:       # one still on its way would open the OSD again after the close
            try:
                p.wait(timeout=1)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()
        _opening.clear()
        try:
            subprocess.run(["omarchy-shell", "-q", "osd", "close"], capture_output=True, timeout=2)
            time.sleep(0.05)     # let the compositor drop it before the capture
        except (OSError, subprocess.TimeoutExpired):
            pass
