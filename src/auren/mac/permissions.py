"""The three macOS permissions Auren needs, all for the daemon's own process (the launch agent):

- Microphone: to hear you (without it macOS gives silence, not an error)
- Accessibility: the talk keys, and the mouse and keyboard for screen control
- Screen Recording: to see other apps' windows and their titles in screenshots

macOS asks once per program; `auren permissions` makes the daemon ask (so the dialogs name the program the
daemon runs as, not the terminal), then opens the right Settings pages. status() is what `auren doctor` shows.

A running process doesn't always see a permission it's given later: Screen Recording counts only from the
process's next start, and the microphone may be the same. So Grants also asks a new process (fresh(), this
module run as a program), which answers for Auren too: a child stays the responsibility of the app that
started it (Auren.app). Allowed there but not here means a restart turns it on; the daemon then restarts
itself once it's idle, once for each grant (RESTARTED says which), and the setup window says so meanwhile.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from ..config import STATE_DIR

PANES = {
    "microphone": "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone",
    "accessibility": "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility",
    "screen": "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
}


def microphone() -> str:
    """'granted', 'denied', 'restricted' or 'not asked'."""
    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
        status = int(AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio))
    except Exception:  # noqa: BLE001
        return "unknown"
    return {0: "not asked", 1: "restricted", 2: "denied", 3: "granted"}.get(status, "unknown")


def accessibility(prompt: bool = False) -> bool:
    from .windows import trusted
    try:
        return trusted(prompt)
    except Exception:  # noqa: BLE001
        return False


def screen_recording(prompt: bool = False) -> bool:
    try:
        import Quartz as Q
        if prompt:
            return bool(Q.CGRequestScreenCaptureAccess())
        return bool(Q.CGPreflightScreenCaptureAccess())
    except Exception:  # noqa: BLE001
        return False


def status() -> dict[str, bool | str]:
    """The permissions as this process has them now."""
    return {"microphone": microphone(), "accessibility": accessibility(), "screen": screen_recording()}


# --- what a restart would turn on -------------------------------------------------------------------

# What a running process may not see until it starts again. Accessibility too: after it was turned off
# (or reset) and on again, macOS can keep telling the running daemon no.
LATER = ("microphone", "accessibility", "screen")
FRESH_TTL = 2.0                    # seconds a new process's answer is used for (the window asks every second)
RESTARTED = STATE_DIR / "permissions-restart.json"   # {"for": [names], "at": time}: what the daemon restarted for
TURNING_ON = 30                    # seconds after that restart the setup window still says "Turning on …"
NAMES = {"microphone": "the microphone", "accessibility": "Accessibility", "screen": "Screen Recording",
         "keys": "the talk keys"}


def allowed(name: str, value) -> bool:
    return value == "granted" if name == "microphone" else value is True


def fresh() -> dict[str, bool | str]:
    """The microphone, Accessibility and Screen Recording as a new process sees them ({} if it couldn't tell)."""
    try:
        p = subprocess.run([sys.executable, "-m", "auren.mac.permissions"], capture_output=True, text=True,
                           timeout=15, stdin=subprocess.DEVNULL)
        data = json.loads((p.stdout.strip().splitlines() or ["{}"])[-1])
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}
    return {k: data[k] for k in LATER if isinstance(data, dict) and k in data}


