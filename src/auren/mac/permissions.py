"""The three macOS permissions Auren needs, all for the daemon's own process (the launch agent):

- Microphone: to hear you (without it macOS gives silence, not an error)
- Accessibility: the talk keys, and the mouse and keyboard for screen control
- Screen Recording: to see other apps' windows and their titles in screenshots

macOS asks once per program; `auren permissions` makes the daemon ask (so the dialogs name the program the
daemon runs as, not the terminal), then opens the right Settings pages. status() is what `auren doctor` shows.
"""

from __future__ import annotations

import subprocess
import sys

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
    return {"microphone": microphone(), "accessibility": accessibility(), "screen": screen_recording()}


def program() -> str:
    """The program macOS asks about, as Settings lists it: Auren.app when Auren runs through it, else Python."""
    import os
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
