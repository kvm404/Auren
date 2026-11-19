"""Updating Auren: find a newer version on GitHub and install it.

Updates install only the latest published, non-prerelease GitHub release, resolved through its tag.
Unreleased commits and standalone tags are never offered. The code comes from Auren's own clone
in ~/.cache/auren/source, so a checkout you work in is never touched. The exception is an install made with `install.sh --dev`, which
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

The Arch package (yay -S auren) is pacman's to update: the check is the same, against the version
installed, but updating opens a terminal that waits until Auren is idle, stops it, runs `yay -S
--needed auren` (it asks for your password there), and starts Auren again as it was. Updating the
whole system (yay) updates Auren too; then restart it: systemctl --user restart auren auren-mascot.
An install.sh install still updates itself, and after updating says once that the package exists.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from . import config, notice
from .config import CACHE_DIR, DATA_HOME, RUNTIME_DIR, STATE_DIR, STATE_FILE

CHANNEL = "releases"
DEV_BRANCH = "main"
DEFAULT_REMOTE = "https://github.com/kvm404/Auren.git"
INSTALL_INFO = DATA_HOME / "install.json"          # written by install.sh: what's installed, from where
SOURCE = CACHE_DIR / "source"                      # Auren's own clone, what updates install from
STATUS = RUNTIME_DIR / "update.json"
LOG = STATE_DIR / "update.log"
NOTIFIED = STATE_DIR / "update-notified"           # the newest version a notification was sent for
LOCK = RUNTIME_DIR / "update.lock"
SUGGESTED = STATE_DIR / "package-suggested"      # the package was suggested once, after a self-update
UNITS = ("auren", "auren-mascot")
INSTALLER = "install-macos.sh" if sys.platform == "darwin" else "install.sh"
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
    if config.PACKAGED:                          # pacman may have updated it since setup wrote install.json
        info = {"mode": "package", "version": _version()}
    data = {"installed": {"commit": info.get("commit", ""), "short": info.get("commit", "")[:7],
                          "date": info.get("committed", 0), "branch": info.get("branch", ""),
                          "mode": info.get("mode", ""), "tag": info.get("tag", ""), "version": info.get("version") or _version()},
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


def _github_repo(remote: str) -> str:
    """Accept GitHub HTTPS/SSH remotes, never send credentials to another host."""
    match = re.fullmatch(r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([\w.-]+/[\w.-]+?)(?:\.git)?/?", remote)
    if not match:
        raise UpdateError("Release updates need a github.com origin remote")
    return match[1]


def _latest_release(remote: str) -> dict:
    repo = _github_repo(remote)
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token and shutil.which("gh"):
        try:
            auth = subprocess.run(["gh", "auth", "token", "--hostname", "github.com"],
                                  capture_output=True, text=True, timeout=5)
            if auth.returncode == 0:
                token = auth.stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            pass
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "auren-updater"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(f"https://api.github.com/repos/{repo}/releases/latest", headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            release = json.load(response)
    except HTTPError as e:
        if e.code == 404:
            raise UpdateError("No published release is available. If the repository is private, sign in with "
                              "gh auth login or set GH_TOKEN.") from e
        if e.code in (401, 403, 429):
            raise UpdateError("GitHub refused the release check: check your login or API rate limit and try again later") from e
        raise UpdateError(f"GitHub's release check failed (HTTP {e.code})") from e
    except (OSError, URLError, ValueError) as e:
        raise UpdateError("Couldn't read the latest GitHub release: check your connection and try again") from e
    if not isinstance(release, dict) or release.get("draft") is not False or release.get("prerelease") is not False:
        raise UpdateError("GitHub didn't return a published stable release")
    tag = release.get("tag_name", "")
    if not isinstance(tag, str) or not re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+", tag):
        raise UpdateError("The latest release needs a version tag such as v0.1.0")
    return {"tag": tag, "name": release.get("name") or tag,
            "url": f"https://github.com/{repo}/releases/tag/{tag}"}


def _fetch(info: dict) -> tuple[Path, bool, str, dict]:
    """Resolve a published release, then fetch exactly its tag, never a branch tip."""
    remote = info.get("remote") or DEFAULT_REMOTE
    release = _latest_release(remote)
    repo, dev = _repo(info)
    if dev:
        if not (repo / ".git").exists():
            raise UpdateError(f"{repo} isn't a git checkout")
    else:
        if not (repo / ".git").exists():
            if repo.exists():
                subprocess.run(["rm", "-rf", "--", str(repo)], check=False)
            repo.parent.mkdir(parents=True, exist_ok=True)
            _git(repo.parent, "clone", "--quiet", "--filter=blob:none", "--no-checkout", "--no-tags", remote, repo.name,
                 timeout=300)
        else:
            _git(repo, "remote", "set-url", "origin", remote)
    # A dev checkout's origin may have changed since install. Use the recorded release remote directly.
    # No force: replacing a release tag is refused instead of silently changing a published version.
    ref = f"refs/tags/{release['tag']}"
    _git(repo, "fetch", "--quiet", "--no-tags", remote, f"{ref}:{ref}")
    target = _git(repo, "rev-parse", f"{ref}^{{commit}}")
    before = info.get("commit", "")
    if before and info.get("tag") == release["tag"] and target != before:
        raise UpdateError(f"Release tag {release['tag']} changed since it was installed: refusing the update")
    # A copied install may come from an unpushed local commit. Recover its graph before either check()
    # or run() decides whether a release is newer; recovering it only for rollback can hide a downgrade.
    if not dev and before and not _has(repo, before):
        source = info.get("source", "")
        if source and (Path(source) / ".git").exists():
            with contextlib.suppress(UpdateError):
                _git(repo, "fetch", "--quiet", "--no-tags", source, before)
    return repo, dev, target, release


def _contains(repo: Path, have: str, target: str) -> bool:
    """A checkout ahead of a release must never be downgraded by the updater."""
    return have == target or (_has(repo, have) and subprocess.run(
        ["git", "-C", str(repo), "merge-base", "--is-ancestor", target, have], capture_output=True).returncode == 0)


# --- checking ------------------------------------------------------------------------------------

def check(notify: bool = False) -> dict:
    """Look for a newer version; the result is also the panel's status."""
    with _lock() as got:
        if not got or _updating():              # an update (or another check) is running: it reports, and
            return status()                     # a check would hide its progress
        if config.PACKAGED:
            result = _check_package()
            if notify and result.get("state") == "available":
                _notify_once(result)
            return result
        info = installed()
        if not info.get("commit"):
            return _write(state="unknown", checked_at=time.time(),
                          message=f"This install doesn't know its version: run {INSTALLER} from a git checkout once")
        try:
            repo, _, latest, release = _fetch(info)
            have = info["commit"]
            new = {**_commits(repo, latest, 1)[0], **release}
            if _contains(repo, have, latest):
                return _write(state="current", latest=new, commits=[], count=0, checked_at=time.time())
            if _has(repo, have) and _contains(repo, latest, have):
                count = int(_git(repo, "rev-list", "--count", f"{have}..{latest}"))
                result = _write(state="available", latest=new, commits=_commits(repo, f"{have}..{latest}"),
                                count=count, checked_at=time.time())
            else:                               # installed from outside the release history
                result = _write(state="available", latest=new, commits=_commits(repo, latest, 5), count=0,
                                checked_at=time.time(),
                                note=f"This install is outside the release history: updating moves it to {release['tag']}")
            if info.get("dirty") and info.get("mode") != "dev":
                note = "It was installed with uncommitted changes: updating replaces them."
                result = _write(**{**result, "note": " ".join(filter(None, (result.get("note"), note)))})
        except UpdateError as e:
            return _write(state="error", message=str(e), checked_at=time.time())
    if notify:
        _notify_once(result)
    return result