def restarted(path: Path = RESTARTED) -> dict:
    """What the daemon last restarted itself for: {"for": [names], "at": time} ({} if nothing)."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("for"), list) else {}


def turning_on(names) -> str:
    """What the setup window says while a restart turns these on ("" for none)."""
    said = [NAMES.get(n, n) for n in dict.fromkeys(names)]
    if not said:
        return ""
    text = said[0] if len(said) == 1 else ", ".join(said[:-1]) + " and " + said[-1]
    return "Turning on " + text + "…"


class Grants:
    """The permissions for the setup window: this process's, plus what a new process already has (a restart
    turns those on). The new process is asked only while something is missing here, and at most every
    FRESH_TTL seconds. Remembers what a restart was already tried for, so it's tried once per grant."""

    def __init__(self, ask=fresh, live=None, path: Path = RESTARTED, ttl: float = FRESH_TTL, clock=time.monotonic):
        self._ask, self._live, self.path, self.ttl, self._clock = ask, live or status, path, ttl, clock
        self._fresh: dict = {}
        self._at = -math.inf
        self._lock = threading.Lock()      # check, mark and done run on different threads: one at a time

    def forget_fresh(self) -> None:
        """Ask a new process next time (the user just clicked Allow)."""
        self._at = -math.inf

    def _fresh_now(self) -> dict:
        if self._clock() - self._at >= self.ttl:
            self._fresh, self._at = self._ask(), self._clock()
        return self._fresh

    def check(self) -> dict:
        """status(), with "waiting": what's allowed for Auren but not live in this process yet (a granted value
        for it, then)."""
        live = self._live()
        missing = [n for n in LATER if not allowed(n, live.get(n))]
        new = self._fresh_now() if missing else {}
        waiting = [n for n in missing if allowed(n, new.get(n))]
        # A restart that was tried: done once it's live here, or forgotten if it's no longer allowed at all
        # (allowed again later, it's a new grant and gets its own restart).
        with self._lock:
            tried = self.tried()
            keep = [n for n in tried if n == "keys" or (n in missing and (n not in new or n in waiting))]
            if keep != tried:
                self._save(keep)
        return {**live, **{n: new[n] for n in waiting}, "waiting": waiting}

    def tried(self) -> list[str]:
        return [str(n) for n in restarted(self.path).get("for", [])]

    def mark(self, names) -> None:
        """A restart for these is starting now."""
        with self._lock:
            self._save(list(dict.fromkeys([*self.tried(), *names])), time.time())

    def done(self, name: str) -> None:
        """It's live now (the talk keys started)."""
        with self._lock:
            tried = self.tried()
            if name in tried:
                self._save([n for n in tried if n != name])

    def _save(self, names: list[str], at: float | None = None) -> None:
        if not names:
            self.path.unlink(missing_ok=True)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps({"for": names, "at": at if at is not None else restarted(self.path).get("at", 0)})
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w") as f:
                f.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def program() -> str:
    """The program macOS asks about, as Settings lists it: Auren.app when Auren runs through it, else Python."""
    return os.environ.get("AUREN_APP") or os.path.realpath(sys.executable)


def request_one(name: str) -> dict[str, bool | str]:
    """Ask for one permission: macOS shows its prompt, which also adds Auren to that Settings list, so the
    user only has to turn it on. Then open the Settings page if it's still missing."""
    if name == "microphone":
        if microphone() == "not asked":
            try:
                from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
                AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: None)
            except Exception:  # noqa: BLE001
                pass
        elif microphone() != "granted":
            subprocess.Popen(["open", PANES["microphone"]])
    elif name in ("accessibility", "screen"):
        # The first click shows macOS's own prompt (which has an Open System Settings button and adds
        # Auren to the list); a second click, after it was dismissed, opens the Settings page.
        granted = accessibility if name == "accessibility" else screen_recording
        if not granted():
            if name in _prompted:
                subprocess.Popen(["open", PANES[name]])
            else:
                _prompted.add(name)
                granted(prompt=True)
    return status()


_prompted: set[str] = set()        # what request_one has shown macOS's prompt for, in this process


def request() -> dict[str, bool | str]:
    """Ask for what's missing (from the daemon's process), then open the Settings pages still needed."""
    if microphone() == "not asked":
        try:
            from AVFoundation import AVCaptureDevice, AVMediaTypeAudio
            AVCaptureDevice.requestAccessForMediaType_completionHandler_(AVMediaTypeAudio, lambda granted: None)
        except Exception:  # noqa: BLE001
            pass
    if not accessibility():
        accessibility(prompt=True)
    if not screen_recording():
        screen_recording(prompt=True)
    now = status()
    for name, ok in now.items():
        if ok is not True and ok != "granted" and ok != "not asked":
            subprocess.Popen(["open", PANES[name]])
    return now


if __name__ == "__main__":          # fresh(): this process is new, so it sees what macOS allows Auren now
    print(json.dumps({"microphone": microphone(), "accessibility": accessibility(), "screen": screen_recording()}))
