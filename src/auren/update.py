"""Updating Auren: find a newer version on GitHub and install it.

For now an update is the newest commit on `main` (development builds); releases will take its place
later (CHANNEL). The code comes from Auren's own clone of the repo in ~/.cache/auren/source, so a
checkout you work in is never touched. The exception is an install made with `install.sh --dev`, which
runs that checkout's code: then the checkout itself is updated (fast-forward only, and only on a clean
`main`).

  auren update             check, show what's new, ask, update
  auren update check       check only (--json prints the result, --notify sends a notification once
                           per new version)
  auren update apply       update now, in the background

The update runs as its own transient systemd unit, so stopping Auren or restarting the bar doesn't stop
it. It waits until Auren is idle, stops Auren and its mascot, runs the new install.sh, and starts them
again as they were. If the install fails, the version that was there is installed back. If the bar
plugin or the mascot's QML changed, the Omarchy shell is restarted to load it. Progress and the result go to
$XDG_RUNTIME_DIR/auren/update.json (the bar panel shows it) and ~/.local/state/auren/update.log.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import notice
from .config import CACHE_DIR, DATA_HOME, RUNTIME_DIR, STATE_DIR, STATE_FILE

CHANNEL = "main"                                    # later: the newest release
DEFAULT_REMOTE = "https://github.com/kvm404/Auren.git"
INSTALL_INFO = DATA_HOME / "install.json"          # written by install.sh: what's installed, from where
SOURCE = CACHE_DIR / "source"                      # Auren's own clone, what updates install from
STATUS = RUNTIME_DIR / "update.json"
LOG = STATE_DIR / "update.log"
NOTIFIED = STATE_DIR / "update-notified"           # the newest version a notification was sent for
LOCK = RUNTIME_DIR / "update.lock"
UNITS = ("auren", "auren-mascot")
# What the Omarchy shell loads: the bar plugin, and the mascot's QML, which the panel imports for its
# live mascot. A change in either needs the shell restarted (it keeps what it already loaded).
SHELL_PATHS = ("omarchy-plugin", "mascot")

BUSY = {"starting", "listening", "thinking", "working", "speaking"}
IDLE_WAIT = 15 * 60                                # how long an update waits for Auren to be idle
INSTALL_TIMEOUT = 20 * 60
LOCK_WAIT = 10 * 60                                # how long the update waits for a check to finish
UPDATE_TIMEOUT = IDLE_WAIT + INSTALL_TIMEOUT * 2 + 10 * 60    # an "updating" status older than this is stale
# The session's environment the update needs: the user manager's may lack PATH entries (uv), custom XDG
# folders, and what git signs in with (gh, an SSH agent, tokens).
PASS_ENV = ("PATH", "HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
            "XDG_RUNTIME_DIR", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE",
            "SSH_AUTH_SOCK", "GH_TOKEN", "GITHUB_TOKEN", "GIT_SSH_COMMAND", "GIT_ASKPASS", "SSH_ASKPASS")


class UpdateError(Exception):
    pass


# --- small parts ---------------------------------------------------------------------------------

def _git(repo: Path, *args: str, timeout: float = 90) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, timeout=timeout,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})     # fail, don't ask for a login
    except subprocess.TimeoutExpired as e:
        raise UpdateError(f"git {args[0]} took too long: is the network up?") from e
    except OSError as e:
        raise UpdateError("git isn't installed") from e
    if r.returncode:
        lines = (r.stderr or r.stdout).strip().splitlines()
        err = lines[-1] if lines else f"git {args[0]} failed"
        if any(w in err for w in ("could not read Username", "Authentication failed", "Repository not found")):
            err = "GitHub didn't let git in: the repo is private, so sign in with gh auth login"
        elif "Could not resolve host" in err or "unable to access" in err:
            err = "Couldn't reach GitHub: is the network up?"
        raise UpdateError(err)
    return r.stdout.strip()


def _has(repo: Path, commit: str) -> bool:
    return bool(commit) and subprocess.run(["git", "-C", str(repo), "cat-file", "-e", commit + "^{commit}"],
                                           capture_output=True).returncode == 0


def _commits(repo: Path, spec: str, n: int = 15) -> list[dict]:
    out = _git(repo, "log", f"-n{n}", "--format=%H%x1f%ct%x1f%s", spec)
    return [{"commit": c, "short": c[:7], "date": int(t), "subject": s}
            for c, t, s in (line.split("\x1f", 2) for line in out.splitlines() if line)]


def installed() -> dict:
    """What install.sh recorded: commit, branch, remote, source, mode, committed (a timestamp)."""
    try:
        info = json.loads(INSTALL_INFO.read_text())
    except (OSError, ValueError):
        return {}
    return info if isinstance(info, dict) else {}


def _version() -> str:
    try:
        return version("auren")
    except PackageNotFoundError:
        return ""


def status() -> dict:
    try:
        data = json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(**fields) -> dict:
    """Replace the status the panel shows (installed is always filled in)."""
    info = installed()
    data = {"installed": {"commit": info.get("commit", ""), "short": info.get("commit", "")[:7],
                          "date": info.get("committed", 0), "branch": info.get("branch", ""),
                          "mode": info.get("mode", ""), "version": info.get("version") or _version()},
            "channel": CHANNEL, "log": str(LOG), **fields}
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATUS.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(STATUS)
    return data


@contextlib.contextmanager
def _lock(wait: float = 0):
    """One check or update at a time. Yields False when another still holds it after `wait` seconds."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait
    with open(LOCK, "w") as f:
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    yield False
                    return
                time.sleep(0.5)
        yield True