def _numbers(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v.lstrip("v").split("-")[0].split("+")[0]))


AUR_INFO = "https://aur.archlinux.org/rpc/v5/info?arg[]={}"


def _aur_version() -> str:
    """The version the AUR has now (what yay would install), like 0.1.6-1; '' if it can't be read."""
    request = Request(AUR_INFO.format(config.PACKAGE_NAME), headers={"User-Agent": "auren-updater"})
    try:
        with urlopen(request, timeout=20) as response:
            results = json.load(response).get("results") or []
    except (OSError, URLError, ValueError, AttributeError):
        return ""
    version = results[0].get("Version", "") if results and isinstance(results[0], dict) else ""
    return version if isinstance(version, str) else ""


def _newer(a: str, b: str) -> bool:
    """Whether package version a is newer than b, the way pacman compares them."""
    try:
        out = subprocess.run(["vercmp", a, b], capture_output=True, text=True, timeout=5).stdout.strip()
        return int(out) > 0
    except (OSError, subprocess.TimeoutExpired, ValueError):          # no pacman: the version, then pkgrel
        def key(v: str):
            ver, _, rel = v.rpartition("-") if "-" in v else (v, "", "0")
            return _numbers(ver), _numbers(rel)
        return key(a) > key(b)


def _check_package() -> dict:
    """The installed package against the AUR's (what an update would install), with the GitHub release for
    its notes. A release on GitHub that hasn't reached the AUR yet isn't offered."""
    have, aur = _package_version() or _version(), _aur_version()
    try:
        release = _latest_release(DEFAULT_REMOTE)
    except UpdateError as e:
        if not aur:
            return _write(state="error", message=str(e), checked_at=time.time())
        release = {"tag": "v" + aur.split("-")[0], "name": f"Auren {aur}", "url": "", "notes": ""}
    latest = {**release, "commit": release["tag"], "short": release["tag"], "subject": release["name"], "date": 0}
    available = _newer(aur, have) if aur and have else not have or _numbers(release["tag"]) > _numbers(have)
    if not available:
        return _write(state="current", latest=latest, commits=[], count=0, checked_at=time.time())
    return _write(state="available", latest=latest, commits=[], count=0, checked_at=time.time(),
                  note=f"Installed with pacman: updating runs yay -S {config.PACKAGE_NAME} in a terminal.")


