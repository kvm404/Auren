"""`auren update` on macOS: what update.py does with systemd, done with launchd.

The update runs as a process of its own session (so stopping Auren's launch agents doesn't stop it), and
update.py's systemctl calls are answered from mac/service.py.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable

from . import service

NAMES = {"auren": "daemon", "auren-mascot": "mascot"}


def spawn(data: dict, write: Callable[..., dict], kept: Callable[[], dict]) -> dict:
    from ..update import LOG
    LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen([sys.executable, "-m", "auren.cli", "update", "run"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        return write(state="failed", message=f"Couldn't start the update: {e}", **kept())
    return data


def launchctl_like(*args: str) -> int:
    """systemctl --user is-active/is-enabled/stop/restart/disable for Auren's two units, as 0 (yes, done) or 1."""
    verb, units = args[0], [NAMES[a] for a in args[1:] if a in NAMES]
    if verb == "is-active":
        return 0 if all(service.running(u) for u in units) else 1
    if verb == "is-enabled":
        return 0 if all(service.plist_path(u).exists() for u in units) else 1
    if verb == "stop":
        return 0 if all(service.stop(u) for u in units) else 1
    if verb == "restart":
        return 0 if all(service.restart(u) for u in units) else 1
    if verb == "disable":
        return 0                    # a launch agent that's installed runs at login: nothing to turn off
    return 1
