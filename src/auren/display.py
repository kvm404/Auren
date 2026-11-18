"""Waiting for the graphical session, for Auren's systemd services.

At login, systemd can start the services before the session has passed WAYLAND_DISPLAY to the user
manager. A `ConditionEnvironment=` in the unit would skip them then, and systemd never checks a
condition again, so Auren wouldn't start until the next login. Instead the services start anyway and
call wait() first:

- the display is in this process's environment: carry on;
- it isn't, but reaches the user manager within WAIT seconds: exit 75, and `Restart=on-failure`
  starts the service again, with the manager's environment, display included;
- it never comes (a session without Wayland): exit 78, which the units list in
  `RestartPreventExitStatus=`, so the service stops there instead of restarting forever.

`auren with-display <command>` does the same for a program that isn't Auren's (the mascot's qs), then
replaces itself with it.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

RETRY = 75          # EX_TEMPFAIL: the display arrived, start again to get it
NO_DISPLAY = 78     # EX_CONFIG: no display came; the units don't restart on this one
WAIT = 30


def manager_env() -> dict[str, str]:
    """The systemd user manager's environment (what a restarted service gets)."""
    try:
        out = subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    return dict(line.split("=", 1) for line in out.splitlines() if "=" in line)


def wait(timeout: float = WAIT, sleep=time.sleep) -> int | None:
    """None when the display is there (or this isn't a systemd service); else the code to exit with."""
    if os.environ.get("WAYLAND_DISPLAY") or not os.environ.get("INVOCATION_ID"):
        return None                     # outside systemd, the program itself says what's missing
    deadline = time.monotonic() + timeout
    while True:
        if manager_env().get("WAYLAND_DISPLAY"):
            print("the session's display arrived: starting again to pick it up", file=sys.stderr)
            return RETRY
        if time.monotonic() >= deadline:
            print(f"no Wayland display after {int(timeout)} s: not starting (log in to a graphical session)",
                  file=sys.stderr)
            return NO_DISPLAY
        sleep(1)


def main(argv: list[str]) -> int:
    """auren with-display <command> [args…]"""
    if not argv:
        print("usage: auren with-display <command> [args…]", file=sys.stderr)
        return 2
    code = wait()
    if code is not None:
        return code
    try:
        os.execvp(argv[0], argv)
    except OSError as e:
        print(f"couldn't run {argv[0]}: {e}", file=sys.stderr)
        return 127