def _notify_once(result: dict) -> None:
    latest = result.get("latest", {}).get("commit", "")
    try:
        if NOTIFIED.read_text().strip() == latest:
            return
    except OSError:
        pass
    n = result.get("count") or 0
    notice.notify("Auren update available",
                  (result.get("latest", {}).get("tag") or (f"{n} new changes" if n else "A newer release")) + ". Open Auren in the bar to update.",
                  "update", always=True)
    NOTIFIED.parent.mkdir(parents=True, exist_ok=True)
    NOTIFIED.write_text(latest)


# --- updating ------------------------------------------------------------------------------------

def start() -> dict:
    """Start the update in its own transient systemd unit and return at once."""
    if config.PACKAGED:
        return _start_terminal()
    with _lock() as got:
        if not got or _updating():              # a check is running (try again in a moment), or an update
            return status()
        data = _write(state="updating", step="Starting…", started_at=time.time(), **_kept())
        if sys.platform == "darwin":               # no systemd: a process of its own, outside Auren's launch agent
            from .mac.update import spawn
            return spawn(data, _write, _kept)
        env = [f"--setenv={k}" for k in PASS_ENV if k in os.environ]    # a name alone: the value from here
        cmd = ["systemd-run", "--user", "--collect", "--quiet", f"--unit=auren-update-{os.getpid()}-{int(time.time())}",
               "--description=Auren update", *env, sys.executable, "-m", "auren.cli", "update", "run"]
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as e:
            err = getattr(e, "stderr", "") or str(e)
            return _write(state="failed", message="Couldn't start the update: " + err.strip()[-300:], **_kept())
    return data                                 # the unit waits for the lock this held


def _start_terminal() -> dict:
    """The package: pacman needs a password, so the update runs in a terminal (auren update pacman)."""
    if _updating():
        return status()
    command = f"{config.PACKAGE_BIN / 'auren'} update pacman"
    launcher = shutil.which("omarchy-launch-floating-terminal-with-presentation")
    cmd = [launcher, command] if launcher else ["xdg-terminal-exec", "-e", "sh", "-c", command]
    try:
        subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    except OSError as e:
        return _write(state="failed", message=f"Couldn't open a terminal ({e}): run auren update in one", **_kept())
    return {**status(), "terminal": True, "message": "Updating in a terminal: it asks for your password there"}


