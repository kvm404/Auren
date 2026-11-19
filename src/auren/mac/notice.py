"""Notices on macOS. There is no on-screen display like Omarchy's, so short statuses show only in the
mascot's bubble; replies and errors become notifications (Notification Center, through AppleScript)."""

from __future__ import annotations

import os

from ..config import RUNTIME_DIR

__all__ = ["_mascot_running", "show", "notify", "clear"]


def _mascot_running() -> bool:
    try:
        pid = int((RUNTIME_DIR / "mascot.pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def show(message: str, kind: str = "auren", ms: int = 1500, always: bool = False) -> None:
    """Statuses are the mascot's to show: nothing on macOS without it."""


def notify(headline: str, text: str = "", kind: str = "auren", always: bool = False) -> None:
    from ..notice import _spawn, mascot_showing
    if not always and mascot_showing():
        return

    def q(s: str) -> str:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    body = text or headline
    title = "Auren" if not text else headline
    _spawn(["osascript", "-e", f"display notification {q(body[:400])} with title {q(title[:80])}"])


def clear() -> None:
    """Nothing to close."""
