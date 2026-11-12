"""Open Browser Use on macOS: the darwin build of the same pinned release, and the process lookups that
hands/obu.py does through /proc and `ss` (which macOS doesn't have) done with `lsof` and `ps`."""

from __future__ import annotations

import os
import platform
import re
import subprocess

from ..hands import browsers
from ..hands.obu import SetupError, _own_data_dir

__all__ = ["RELEASE", "SHA256", "_arch", "_listener_pid", "_parent", "_browser_above", "_cmdline"]

RELEASE = "https://github.com/iFurySt/open-browser-use/releases/download/v{v}/open-browser-use-cli-{v}-darwin-{arch}.tar.gz"
SHA256 = {
    "amd64": "06e02a6f37e255e7cebfc74d8acbc270eaa94ca9f112e7fca2c13b3a167a299f",
    "arm64": "280f63b29fb2d8371ce59918739df1d44ca8239da47b61dddee0f90ba169a9cf",
}


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine == "arm64":
        return "arm64"
    raise SetupError(f"Open Browser Use has no macOS build for {machine}")


def _ps(pid: int, field: str) -> str:
    try:
        return subprocess.run(["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _listener_pid(sock: str) -> int | None:
    """The process listening on a Unix socket (lsof prints p<pid> then n<path> lines)."""
    try:
        out = subprocess.run(["lsof", "-U", "-a", "-F", "pn", "--", sock], capture_output=True, text=True,
                             timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        out = ""
    if not out:            # older lsof doesn't take a socket path as a file: list them all
        try:
            out = subprocess.run(["lsof", "-U", "-F", "pn"], capture_output=True, text=True, timeout=5).stdout
        except (OSError, subprocess.TimeoutExpired):
            return None
    pid = None
    real = os.path.realpath(sock)
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:]) if line[1:].isdigit() else None
        elif line.startswith("n") and pid and line[1:].strip() in (sock, real):
            return pid
    return None


def _parent(pid: int) -> int | None:
    v = _ps(pid, "ppid")
    return int(v) if v.isdigit() else None


def _browser_above(pid: int | None) -> tuple[browsers.Kind, bool] | None:
    for _ in range(6):
        pid = _parent(pid) if pid else None
        if not pid or pid <= 1:
            return None
        exe = os.path.basename(_ps(pid, "comm"))
        kind = next((k for k in browsers.KINDS if exe in k.processes), None)
        if kind:
            return kind, _own_data_dir(pid, kind)
    return None


def _cmdline(pid: int) -> list[bytes]:
    """The arguments (ps joins them with spaces; good enough to find --user-data-dir=...)."""
    args = _ps(pid, "args")
    m = re.search(r"--user-data-dir=(.+?)(?= --|$)", args)
    return [b"--user-data-dir=" + m.group(1).encode()] if m else []
