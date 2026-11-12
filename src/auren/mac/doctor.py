"""`auren doctor` on macOS: the system, the permissions (as the daemon has them), audio, the shared checks
(speech key, voice, brains, integrations) and the install (launch agents, talk keys)."""

from __future__ import annotations

import json
import os
import platform
import plistlib

from ..config import DATA_HOME
from ..doctor import FAIL, OK, WARN, Check, _run, _shared, _version
from . import service

FIX_PERMISSIONS = "run `auren permissions`, turn Auren's Python on in the Settings page it opens, then `auren service restart`"


def service_path() -> str | None:
    """The PATH the daemon's launch agent gets."""
    try:
        with open(service.plist_path("daemon"), "rb") as f:
            return plistlib.load(f).get("EnvironmentVariables", {}).get("PATH")
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None


def _permissions() -> list[Check]:
    from ..ctl import send
    g = "Permissions"
    try:
        st = json.loads(send("permissions status", timeout=15))
    except (OSError, ValueError):
        return [Check(g, "permissions", WARN, "Auren isn't running, so its permissions can't be checked",
                      "auren service start, then auren doctor")]
    program = st.get("program", "")
    out = []
    mic = st.get("microphone")
    out.append(Check(g, "Microphone", OK, "allowed") if mic == "granted" else
               Check(g, "Microphone", FAIL if mic in ("denied", "restricted") else WARN,
                     {"not asked": "not asked yet"}.get(mic, str(mic)), FIX_PERMISSIONS))
    out.append(Check(g, "Accessibility", OK, "allowed (talk keys, mouse and keyboard)") if st.get("accessibility") else
               Check(g, "Accessibility", FAIL, f"not allowed for {program}: no talk keys and no screen control",
                     FIX_PERMISSIONS))
    out.append(Check(g, "Screen Recording", OK, "allowed") if st.get("screen") else
               Check(g, "Screen Recording", WARN, f"not allowed for {program}: screenshots show only the desktop",
                     FIX_PERMISSIONS))
    if st.get("accessibility") and not st.get("talk_keys"):
        out.append(Check(g, "talk keys", WARN, "allowed, but the daemon started before that", "auren service restart"))
    return out


def checks() -> list[Check]:
    out: list[Check] = []
    g = "System"
    ver = platform.mac_ver()[0]
    out.append(Check(g, "macOS", OK, ver) if _version(ver) >= (14,) else
               Check(g, "macOS", FAIL, f"{ver}: the voice needs macOS 14 or newer", "update macOS"))
    arch = platform.machine()
    out.append(Check(g, "processor", OK, "Apple Silicon") if arch == "arm64" else
               Check(g, "processor", FAIL, f"{arch}: the voice model's runtime needs Apple Silicon"))
    try:
        import AppKit  # noqa: F401
        import Quartz  # noqa: F401
        out.append(Check(g, "macOS libraries (pyobjc)", OK, "installed"))
    except ImportError as e:
        out.append(Check(g, "macOS libraries (pyobjc)", FAIL, str(e), "./install-macos.sh"))

    out += _permissions()

    g = "Audio"
    try:
        import sounddevice as sd
        dev = sd.query_devices(kind="input")
        out.append(Check(g, "microphone", OK, str(dev.get("name", "default input"))))
    except Exception as e:  # noqa: BLE001 - no input device, or PortAudio missing
        out.append(Check(g, "microphone", FAIL, f"no microphone ({e})", "pick one in System Settings → Sound → Input"))
    svc = service_path()
    out += _shared(svc, mac=True)

    g = "Install"
    ctl = DATA_HOME / "bin/auren-ctl"
    out.append(Check(g, "auren-ctl", OK, str(ctl)) if os.access(ctl, os.X_OK) else
               Check(g, "auren-ctl", FAIL, f"{ctl} missing", "./install-macos.sh"))
    for name, label in service.LABELS.items():
        if not service.plist_path(name).exists():
            out.append(Check(g, label, FAIL, "launch agent not installed", "./install-macos.sh"))
        elif service.running(name):
            out.append(Check(g, label, OK, "running"))
        else:
            out.append(Check(g, label, WARN, "not running", f"auren service start {name}"))
    from ..settings import current
    s = current()
    out.append(Check(g, "talk keys", OK, f"{s['keys.talk']} (fresh conversation: {s['keys.talk_fresh']})"))
    code, _ = _run(["which", "osascript"])
    if code:
        out.append(Check(g, "osascript", FAIL, "missing (notifications, volume, Spotify)"))
    return out