def _repo(info: dict) -> tuple[Path, bool]:
    """Where the update comes from, and whether that's the --dev checkout itself."""
    if info.get("mode") == "dev":
        return Path(info.get("source", "")), True
    return SOURCE, False


def _fetch(info: dict) -> tuple[Path, bool, str]:
    """Bring the newest CHANNEL commit; returns the repo, whether it's a dev checkout, and the commit."""
    repo, dev = _repo(info)
    if dev:
        if not (repo / ".git").exists():
            raise UpdateError(f"{repo} isn't a git checkout")
    else:
        remote = info.get("remote") or DEFAULT_REMOTE
        if not (repo / ".git").exists():
            if repo.exists():
                subprocess.run(["rm", "-rf", "--", str(repo)], check=False)    # a clone that didn't finish
            repo.parent.mkdir(parents=True, exist_ok=True)
            _git(repo.parent, "clone", "--quiet", "--filter=blob:none", "--no-checkout", remote, repo.name,
                 timeout=300)
        else:
            _git(repo, "remote", "set-url", "origin", remote)
    _git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{CHANNEL}:refs/remotes/origin/{CHANNEL}")
    return repo, dev, _git(repo, "rev-parse", f"origin/{CHANNEL}")


# --- checking ------------------------------------------------------------------------------------

def check(notify: bool = False) -> dict:
    """Look for a newer version; the result is also the panel's status."""
    with _lock() as got:
        if not got or _updating():              # an update (or another check) is running: it reports, and
            return status()                     # a check would hide its progress
        info = installed()
        if not info.get("commit"):
            return _write(state="unknown", checked_at=time.time(),
                          message="This install doesn't know its version: run install.sh from a git checkout once")
        try:
            repo, _, latest = _fetch(info)
            have = info["commit"]
            new = _commits(repo, latest, 1)[0]
            if have == latest:
                return _write(state="current", latest=new, commits=[], count=0, checked_at=time.time())
            if _has(repo, have) and _git(repo, "merge-base", have, latest) == have:
                count = int(_git(repo, "rev-list", "--count", f"{have}..{latest}"))
                result = _write(state="available", latest=new, commits=_commits(repo, f"{have}..{latest}"),
                                count=count, checked_at=time.time())
            else:                               # installed from a branch, or from commits main doesn't have
                result = _write(state="available", latest=new, commits=_commits(repo, latest, 5), count=0,
                                checked_at=time.time(),
                                note=f"Installed from {info.get('branch') or 'a commit'} that isn't on {CHANNEL}: "
                                     f"updating moves it to {CHANNEL}")
            if info.get("dirty") and info.get("mode") != "dev":
                note = "It was installed with uncommitted changes: updating replaces them."
                result = _write(**{**result, "note": " ".join(filter(None, (result.get("note"), note)))})
        except UpdateError as e:
            return _write(state="error", message=str(e), checked_at=time.time())
    if notify:
        _notify_once(result)
    return result


def _notify_once(result: dict) -> None:
    latest = result.get("latest", {}).get("commit", "")
    try:
        if NOTIFIED.read_text().strip() == latest:
            return
    except OSError:
        pass
    n = result.get("count") or 0
    notice.notify("Auren update available",
                  (f"{n} new change{'s' if n != 1 else ''}" if n else "A newer version") + ". Open Auren in the bar to update.",
                  "update", always=True)
    NOTIFIED.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED.write_text(latest)


# --- updating ------------------------------------------------------------------------------------