def _share_files() -> dict[str, tuple[int, int]]:
    """The package's QML (the bar plugin and the mascot), to tell whether the shell needs a restart."""
    out = {}
    for path in sorted(config.PACKAGE_SHARE.rglob("*")):
        if path.is_file():
            st = path.stat()
            out[str(path)] = (st.st_size, st.st_mtime_ns)
    return out


def _package_version() -> str:
    try:
        out = subprocess.run(["pacman", "-Q", config.PACKAGE_NAME], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return out.split()[1] if len(out.split()) == 2 else ""


def run_package() -> int:
    """The package's update, in a terminal: wait until idle, stop Auren, yay, start it again as it was."""
    helper = shutil.which("yay") or shutil.which("paru")
    if not helper:
        print(f"Updating the package needs yay or paru (or: sudo pacman -U a built {config.PACKAGE_NAME} package)")
        return 1
    with _lock(wait=LOCK_WAIT) as got:
        if not got:
            print("A check for updates is running: try again in a moment")
            return 1
        LOG.parent.mkdir(parents=True, exist_ok=True)
        LOG.write_text("")
        _write(state="updating", step="Starting…", started_at=time.time(), **_kept())

        def step(text: str) -> None:
            with contextlib.suppress(OSError):    # the terminal may be gone: carry on regardless
                print(text, flush=True)
            _step(text)

        before, shell_before = _package_version(), _share_files()
        try:
            step("Waiting for Auren to finish…")
            _wait_idle()
        except UpdateError as e:
            _write(state="failed", message=str(e), finished_at=time.time(), **_kept())
            print(e)
            return 1
        running = {u: _systemctl("is-active", "--quiet", u) == 0 for u in UNITS}
        # Closing the terminal mustn't leave Auren stopped: from here on a hangup is ignored (the helper
        # gets its own, and fails), and Auren is started again whatever happens.
        hup = signal.signal(signal.SIGHUP, signal.SIG_IGN)
        code, stopped = 1, False
        try:
            step("Stopping Auren…")
            stopped = _systemctl("stop", *UNITS) == 0
            if stopped:
                step(f"Updating with {Path(helper).name}…")
                try:
                    code = subprocess.run([helper, "-S", "--needed", config.PACKAGE_NAME]).returncode
                except (OSError, KeyboardInterrupt):
                    code = 1
        finally:
            step("Starting Auren…")
            stuck = [u for u, was in running.items() if was and (_systemctl("restart", u) or not _started(u))]
            signal.signal(signal.SIGHUP, hup)
        trouble = f" {' and '.join(stuck)} didn't start: journalctl --user -u {stuck[0]}" if stuck else ""
        if not stopped:
            message = "Auren didn't stop, so nothing was updated: try again when it's idle." + trouble
            _write(state="failed", message=message, finished_at=time.time(), **_kept())
            print(message)
            return 1
        after = _package_version()
        if code:
            message = f"{Path(helper).name} didn't finish (exit {code}): Auren is as it was.{trouble}"
            _write(state="failed", message=message, finished_at=time.time(), **_kept())
            print(message)
            return 1
        message = (f"Updated to {after}" if after != before else f"Already up to date ({after})") + trouble
        _write(state="done", message=message, finished_at=time.time())
        print(message)
    if _share_files() != shell_before:
        print("Restarting the Omarchy shell for the new bar plugin")
        _restart_shell()
    return 0


def _suggest_package() -> None:
    """Once, after an install.sh install updated itself: there's a package now. Only on Omarchy (a Mac
    keeps updating itself), and only once the AUR has the package, so yay can find it."""
    if sys.platform == "darwin" or SUGGESTED.exists() or not _aur_version():
        return
    notice.notify("Auren is now an Arch package",
                  f"Switch with yay -S {config.PACKAGE_NAME}, then run /usr/bin/auren setup: your settings, talk "
                  "keys and voice model are kept.", "update", always=True)
    SUGGESTED.parent.mkdir(parents=True, exist_ok=True)
    SUGGESTED.write_text(str(int(time.time())))


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
    if sys.platform == "darwin":
        from .mac.update import launchctl_like
        return launchctl_like(*args)
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
        script = INSTALLER if (repo / INSTALLER).exists() else "install.sh"
        p = subprocess.Popen(["bash", str(repo / script), *(["--dev"] if dev else [])], cwd=repo,
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


def _dev_ready(repo: Path, target: str) -> None:
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    if branch != DEV_BRANCH or _git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise UpdateError(f"{repo} is a --dev install: update it yourself (it's on {branch} or has changes)")
    if subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "HEAD", target],
                      capture_output=True).returncode != 0:
        raise UpdateError(f"{repo} is a --dev install ahead of or divergent from the release: update it yourself")


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
        if message.startswith("Updated") and installed().get("mode") == "install":   # it did update
            with contextlib.suppress(OSError):
                _suggest_package()
    if restart_shell:                          # last: the panel showing this goes with the old shell
        _log("restarting the Omarchy shell for the new bar plugin")
        _restart_shell()
    return 0


