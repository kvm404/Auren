"""Live signals for the mascot: what auren is doing, in words, and how loud it is.

- describe(tool, args): a short phrase for a tool call ("Opening WhatsApp"), shown in the
  mascot's speech bubble while auren works, and whether it is using the mouse.
- Level: the loudness of the mic while listening and of the voice while speaking, 0..1,
  written ~20 times a second to $XDG_RUNTIME_DIR/auren/level (same-length writes in place,
  so a reader never sees an empty file). The mascot bounces to it.
"""

from __future__ import annotations

import math
import os

import numpy as np

from .config import RUNTIME_DIR

LEVEL_FILE = RUNTIME_DIR / "level"

MOUSE_TOOLS = {"look_at_screen", "click_at", "drag_at", "scroll_at", "type_text", "press_keys"}

_PHRASES = {
    "system_state": "Checking your system",
    "system_lookup": "Looking that up",
    "system_info": "Checking your system",
    "launch_app": "Opening {app}",
    "focus_window": "Switching to {window}",
    "move_window": "Moving {window}",
    "switch_workspace": "Going to workspace {workspace}",
    "close_window": "Closing {window}",
    "open_url": "Opening a page",
    "open_path": "Opening a file",
    "screenshot": "Taking a screenshot",
    "volume": "Changing the volume",
    "brightness": "Changing the brightness",
    "media": "Controlling the music",
    "notify": "Sending a notification",
    "set_reminder": "Setting a reminder",
    "toggle": "Switching {feature}",
    "lock_screen": "Locking the screen",
    "list_agents": "Checking your agents",
    "read_agent": "Reading {agent}",
    "prompt_agent": "Asking {agent}",
    "start_agent": "Starting an agent",
    "run_command": "Running a command",
    "job_status": "Checking the job",
    "close_job": "Closing a job",
    "show_agent": "Showing the agent",
    "browser_open": "Opening the browser",
    "browser_read": "Reading the page",
    "browser_click": "Clicking {target}",
    "browser_type": "Typing in the browser",
    "browser_scroll": "Scrolling the page",
    "browser_close": "Closing the tab",
    "browser_bookmarks": "Looking at bookmarks",
    "recent_downloads": "Checking downloads",
    "look_at_screen": "Looking at your screen",
    "click_at": "Clicking {target}",
    "drag_at": "Dragging: {target}",
    "scroll_at": "Scrolling",
    "type_text": "Typing",
    "press_keys": "Pressing {keys}",
    "confirm_action": "Doing it",
}


def describe(tool: str, args: dict | None = None) -> tuple[str, bool]:
    """('Opening WhatsApp', uses_mouse) for a tool call."""
    args = args or {}
    phrase = _PHRASES.get(tool, "Working")
    fields = {k: str(v)[:28] for k, v in args.items() if isinstance(v, (str, int, float)) and str(v).strip()}
    if fields.get("target", "").isdigit():      # "click 7" (an item number from a page read) says nothing
        del fields["target"]
    if "{" in phrase:
        try:
            phrase = phrase.format(**fields)
        except KeyError:              # the argument wasn't given: drop the placeholder
            phrase = phrase.split(" {")[0]
    return phrase, tool in MOUSE_TOOLS


# The notice an action gets when the mascot isn't showing it (notice.py): screen control by what it uses,
# anything else Auren does with the sparkles. None for quick reads (many, and nothing to see), for what
# shows its own feedback (volume, brightness, notifications, the lock screen) and for screenshots, which
# would capture the notice.
SCREEN_KINDS = {"click_at": "click", "drag_at": "mouse", "scroll_at": "mouse", "type_text": "keyboard",
                "press_keys": "keyboard"}
NO_NOTICE = {"system_state", "system_lookup", "system_info", "list_agents", "read_agent", "job_status",
             "browser_read", "browser_scroll", "browser_bookmarks", "recent_downloads", "look_at_screen",
             "screenshot", "volume", "brightness", "notify", "lock_screen"}


def notice_kind(tool: str) -> str | None:
    if tool in SCREEN_KINDS:
        return SCREEN_KINDS[tool]
    from .tools import TOOLS
    return "auren" if tool in TOOLS and tool not in NO_NOTICE else None


def loudness(samples: np.ndarray) -> float:
    """0..1 from float samples in -1..1: about -50 dB is silence, -10 dB is loud speech."""
    if samples.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    if rms <= 1e-6:
        return 0.0
    return min(1.0, max(0.0, (20 * math.log10(rms) + 50) / 40))


def envelope(samples: np.ndarray, rate: int, per_second: int = 20) -> list[float]:
    step = max(1, rate // per_second)
    return [loudness(samples[i:i + step]) for i in range(0, len(samples), step)]


class Level:
    """Writes the current level to LEVEL_FILE. Thread-safe enough: one small pwrite per call."""

    def __init__(self, path=LEVEL_FILE):
        self.path = path
        self._fd: int | None = None
        self._last = -1.0

    def set(self, value: float) -> None:
        value = round(min(1.0, max(0.0, value)), 3)
        if value == self._last:
            return
        self._last = value
        try:
            if self._fd is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._fd = os.open(self.path, os.O_WRONLY | os.O_CREAT, 0o600)
            os.pwrite(self._fd, f"{value:.3f}\n".encode(), 0)
        except OSError:
            self._fd = None
