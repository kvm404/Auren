"""`auren doctor` on macOS: the system, the permissions (as the daemon has them), audio, the shared checks
(speech key, voice, brains, integrations) and the install (launch agents, talk keys)."""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import sys
from pathlib import Path

from .. import config
from ..config import DATA_HOME
from ..doctor import FAIL, OK, WARN, Check, _run, _shared, _version, phone_checks
from . import service

FIX_PERMISSIONS = "open Auren's setup (menu bar icon → Set up Auren…, or `auren open`) and click Allow"


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
    program = "Auren" if program.endswith(".app") else program
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
        out.append(Check(g, "macOS libraries (pyobjc)", FAIL, str(e), config.REINSTALL))

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
    out += phone_checks()

    g = "Install"
    ctl = DATA_HOME / "bin/auren-ctl"
    out.append(Check(g, "auren-ctl", OK, str(ctl)) if os.access(ctl, os.X_OK) else
               Check(g, "auren-ctl", FAIL, f"{ctl} missing", config.REINSTALL))
    from . import app
    out.append(Check(g, "Auren.app", OK, str(app.APP)) if app.current() else
               Check(g, "Auren.app", WARN, "out of date" if app.installed() else
                     "not installed: macOS lists the permissions under a Python, not Auren", config.REINSTALL))
    for name, label in service.LABELS.items():
        if not service.plist_path(name).exists():
            out.append(Check(g, label, FAIL, "launch agent not installed", config.REINSTALL))
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


# --- the phone remote and the Application Firewall ------------------------------------------------

SOCKETFILTERFW = "/usr/libexec/ApplicationFirewall/socketfilterfw"
FIREWALL_PANE = "System Settings → Network → Firewall → Options"


def _fw(*args: str) -> tuple[int, str]:
    return _run([SOCKETFILTERFW, *args])


def firewall_state(text: str) -> str:
    """--getglobalstate's answer: "on", "off", "block all" (State = 2 on older macOS) or "" (unknown)."""
    t = text.lower()
    if "state = 2" in t:
        return "block all"
    if "state = 0" in t or "disabled" in t:
        return "off"
    if "state = 1" in t or "enabled" in t:
        return "on"
    return ""


def blocks_all(text: str) -> bool | None:
    """--getblockall's answer ("... block all state set to enabled.", or "Block all ENABLED!" on older macOS)."""
    t = text.lower()
    return False if "disabled" in t else True if "enabled" in t else None


def app_state(text: str) -> str:
    """--getappblocked's answer for one program: "blocked", "allowed", or "" (not in the firewall's list yet,
    or an answer this doesn't know)."""
    t = text.lower()
    if "not part of" in t or "not found" in t or "no such" in t:
        return ""
    if "not blocked" in t or "permitted" in t or "allowed" in t:
        return "allowed"
    return "blocked" if "blocked" in t else ""


def listed_apps(text: str) -> dict[str, str] | None:
    """--listapps's answer: {program path: "allowed" | "blocked" | ""}. Each program is a numbered line
    ("1 :  /path"), with its rule on the next ("( Allow incoming connections )"). None: a format this
    doesn't know (then nothing is taken as allowed)."""
    apps: dict[str, str] = {}
    last = None
    for line in text.splitlines():
        if m := re.match(r"^\s*\d+\s*:\s*(/.+?)\s*$", line):
            last = m[1]
            apps[last] = ""
        elif last and apps[last] == "" and re.search(r"incoming", line, re.I):
            t = line.lower()
            apps[last] = "blocked" if "block" in t else "allowed" if "allow" in t else ""
    if not apps and not re.search(r"total number of apps\s*=\s*0\b", text, re.I):
        return None
    return apps


def listener() -> list[str]:
    """The programs the firewall may know the phone server as: the venv's Python (as macOS runs it, links
    followed) and Auren.app, whose child it is."""
    from . import app
    return [os.path.realpath(sys.executable), str(app.APP)]


def firewall_check(port: int) -> Check | None:
    """The Application Firewall (System Settings → Network → Firewall). On, it asks once whether the program
    that listens may accept incoming connections; until then, or once denied, a phone can't connect. "Block
    all incoming connections" stops every phone whatever is allowed. None: socketfilterfw isn't there."""
    g = "Phone"
    if not os.access(SOCKETFILTERFW, os.X_OK):
        return None
    code, out = _fw("--getglobalstate")
    state = firewall_state(out) if code == 0 else ""
    if state == "off":
        return Check(g, "firewall", OK, "the macOS firewall is off")
    block_all = state == "block all"
    if not block_all:
        code, out = _fw("--getblockall")
        block_all = code == 0 and blocks_all(out) is True
    if block_all:
        return Check(g, "firewall", WARN, "the macOS firewall blocks all incoming connections: no phone can reach"
                     " this Mac", f"{FIREWALL_PANE}: turn off Block all incoming connections")
    programs = listener()
    names = " or ".join(dict.fromkeys(Path(p).name for p in programs))
    # The firewall's own list says allowed or blocked. --getappblocked is asked too, but only its "blocked"
    # counts: it says "permitted" for programs that aren't in the list at all.
    code, out = _fw("--listapps")
    apps = listed_apps(out) if code == 0 else None
    listed = {os.path.realpath(path): rule for path, rule in (apps or {}).items()}
    found = {}
    for prog in programs:
        code, out = _fw("--getappblocked", prog)
        asked = app_state(out) if code == 0 else ""
        rule = listed.get(os.path.realpath(prog), "")
        found[prog] = "blocked" if "blocked" in (rule, asked) else rule
    blocked = [Path(p).name for p, st in found.items() if st == "blocked"]
    if blocked:
        return Check(g, "firewall", WARN, f"the macOS firewall blocks {', '.join(blocked)}: the phone can't connect",
                     f"{FIREWALL_PANE}: set {', '.join(blocked)} to Allow incoming connections")
    if any(st == "allowed" for st in found.values()):
        return Check(g, "firewall", OK, f"the macOS firewall allows Auren's server (port {port})")
    if state == "":
        why = "couldn't ask the macOS firewall how it is"
    else:
        why = (f"the macOS firewall is on: when Auren first listens for a phone, macOS asks whether {names} may"
               " accept incoming network connections. Click Allow")
    return Check(g, "firewall", WARN, why,
                 f"if you clicked Deny, or nothing asked: {FIREWALL_PANE}, set {names} to Allow incoming connections"
                 " (add it with + if it isn't listed)")