def start() -> dict:
    """Start the update in its own transient systemd unit and return at once."""
    with _lock() as got:
        if not got or _updating():              # a check is running (try again in a moment), or an update
            return status()
        data = _write(state="updating", step="Starting…", started_at=time.time(), **_kept())
        env = [f"--setenv={k}" for k in PASS_ENV if k in os.environ]    # a name alone: the value from here
        cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit=auren-update-{os.getpid()}-{int(time.time())}",
               "--description=Auren update", *env, sys.executable, "-m", "auren.cli", "update", "run"]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            err = getattr(e, "stderr", "") or str(e)
            return _write(state="failed", message="Couldn't start the update: " + err.strip()[-300:], **_kept())
    return data                                 # the unit waits for the lock this held


def _updating() -> bool:
    s = status()
    return s.get("state") == "updating" and time.time() - s.get("started_at", 0) < UPDATE_TIMEOUT


def _kept() -> dict:
    """What the last check found, kept while updating so the panel can still show it."""
    s = status()
    return {k: s[k] for k in ("latest", "commits", "count", "note") if k in s}


def _log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as f:
        f.write(time.strftime("%H:%M:%S ") + text + "\n")


def _step(text: str) -> None:
    _log(text)
    _write(state="updating", step=text, started_at=status().get("started_at", time.time()), **_kept())


def _systemctl(*args: str) -> int:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True).returncode


def _wait_idle() -> None:
    timeout = IDLE_WAIT
    deadline = time.monotonic() + timeout
    while True:
        try:
            state = json.loads(STATE_FILE.read_text()).get("state", "off")
        except (OSError, ValueError):
            state = "off"
        if state not in BUSY:
            return
        if time.monotonic() > deadline:
            raise UpdateError(f"Auren stayed busy for {int(timeout // 60)} minutes: try again later")
        time.sleep(2)


def _install(repo: Path, dev: bool) -> None:
    """Run install.sh, in its own process group so a timeout stops everything it started (uv, downloads)."""
    _log(f"install.sh{' --dev' if dev else ''} from {repo}")
    with LOG.open("a") as out:
        p = subprocess.Popen(["bash", str(repo / "install.sh"), *(["--dev"] if dev else [])], cwd=repo,
                             env={**os.environ, "AUREN_UPDATE": "1"}, start_new_session=True,
                             stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT)
        try:
            code = p.wait(timeout=INSTALL_TIMEOUT)
        except subprocess.TimeoutExpired as e:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
            raise UpdateError(f"the install took more than {INSTALL_TIMEOUT // 60} minutes") from e
    if code:
        raise UpdateError(f"install.sh failed (exit {code}): see {LOG}")


def _checkout(repo: Path, dev: bool, commit: str) -> None:
    if dev:
        _git(repo, "merge", "--quiet", "--ff-only", commit)
    else:
        _git(repo, "checkout", "--quiet", "--force", "--detach", commit, timeout=300)
        _git(repo, "clean", "-ffdxq")


def _back(repo: Path, dev: bool, commit: str) -> None:
    """Undo _checkout: the dev checkout was clean and only fast-forwarded, so --keep loses nothing."""
    if dev:
        _git(repo, "reset", "--quiet", "--keep", commit)
    else:
        _checkout(repo, dev, commit)


def _dev_ready(repo: Path) -> None:
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != CHANNEL or _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError(f"{repo} is a --dev install: update it yourself (it's on {branch} or has changes)")


def run() -> int:
    """The update itself (in the transient unit that start() made)."""
    with _lock(wait=LOCK_WAIT) as got:
        if not got:
            _write(state="failed", message="A check for updates didn't finish: try again", **_kept())
            return 1
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("")                   # this update's log only
        try:
            message, restart_shell = _update()
        except UpdateError as e:
            _log("failed: " + str(e))
            _write(state="failed", message=str(e), finished_at=time.time(), **_kept())
            return 1
        _log(message)
        _write(state="done", message=message, finished_at=time.time())
    if restart_shell:                          # last: the panel showing this goes with the old shell
        _log("restarting the Omarchy shell for the new bar plugin")
        _restart_shell()
    return 0


