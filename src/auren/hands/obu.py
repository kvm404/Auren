"""Open Browser Use: finding it, setting it up, and finding the browser it connects to.

How it works: the Open Browser Use extension in a browser profile starts a native host (the
`open-browser-use` binary, registered with the browser in a native messaging manifest). The host listens
on a socket with a random name, in /tmp/open-browser-use unless its manifest's script names another
folder. Every profile with the extension gets its own host and socket, and nothing in the socket's name
says which browser or profile it is.

So Auren looks at every live socket:
- it asks the host for the extension's instance id (`info`), and finds the profiles whose extension storage
  holds that id (Open Browser Use does the same, for Chrome only);
- the host's parent process names the browser. That matters: a profile imported or copied from another
  browser carries the extension's storage, so the same id can be in a Brave and a Helium profile;
- of that browser's profiles holding the id, the one whose storage changed last; if none holds it and only
  one of its profiles has the extension, that one. Without a parent to go by, only running browsers count,
  and a socket that still can't be told apart is left unused rather than mislabelled.
Then every command goes to that exact socket (`--socket`), so two browsers or two profiles never get mixed up,
whichever socket folders the user's hosts use.

Setup (`auren browser setup`, the installer, or the brain's browser_setup) puts a pinned, checksummed
copy of the binary in Auren's data folder, registers it with each chosen browser that has no working
host yet (a manifest that already works is left alone), and opens the extension's Web Store page.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import http.client
import io
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import browsers

EXTENSION_ID = "bgjoihaepiejlfjinojjfgokghnodnhd"
HOST_NAME = "com.ifuryst.open_browser_use.extension"
STORE_URL = f"https://chromewebstore.google.com/detail/{EXTENSION_ID}"
DEFAULT_SOCKETS = browsers.DEFAULT_SOCKETS

# The binary Auren installs: a release of github.com/iFurySt/open-browser-use, checked against these hashes.
VERSION = "1.1.0"
RELEASE = "https://github.com/iFurySt/open-browser-use/releases/download/v{v}/open-browser-use-cli-{v}-linux-{arch}.tar.gz"
SHA256 = {
    "amd64": "9fdc551631806ef80f0cd219c50f2849e67430a085affac2741de2009a724039",
    "arm64": "fc1095d5d5d80a13fe2d0934c519f59bd7d98f710d561b4424e86a308ec74a39",
}

CACHE_FOR = 3.0         # seconds a scan of the sockets is reused


class SetupError(RuntimeError):
    pass


class UnknownProfile(browsers.UnknownBrowser):
    pass


# --- the binary -----------------------------------------------------------------------------------

def _data_home() -> Path:
    from .. import config
    return config.DATA_HOME


def own_binary() -> Path:
    """Where Auren keeps its own copy: a stable path for the manifests it writes."""
    return _data_home() / "obu" / "open-browser-use"


def cli() -> str | None:
    """The open-browser-use command to run: Auren's own copy, else one on PATH."""
    own = own_binary()
    if os.access(own, os.X_OK):
        return str(own)
    return shutil.which("open-browser-use") or shutil.which("obu")


def installed_version() -> str | None:
    """The version of Auren's own copy, if it has one."""
    try:
        return (own_binary().parent / "VERSION").read_text().strip()
    except OSError:
        return None


def _arch() -> str:
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise SetupError(f"Open Browser Use has no Linux build for {machine}")


def _fetch(url: str) -> bytes:
    # 15 s per step: GitHub's download host has several addresses, and one that doesn't answer from some
    # networks would otherwise hold the setup for a minute before the next is tried.
    with urllib.request.urlopen(url, timeout=15) as r:       # noqa: S310 - a fixed https URL
        return r.read()


