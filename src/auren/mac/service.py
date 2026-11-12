"""Auren's background services on macOS: two launchd agents, as the systemd user units are on Omarchy.

    com.auren.daemon   `auren daemon`   ~/Library/LaunchAgents/com.auren.daemon.plist
    com.auren.mascot   `auren mascot`   ~/Library/LaunchAgents/com.auren.mascot.plist

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
        "ProgramArguments": [str(DATA_HOME / "venv/bin/auren"), name],
        "RunAtLoad": True,
        "KeepAlive": {"SuccessfulExit": False},
        "ThrottleInterval": 10,
        "ProcessType": "Interactive",          # the mic and the voice want low latency
        "EnvironmentVariables": {"PATH": ":".join(seen), "PYTHONUNBUFFERED": "1",
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


def restart(name: str) -> bool:
    if not loaded(name):
        return start(name)
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
