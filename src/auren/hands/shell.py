"""Shell commands without herdr: each runs in the background, its output going to a log file.

The same results as the herdr runner (`job`, `status`, `exit_code`, `output_tail`), so the brain doesn't
need to know which one ran it. Jobs live as long as the daemon does; their logs are in
~/.local/state/auren/jobs/.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import STATE_DIR

JOBS_DIR = STATE_DIR / "jobs"


class ShellError(RuntimeError):
    pass


@dataclass
class Job:
    name: str
    command: str
    cwd: str
    proc: subprocess.Popen
    log: Path


_jobs: dict[str, Job] = {}
_lock = threading.Lock()


def _slug(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:24] or "job"
    name, n = base, 2
    while name in _jobs:
        name, n = f"{base}-{n}", n + 1
    return name


def has(job: str) -> bool:
    return job in _jobs


def _tail(path: Path, lines: int) -> str:
    try:
        return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def run_command(command: str, cwd: str = "~", name: str = "", wait: bool = False, timeout_s: int = 600) -> dict:
    cwd = str(Path(os.path.expanduser(cwd)).resolve())
    if not Path(cwd).is_dir():
        raise ShellError(f"folder does not exist: {cwd}")
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with _lock:
        job = _slug(name or command.split()[0])
        log = JOBS_DIR / f"{job}.log"
        with log.open("w") as out:
            proc = subprocess.Popen(["bash", "-lc", command], cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
        _jobs[job] = Job(job, command, cwd, proc, log)
    result = {"job": job, "cwd": cwd, "status": "running"}
    if wait:
        result.update(command_result(job, timeout_s))
    return result


def command_result(job: str, timeout_s: int = 0) -> dict:
    j = _jobs.get(job)
    if j is None:
        raise ShellError(f"no auren job called {job}")
    deadline = time.monotonic() + timeout_s
    while j.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.2)
    code = j.proc.poll()
    if code is None:
        return {"status": "running", "output_tail": _tail(j.log, 20)}
    return {"status": "finished", "exit_code": code, "output_tail": _tail(j.log, 30)}


def close_job(job: str) -> str:
    """Stop a job Auren started (if it's still running) and forget it."""
    with _lock:
        j = _jobs.pop(job, None)
    if j is None:
        raise ShellError(f"{job} is not an auren job")
    if j.proc.poll() is None:
        try:
            os.killpg(j.proc.pid, signal.SIGTERM)
            j.proc.wait(timeout=3)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            os.killpg(j.proc.pid, signal.SIGKILL)
    return f"closed {job}"