def install_binary() -> Path:
    """Download the pinned release into Auren's data folder (once), checking its hash."""
    target = own_binary()
    if os.access(target, os.X_OK) and installed_version() == VERSION:
        return target
    arch = _arch()
    url = RELEASE.format(v=VERSION, arch=arch)
    try:
        data = _fetch(url)
    except (OSError, http.client.HTTPException) as e:
        raise SetupError(f"couldn't download Open Browser Use ({e})") from None
    digest = hashlib.sha256(data).hexdigest()
    if digest != SHA256[arch]:
        raise SetupError(f"the Open Browser Use download didn't match its checksum (got {digest[:12]}…): not installed")
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            member = next((m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "open-browser-use"), None)
            if member is None:
                raise SetupError("the Open Browser Use download has no open-browser-use binary in it")
            binary = tar.extractfile(member).read()
    except tarfile.TarError as e:
        raise SetupError(f"the Open Browser Use download is broken ({e})") from None
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".open-browser-use.")     # two setups at once don't collide
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(binary)
        os.chmod(tmp, 0o755)
        os.replace(tmp, target)             # a running host keeps its old file; new ones start this one
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    (target.parent / "VERSION").write_text(VERSION + "\n")
    return target


# --- native messaging hosts -----------------------------------------------------------------------

def manifest_path(kind: browsers.Kind) -> Path:
    base = browsers.config_dir(kind) or (browsers.config_home() / kind.config_dirs[0])
    return base / "NativeMessagingHosts" / f"{HOST_NAME}.json"