def _restart_shell() -> None:
    try:
        subprocess.Popen(["omarchy", "restart", "shell"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass                                   # not on Omarchy: no bar to reload


def _update() -> tuple[str, bool]:
    info = installed()
    before = info.get("commit", "")
    if not before:                             # not knowing what's there, don't replace it
        raise UpdateError("This install doesn't know its version: run install.sh from a git checkout once")
    _step("Looking for the newest version…")
    repo, dev, target = _fetch(info)
    if target == before:
        return "Already up to date", False
    if dev:
        _dev_ready(repo)
    elif not _has(repo, before):               # installed from a commit GitHub doesn't have: get it from
        source = info.get("source", "")        # the checkout it came from, so a failed install can go back
        if source and (Path(source) / ".git").exists():
            with contextlib.suppress(UpdateError):
                _git(repo, "fetch", "--quiet", source, before)
        if not _has(repo, before):
            raise UpdateError(f"If the install failed, Auren couldn't go back to {before[:7]} (it isn't on GitHub): "
                              "update from your checkout instead (git pull && ./install.sh)")
    plugin_changed = subprocess.run(["git", "-C", str(repo), "diff", "--quiet", before, target, "--",
                                     *SHELL_PATHS]).returncode != 0
    _step("Waiting for Auren to finish…")
    _wait_idle()
    running = {u: _systemctl("is-active", "--quiet", u) == 0 for u in UNITS}
    enabled = {u: _systemctl("is-enabled", "--quiet", u) == 0 for u in UNITS}
    _step("Stopping Auren…")
    failure, back_to = None, before
    try:
        if _systemctl("stop", *UNITS):
            raise UpdateError("Couldn't stop Auren, so nothing was changed")
        if dev:
            _dev_ready(repo)                   # again: the wait was long enough to switch branches
            back_to = _git(repo, "rev-parse", "HEAD")    # its commits since the install stay on the branch
        _step("Downloading…")
        _checkout(repo, dev, target)
        _step("Installing…")
        try:
            _install(repo, dev)
        except UpdateError as e:
            _step("The install failed: going back to the version that was there…")
            try:
                _back(repo, dev, back_to)
                _install(repo, dev)
            except UpdateError as again:
                raise UpdateError(f"{e}. Going back failed too ({again}): run ./install.sh in "
                                  f"{info.get('source') or 'your checkout'}") from e
            raise UpdateError(f"{e}. Auren is back on {back_to[:7]}.") from e
    except Exception as e:                     # whatever happened, Auren is started again below
        failure = e if isinstance(e, UpdateError) else UpdateError(f"unexpected error: {e}")
    with contextlib.suppress(OSError):         # a full disk mustn't keep Auren stopped
        _step("Starting Auren…")
    for unit, was in enabled.items():          # an older install.sh enables them
        if not was:
            _systemctl("disable", unit)
    stuck = [u for u, was in running.items()   # as they were; restart: their files changed
             if _systemctl("restart" if was else "stop", u) or (was and not _started(u))]
    trouble = f" {' and '.join(stuck)} didn't start: see its log (journalctl --user -u {stuck[0]})." if stuck else ""
    if failure:
        raise UpdateError(str(failure) + trouble) from failure
    new = _commits(repo, target, 1)[0]
    return f"Updated to {new['short']}: {new['subject']}" + trouble, plugin_changed


def _started(unit: str, timeout: float = 20) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _systemctl("is-active", "--quiet", unit) == 0:
            return True
        time.sleep(1)
    return False


# --- the command ---------------------------------------------------------------------------------

def _ago(t: float) -> str:
    return time.strftime("%d %b %Y", time.localtime(t)) if t else "unknown date"


def main(args: list[str]) -> int:
    cmd = args[0] if args else ""
    if cmd == "check":
        result = check(notify="--notify" in args)
        if "--json" in args:
            print(json.dumps(result))
        else:
            _print(result)
        return 0 if result.get("state") in ("current", "available") else 1
    if cmd == "apply":
        result = start()
        print(result.get("message") or "Updating in the background: see auren update status")
        return 0 if result.get("state") == "updating" else 1
    if cmd == "run":
        return run()
    if cmd == "status":
        print(json.dumps(status(), indent=2))
        return 0
    if cmd:
        print("usage: auren update [check [--json] [--notify] | apply | status]", file=sys.stderr)
        return 2
    result = check()
    _print(result)
    if result.get("state") != "available":
        return 0 if result.get("state") == "current" else 1
    try:
        answer = input("Update now? Auren restarts, which takes about a minute. [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        return 0
    result = start()
    seen = ""
    while result.get("state") == "updating":
        if result.get("step") != seen:
            seen = result.get("step", "")
            print("  " + seen)
        time.sleep(1)
        result = status()
    print(result.get("message", ""))
    return 0 if result.get("state") == "done" else 1


def _print(result: dict) -> None:
    have = result.get("installed", {})
    print(f"Installed: {have.get('version', '')} {have.get('short') or '(unknown)'} ({_ago(have.get('date', 0))})")
    state = result.get("state")
    if state == "current":
        print(f"Up to date with {CHANNEL}.")
    elif state == "available":
        n = result.get("count") or 0
        print(f"Update available on {CHANNEL}" + (f": {n} new change{'s' if n != 1 else ''}" if n else ""))
        if result.get("note"):
            print("  " + result["note"])
        for c in result.get("commits", []):
            print(f"  {c['short']}  {c['subject']}")
    else:
        print(result.get("message", "Couldn't check for updates"))
