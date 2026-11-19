"""Auren's background services on macOS: two launchd agents, as the systemd user units are on Omarchy.

    com.auren.daemon   `auren daemon`   ~/Library/LaunchAgents/com.auren.daemon.plist
    com.auren.mascot   `auren mascot`   ~/Library/LaunchAgents/com.auren.mascot.plist

Both run through Auren.app when it's installed (mac/app.py), so macOS gives the permissions to Auren.
Both start at login and are started again if they crash (KeepAlive on a failed exit). `stop` unloads one
until it's started again or the next login; `auren-ctl quit` and the menu's Quit end the daemon cleanly.

The PATH the services get is written into the plists when they're installed (the installer passes the
login shell's), so the brains (`claude`, `codex`), herdr and Homebrew's tools are found.
"""

from __future__ import annotations

import os
import plistlib
import shlex
import subprocess
import time
from pathlib import Path

from ..config import DATA_HOME, STATE_DIR
from . import app

AGENTS_DIR = Path.home() / "Library/LaunchAgents"
LABELS = {"daemon": "com.auren.daemon", "mascot": "com.auren.mascot"}
DEFAULT_PATH = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"


def plist_path(name: str) -> Path:
    return AGENTS_DIR / f"{LABELS[name]}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def _target(name: str) -> str:
    return f"{_domain()}/{LABELS[name]}"


def plist(name: str, path: str | None = None) -> dict:
    """The launch agent for `auren <name>`, run from the installed venv."""
    home = str(Path.home())
    extra = [f"{home}/.local/bin", str(DATA_HOME / "bin")]
    parts = [*extra, *(path or os.environ.get("PATH") or DEFAULT_PATH).split(":"), *DEFAULT_PATH.split(":")]
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return {
        "Label": LABELS[name],
        # Through Auren.app when it's there, so the permissions are Auren's (mac/app.py)
        "ProgramArguments": [str(app.executable()), name] if app.installed() else [str(DATA_HOME / "venv/bin/auren"), name],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",          # the mic and the voice want low latency
        "EnvironmentVariables": {"PATH": ":".join(seen), "PYTHONUNBUFFERED": "1",
                                 "AUREN_PYTHON": str(DATA_HOME / "venv/bin/python"),
                                 **({"LANG": os.environ["LANG"]} if os.environ.get("LANG") else {"LANG": "en_US.UTF-8"})},
        "StandardOutPath": str(STATE_DIR / f"{name}.out.log"),
        "StandardErrorPath": str(STATE_DIR / f"{name}.out.log"),
    }


def install(name: str, path: str | None = None) -> Path:
    """Write the agent's plist (replacing an old one). It runs from the next start or login."""
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    p = plist_path(name)
    tmp = p.with_name(p.name + ".tmp")
    with open(tmp, "wb") as f:
        plistlib.dump(plist(name, path), f)
    os.replace(tmp, p)
    return p


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, timeout=20)


def loaded(name: str) -> bool:
    return _launchctl("print", _target(name)).returncode == 0


def running(name: str) -> bool:
    p = _launchctl("print", _target(name))
    return p.returncode == 0 and "state = running" in p.stdout


def start(name: str) -> bool:
    """Load the agent if it isn't, and start it now."""
    if not plist_path(name).exists():
        return False
    for _ in range(10):                     # right after a bootout, bootstrap fails until launchd lets go of it
        if loaded(name) or _launchctl("bootstrap", _domain(), str(plist_path(name))).returncode == 0:
            break
        time.sleep(0.3)
    return _launchctl("kickstart", _target(name)).returncode == 0


def _loaded_program(name: str) -> str | None:
    """The program launchd runs for the loaded agent, as it read it from the plist when it was loaded."""
    p = _launchctl("print", _target(name))
    for line in p.stdout.splitlines() if p.returncode == 0 else []:
        key, _, value = line.strip().partition(" = ")
        if key == "program":
            return value.strip()
    return None


def _loaded_env(name: str) -> dict[str, str] | None:
    """The environment launchd gives the loaded agent (`environment = { KEY => value ... }` in its print)."""
    p = _launchctl("print", _target(name))
    if p.returncode != 0:
        return None
    env, inside = {}, False
    for line in p.stdout.splitlines():
        s = line.strip()
        if s == "environment = {":
            inside = True
        elif inside and s == "}":
            return env
        elif inside:
            key, sep, value = s.partition(" => ")
            if sep:
                env[key] = value
    return None


def _plist_env(name: str) -> dict[str, str]:
    try:
        with open(plist_path(name), "rb") as f:
            return {str(k): str(v) for k, v in plistlib.load(f).get("EnvironmentVariables", {}).items()}
    except (OSError, ValueError, AttributeError, plistlib.InvalidFileException):
        return {}


def _stale(name: str) -> bool:
    """The plist changed since launchd loaded it: another program (Auren.app installed or removed), or another
    PATH (Claude Code or Codex installed since). kickstart would keep the old ones."""
    if _loaded_program(name) != _plist_program(name):
        return True
    loaded = _loaded_env(name)
    return loaded is not None and any(loaded.get(k) != v for k, v in _plist_env(name).items())


def _plist_program(name: str) -> str | None:
    try:
        with open(plist_path(name), "rb") as f:
            return str(plistlib.load(f)["ProgramArguments"][0])
    except (OSError, ValueError, KeyError, IndexError, plistlib.InvalidFileException):
        return None


def installed_path(name: str) -> str | None:
    """The PATH the installed plist gives the agent (to write it again with the same one)."""
    try:
        with open(plist_path(name), "rb") as f:
            return str(plistlib.load(f)["EnvironmentVariables"]["PATH"])
    except (OSError, ValueError, KeyError, plistlib.InvalidFileException):
        return None


def restart(name: str) -> bool:
    """Start it again. kickstart -k keeps what launchd read at load time, so when the plist changed since
    (_stale), it's unloaded and loaded again instead."""
    if not loaded(name):
        return start(name)
    if _stale(name):
        return stop(name) and start(name)
    return _launchctl("kickstart", "-k", _target(name)).returncode == 0


def stop(name: str) -> bool:
    """Unload it (it would be started again otherwise); it comes back at the next login or start."""
    if not loaded(name):
        return True
    return _launchctl("bootout", _target(name)).returncode == 0


def uninstall(name: str) -> None:
    stop(name)
    plist_path(name).unlink(missing_ok=True)


def open_in_terminal(command: str) -> None:
    """Run a command in a new Terminal window (for `auren doctor` from the menu)."""
    exe = DATA_HOME / "venv/bin"
    line = f"export PATH={shlex.quote(str(exe))}:$PATH; {command}"
    script = 'tell application "Terminal"\nactivate\ndo script ' + '"' + line.replace("\\", "\\\\").replace('"', '\\"') + '"\nend tell'
    subprocess.Popen(["osascript", "-e", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