def host_of(kind: browsers.Kind) -> Path | None:
    """The host program the browser's manifest names, if the manifest is there."""
    try:
        data = json.loads(manifest_path(kind).read_text())
        return Path(data["path"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def registered(kind: browsers.Kind) -> bool:
    """The browser has a manifest for Open Browser Use whose program exists and runs."""
    host = host_of(kind)
    return host is not None and os.access(host, os.X_OK)


def register(kind: browsers.Kind, binary: Path) -> bool:
    """Write the browser's manifest, pointing at `binary`. Leaves a working one alone; returns True if written."""
    if registered(kind):
        return False
    path = manifest_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"name": HOST_NAME, "description": "Open Browser Use native messaging host (set up by Auren)",
                "path": str(binary), "type": "stdio", "allowed_origins": [f"chrome-extension://{EXTENSION_ID}/"]}
    tmp = path.with_name(path.name + ".auren-tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n")
    os.replace(tmp, path)
    return True


def socket_dirs() -> list[str]:
    """Every folder a host may listen in: the default, /tmp/open-browser-use-*, ones named in config,
    and ones the manifests' host scripts pass with --socket-dir."""
    from ..settings import current as settings
    dirs = [DEFAULT_SOCKETS, *sorted(glob.glob(DEFAULT_SOCKETS + "-*"))]
    dirs += list(settings()["browser.sockets"].values())
    for kind in browsers.KINDS:
        host = host_of(kind)
        if host is None or not host.is_file():
            continue
        try:
            head = host.read_bytes()[:4096]
        except OSError:
            continue
        if head.startswith(b"#!"):
            dirs += re.findall(r"--socket-dir[= ]+[\"']?([^\s\"']+)", head.decode(errors="replace"))
    seen: list[str] = []
    for d in dirs:
        d = os.path.expanduser(os.path.expandvars(d))
        if d not in seen and "$" not in d:          # a variable this process doesn't have can't be looked in
            seen.append(d)
    return seen


# --- profiles and the extension ---------------------------------------------------------------------

@dataclass(frozen=True)
class Profile:
    kind: browsers.Kind
    dir: str            # its folder: Default, Profile 1, ...
    name: str           # what the user calls it (the avatar menu's name)

    @property
    def path(self) -> Path:
        return browsers.config_dir(self.kind) / self.dir      # type: ignore[operator]

    def label(self) -> str:
        return self.name if self.name == self.dir else f"{self.name} ({self.dir})"


def profiles(kind: browsers.Kind) -> list[Profile]:
    base = browsers.config_dir(kind)
    if base is None:
        return []
    try:
        cache = json.loads((base / "Local State").read_text())["profile"]["info_cache"]
    except (OSError, ValueError, KeyError, TypeError):
        cache = {}
    out = [Profile(kind, d, str(info.get("name") or d)) for d, info in cache.items() if (base / d).is_dir()]
    if not out and (base / "Default").is_dir():
        out = [Profile(kind, "Default", "Default")]
    return out


def has_extension(p: Profile) -> bool:
    """The extension is installed in this profile and not turned off."""
    if not (p.path / "Extensions" / EXTENSION_ID).is_dir():
        return False
    for prefs in ("Secure Preferences", "Preferences"):
        try:
            entry = json.loads((p.path / prefs).read_text())["extensions"]["settings"][EXTENSION_ID]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if entry.get("disable_reasons") or entry.get("state") == 0:
            return False
        return True
    return True


def with_extension(kind: browsers.Kind) -> list[Profile]:
    return [p for p in profiles(kind) if has_extension(p)]


def _holds(p: Profile, instance: str) -> bool:
    needle = instance.encode()
    for sub in ("Local Extension Settings", "Sync Extension Settings"):
        for f in glob.glob(str(p.path / sub / EXTENSION_ID / "*")):
            try:
                with open(f, "rb") as fh:
                    if needle in fh.read():
                        return True
            except OSError:
                pass
    return False


# --- connections ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Conn:
    """One live host: the socket, and the browser and profile behind it (profile None if it can't be told)."""
    socket: str
    kind: browsers.Kind
    profile: Profile | None
    instance: str
    since: float          # when the socket appeared

    def label(self) -> str:
        return self.kind.name + (f", profile {self.profile.label()}" if self.profile else "")


_scan: tuple[float, list[Conn]] | None = None
_known: dict[str, tuple[str, str | None]] = {}        # socket -> (browser key, profile folder), for its life


def run(binary: str, *args: str, timeout: float = 10) -> dict:
    """Run open-browser-use and return its parsed JSON reply (an "error" member if it failed)."""
    try:
        p = subprocess.run([binary, *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"error": {"message": f"no answer within {timeout:.0f}s"}}
    except OSError as e:
        return {"error": {"message": str(e)}}
    out = (p.stdout or "").strip()
    try:
        d = json.loads(out) if out else {}
    except json.JSONDecodeError:
        d = {}
    if p.returncode != 0 and "error" not in d:
        d = {"error": {"message": (p.stderr or out or f"exit {p.returncode}").strip()}}
    return d if isinstance(d, dict) else {"result": d}


def _instance(binary: str, sock: str) -> str | None:
    d = run(binary, "info", "--socket", sock, "--session-id", "auren-probe", "--timeout", "3s", timeout=6)
    try:
        return str(d["result"]["metadata"]["extensionInstanceId"])
    except (KeyError, TypeError):
        return None


def _listener_pid(sock: str) -> int | None:
    try:
        out = subprocess.run(["ss", "-xlpnH"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in out.splitlines():
        if f" {sock} " in f" {line} ":
            m = re.search(r"pid=(\d+)", line)
            if m:
                return int(m.group(1))
    return None


def _parent(pid: int) -> int | None:
    try:
        stat_line = Path(f"/proc/{pid}/stat").read_text()
        return int(stat_line.rsplit(")", 1)[1].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def _browser_above(pid: int | None) -> tuple[browsers.Kind, bool] | None:
    """The browser that started a host: the first ancestor whose program is one we know, and whether it
    runs with its own --user-data-dir (a separate browser, not the user's profiles)."""
    for _ in range(4):
        pid = _parent(pid) if pid else None
        if not pid or pid <= 1:
            return None
        try:
            exe = Path(os.readlink(f"/proc/{pid}/exe")).name
        except OSError:
            continue
        kind = next((k for k in browsers.KINDS if exe in k.processes or exe in k.commands), None)
        if kind:
            return kind, _own_data_dir(pid, kind)
    return None


def _cmdline(pid: int) -> list[bytes]:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    except OSError:
        return []


def _own_data_dir(pid: int, kind: browsers.Kind) -> bool:
    for a in _cmdline(pid):
        if a.startswith(b"--user-data-dir="):
            given = os.path.realpath(os.path.expanduser(a.split(b"=", 1)[1].decode(errors="replace")))
            base = browsers.config_dir(kind)
            return base is None or given != os.path.realpath(base)
    return False


def _storage_time(p: Profile) -> float:
    """When the profile's extension storage last changed (a connecting extension writes to it)."""
    times = [os.stat(f).st_mtime for f in glob.glob(str(p.path / "Local Extension Settings" / EXTENSION_ID / "*"))
             if os.path.isfile(f)]
    return max(times, default=0.0)


def _identify(sock: str, instance: str) -> tuple[browsers.Kind, Profile | None] | None:
    """The browser and profile behind a host. The instance id can be in more than one profile: a profile
    imported or copied from another browser brings the extension's storage along, id and all. So every
    profile holding it is a candidate, and the browser that started the host decides between them."""
    if sock in _known:
        key, folder = _known[sock]
        kind = browsers.BY_KEY[key]
        prof = next((p for p in profiles(kind) if p.dir == folder), None) if folder else None
        return kind, prof
    holders = [p for kind in browsers.KINDS for p in profiles(kind) if _holds(p, instance)]
    above = _browser_above(_listener_pid(sock))
    if above is not None and above[1]:
        return None                     # a browser with its own data folder: none of the profiles Auren knows
    started_by = above[0] if above else None
    if started_by is not None:
        mine = [p for p in holders if p.kind == started_by]
        if not mine:                    # its storage doesn't name it yet: one profile with the extension is it
            ext = with_extension(started_by)
            mine = ext if len(ext) == 1 else []
        kind = started_by
    else:
        running = [p for p in holders if browsers.running(p.kind)]
        mine = running or holders
        kinds = {p.kind for p in mine}
        if len(kinds) != 1:
            return None                 # can't tell which browser this is: better unused than mislabelled
        kind = kinds.pop()
    prof = max(mine, key=_storage_time) if mine else None
    if prof is not None:                # an unknown profile is looked at again next time, not remembered
        _known[sock] = (kind.key, prof.dir)
    return kind, prof


def _sockets() -> list[str]:
    out = []
    uid = os.getuid()
    for d in socket_dirs():
        for f in glob.glob(os.path.join(d, "*.sock")):
            try:
                st = os.stat(f)
            except OSError:
                continue
            if stat.S_ISSOCK(st.st_mode) and st.st_uid == uid:
                out.append(f)
    return out


_lock = threading.Lock()        # tools run in parallel threads: one scan at a time


def connections(fresh: bool = False) -> list[Conn]:
    """Every live host, newest first: at most one per browser and profile (the newest)."""
    with _lock:
        return _connections(fresh)


def _connections(fresh: bool) -> list[Conn]:
    global _scan
    if not fresh and _scan and time.monotonic() - _scan[0] < CACHE_FOR:
        return _scan[1]
    binary = cli()
    found: list[Conn] = []
    if binary:
        for sock in _sockets():
            instance = _instance(binary, sock)
            if not instance:
                continue
            # Not skipped when another socket has the same id: a copied profile in another browser shares it.
            who = _identify(sock, instance)
            if who is None:
                continue
            try:
                since = os.stat(sock).st_mtime
            except OSError:
                continue
            found.append(Conn(sock, who[0], who[1], instance, since))
    found.sort(key=lambda c: -c.since)
    seen: set[tuple] = set()
    unique = []
    for c in found:
        key = (c.kind.key, c.profile.dir if c.profile else c.socket)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    found = unique
    live = {c.socket for c in found}
    for sock in [s for s in _known if s not in live]:
        del _known[sock]
    _scan = (time.monotonic(), found)
    return found


def matches(c: Conn, kind: browsers.Kind, profile: Profile | None) -> bool:
    """The connection is that browser, and that profile when one is wanted (an unknown profile isn't it)."""
    return c.kind == kind and (profile is None or (c.profile is not None and c.profile.dir == profile.dir))


def forget_scan() -> None:
    global _scan
    _scan = None


# --- choosing ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Target:
    """The browser and profile to use. conn is set when it's connected now."""
    kind: browsers.Kind
    profile: Profile | None
    conn: Conn | None = None

    def label(self) -> str:
        if self.conn:
            return self.conn.label()
        return self.kind.name + (f", profile {self.profile.label()}" if self.profile else "")


def find_profile(kind: browsers.Kind, want: str) -> Profile | None:
    w = want.strip().casefold()
    return next((p for p in profiles(kind) if w in (p.dir.casefold(), p.name.casefold())), None)


def _last_used(kind: browsers.Kind) -> str | None:
    base = browsers.config_dir(kind)
    try:
        return json.loads((base / "Local State").read_text())["profile"]["last_used"]      # type: ignore[operator]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def ready(kind: browsers.Kind) -> bool:
    """Set up in this browser: it's installed, its host is registered, and a profile has the extension."""
    return not_set_up(kind) is None


def choose(name: str | None = None, profile: str | None = None) -> Target:
    """Which browser and profile the browser tools use.

    A browser (named by the brain or browser.name) and a profile (named by browser.profile, or the caller)
    are kept to; a profile that doesn't exist is an error, not ignored.
    Otherwise: a connected one, preferring the default browser, then its last-used profile, then the
    newest; if none is connected, the default browser if it's set up, else the first that is.
    """
    from ..settings import current as settings
    configured = str(settings()["browser.name"]).lower()
    want_kind = browsers.resolve(name) if (name or configured != "auto") else None
    # browser.profile belongs to the configured browser, or with browser.name auto to the one picked here:
    # not to a browser the brain names for one request
    same = (configured == "auto" and not name) or (configured != "auto" and browsers.resolve(configured) == want_kind)
    saved = str(settings()["browser.profile"])
    want_profile = profile or (saved if saved != "auto" and same else None)
    default = browsers.default_kind()

    conns = [c for c in connections() if (want_kind is None or c.kind == want_kind)]
    if want_profile:
        conns = [c for c in conns if c.profile and want_profile.casefold() in (c.profile.dir.casefold(), c.profile.name.casefold())]
    if conns:
        def rank(c: Conn) -> tuple:
            return (c.kind != default, not (c.profile and c.profile.dir == _last_used(c.kind)), -c.since)
        c = min(conns, key=rank)
        return Target(c.kind, c.profile, c)

    if want_kind:
        kind = want_kind
    else:
        set_up = [k for k in browsers.KINDS if ready(k)]
        kind = default if default and default in set_up else (set_up[0] if set_up else default or browsers.resolve())
    if want_profile:
        prof = find_profile(kind, want_profile)
        if prof is None:
            names = ", ".join(p.label() for p in profiles(kind)) or "none yet"
            raise UnknownProfile(f"{kind.name} has no profile called {want_profile!r} (its profiles: {names}); "
                                 "fix browser.profile, or: auren browser use auto")
    else:
        ext = with_extension(kind)
        last = _last_used(kind)
        prof = next((p for p in ext if p.dir == last), ext[0] if ext else None)
    return Target(kind, prof)


def set_up_somewhere() -> bool:
    """Some installed browser has a host registered and the extension: from files alone, no sockets asked
    (cheap enough for every tool call's integration check)."""
    return cli() is not None and any(browsers.installed(k) and registered(k) and with_extension(k)
                                     for k in browsers.KINDS)


def not_set_up(kind: browsers.Kind, profile: Profile | None = None) -> str | None:
    """Why the browser tools can't work in this browser yet (None if they can)."""
    if not browsers.installed(kind):
        return f"{kind.name} isn't installed"
    if cli() is None:
        return "Open Browser Use isn't installed"
    if any(matches(c, kind, profile) for c in connections()):
        return None                     # connected proves it, whatever the files say
    if not registered(kind):
        return f"Open Browser Use isn't registered with {kind.name}"
    if profile is not None and not has_extension(profile):
        return f"the Open Browser Use extension isn't installed (or is turned off) in {kind.name}'s {profile.label()} profile"
    if not with_extension(kind):
        return f"the Open Browser Use extension isn't installed in {kind.name}"
    return None


# --- setup ----------------------------------------------------------------------------------------------------

@dataclass
class SetupResult:
    kind: browsers.Kind
    binary: str
    wrote_manifest: bool
    opened_store: bool
    connected: Conn | None

    def summary(self) -> str:
        parts = [f"Open Browser Use host: {self.binary}"]
        parts.append(f"registered with {self.kind.name}" if self.wrote_manifest else f"{self.kind.name} already had it registered")
        if self.connected:
            parts.append(f"connected: {self.connected.label()}")
        elif self.opened_store:
            parts.append(f"opened the extension's Web Store page in {self.kind.name}: the user clicks its Add button")
        elif with_extension(self.kind):
            parts.append(f"not connected yet: open a {self.kind.name} window (restart {self.kind.name} if it was open)")
        return "; ".join(parts)


def setup_choice(name: str | None = None) -> browsers.Kind:
    """Which browser to set up: the one named, else the default browser, else the first one installed."""
    if name:
        return browsers.resolve(name)
    default = browsers.default_kind()
    if default and browsers.installed(default):
        return default
    for kind in browsers.KINDS:
        if browsers.installed(kind):
            return kind
    raise browsers.UnknownBrowser("no Chromium browser is installed (Chrome, Chromium, Brave, Helium, Edge or Vivaldi): "
                                  "Open Browser Use needs one")


def setup(kind: browsers.Kind, open_store: bool = True, wait: float = 0, on_wait=None) -> SetupResult:
    """Set up Open Browser Use for one browser: the binary, its host registration, then the extension."""
    if not browsers.installed(kind):
        raise SetupError(f"{kind.name} isn't installed")
    if browsers.flatpak(kind):
        raise SetupError(f"{kind.name} is a Flatpak here; Open Browser Use needs a normally installed browser")
    binary = install_binary()
    wrote = register(kind, binary)
    forget_scan()
    conn = next((c for c in connections() if c.kind == kind), None)
    opened = False
    if conn is None and not with_extension(kind) and open_store:
        open_in(kind, None, STORE_URL)
        opened = True
    if conn is None and wait > 0:
        if on_wait:
            on_wait(opened)
        conn = wait_for(kind, None, wait)
    return SetupResult(kind, str(host_of(kind) or binary), wrote, opened, conn)


def open_in(kind: browsers.Kind, profile: Profile | None, url: str | None = None) -> None:
    """Open a window of the browser (in a profile, at a URL) without waiting for it."""
    cmd = browsers.command(kind)
    if cmd is None:
        raise SetupError(f"{kind.name} isn't installed")
    args = ["uwsm-app", "--", cmd] if shutil.which("uwsm-app") else [cmd]
    if profile:
        args.append(f"--profile-directory={profile.dir}")
    if url:
        args.append(url)
    from . import desktop
    desktop.hide_scratchpad()
    desktop.spawn(args)


def wait_for(kind: browsers.Kind, profile: Profile | None, seconds: float) -> Conn | None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for c in connections(fresh=True):
            if matches(c, kind, profile):
                return c
        time.sleep(1)
    return None


def version_of(binary: str) -> str:
    try:
        out = subprocess.run([binary, "version"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return "?"
    m = re.search(r"\d+\.\d+\.\d+", out)
    return m.group(0) if m else (out[:20] or "?")


if sys.platform == "darwin":
    from ..mac.obu import *  # noqa: E402, F403 - the darwin build, lsof and ps