def _restart_shell() -> None:
    if sys.platform == "darwin":
        return                                 # the mascot (and its menu) restarted with the services
    try:
        subprocess.Popen(["omarchy", "restart", "shell"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass                                   # not on Omarchy: no bar to reload


def _update() -> tuple[str, bool]:
    info = installed()
    before = info.get("commit", "")
    if not before:                             # not knowing what's there, don't replace it
        raise UpdateError(f"This install doesn't know its version: run {INSTALLER} from a git checkout once")
    _step("Looking for the newest version…")
    repo, dev, target, release = _fetch(info)
    if _contains(repo, before, target):
        return "Already up to date", False
    if dev:
        _dev_ready(repo, target)
    elif not _has(repo, before):               # _fetch couldn't recover the installed commit for rollback
        raise UpdateError(f"If the install failed, Auren couldn't go back to {before[:7]} (it isn't on GitHub): "
                          f"update from your checkout instead (git pull && ./{INSTALLER})")
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
            _dev_ready(repo, target)           # again: the wait was long enough to switch branches
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
                raise UpdateError(f"{e}. Going back failed too ({again}): run ./{INSTALLER} in "
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
    where = ("~/.local/state/auren/" + ("daemon" if stuck[0] == "auren" else "mascot") + ".out.log"
             if sys.platform == "darwin" else f"journalctl --user -u {stuck[0]}") if stuck else ""
    trouble = f" {' and '.join(stuck)} didn't start: see its log ({where})." if stuck else ""
    if failure:
        raise UpdateError(str(failure) + trouble) from failure
    new = _commits(repo, target, 1)[0]
    return f"Updated to {release['tag']} ({new['short']}): {new['subject']}" + trouble, plugin_changed


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
        return 0 if result.get("state") == "updating" or result.get("terminal") else 1
    if cmd == "run":
        return run()
    if cmd == "pacman":
        return run_package()
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
        answer = input("Update now? Auren restarts, which takes about a minute"
                       + (" (yay asks for your password)" if config.PACKAGED else "") + ". [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in ("y", "yes"):
        return 0
    if config.PACKAGED:                          # in this terminal: yay asks for the password here
        return run_package()
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
    if have.get("mode") == "package" or config.PACKAGED:      # pacman knows the version, not a commit
        print(f"Installed: {have.get('version', '')} (Arch package)")
    else:
        print(f"Installed: {have.get('version', '')} {have.get('short') or '(unknown)'} ({_ago(have.get('date', 0))})")
    state = result.get("state")
    if state == "current":
        print("Up to date with published releases.")
    elif state == "available":
        n = result.get("count") or 0
        print(f"Release {result.get('latest', {}).get('tag', '')} available" + (f": {n} new change{'s' if n != 1 else ''}" if n else ""))
        if result.get("note"):
            print("  " + result["note"])
        for c in result.get("commits", []):
            print(f"  {c['short']}  {c['subject']}")
    else:
        print(result.get("message", "Couldn't check for updates"))
