"""Paths and secrets.

Every path Auren uses comes from here, following the XDG base directories:

    CONFIG_DIR   ~/.config/auren          config.toml, secrets, routines, the user's own vocabulary
    STATE_DIR    ~/.local/state/auren     history, log, herdr jobs, confirmations
    CACHE_DIR    ~/.cache/auren           the machine profile
    DATA_HOME    ~/.local/share/auren     the installed program: venv, UI files, auren-ctl, voice model
    RUNTIME_DIR  $XDG_RUNTIME_DIR/auren   socket, state file, loudness level

On Omarchy, Auren is installed one of two ways. install.sh puts the program itself in DATA_HOME. The Arch package
puts it in /usr (PACKAGE_LIB, PACKAGE_SHARE, /usr/bin), and `auren setup` links DATA_HOME's bin/,
mascot/ and bar-plugin/ to it, so key bindings, the mascot and the bar plugin find everything at the
same paths either way.

On macOS, install-macos.sh puts the program in DATA_HOME too, from a clone or, for Homebrew's `auren`
(BREW), from brew's copy of the source when you run `auren setup`.

On macOS the same folders are used (XDG variables are rarely set there), except RUNTIME_DIR, which is
~/Library/Application Support/auren-runtime/auren: not $TMPDIR, which macOS empties of files unused
for 3 days, sockets included. The brain's working folder stays outside $HOME, in $TMPDIR.

Secrets are read from the environment, then CONFIG_DIR/secrets.env (chmod 600),
then the keyring (`secret-tool lookup auren <name>`, on macOS the login keychain:
`security find-generic-password -s auren -a <name> -w`). They are never logged.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
MAC = sys.platform == "darwin"


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var, "")
    return Path(value) if value.startswith("/") else default     # the spec says to ignore relative paths


def _private(folder: Path) -> Path:
    """Make the folder (mode 700) and check it's this user's: in /tmp someone else could make it first.
    One of the user's own that others can read (an older Auren made it 755) is closed up."""
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    st = folder.lstat()
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        raise RuntimeError(f"{folder} isn't a folder of yours: remove it")
    if st.st_mode & 0o077:
        folder.chmod(0o700)
    return folder


def _mac_tmp() -> Path:
    """macOS's private per-user temporary folder (/var/folders/...), outside $HOME. Launch agents don't
    always get $TMPDIR, and Python has no name for it in os.confstr, so getconf is asked."""
    tmp = os.environ.get("TMPDIR", "")
    if not tmp.startswith("/var/folders/"):
        try:
            tmp = subprocess.run(["getconf", "DARWIN_USER_TEMP_DIR"], capture_output=True, text=True,
                                 timeout=5).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            tmp = ""
    return Path(tmp) if tmp.startswith("/var/folders/") else Path(f"/tmp/auren-tmp-{os.getuid()}")


def _runtime_base() -> Path:
    """$XDG_RUNTIME_DIR, or a private folder in /tmp when the session doesn't set it."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if value.startswith("/"):
        return Path(value)
    if MAC:                       # not $TMPDIR: macOS deletes what's in it after 3 days unused, sockets included
        base = HOME / "Library/Application Support/auren-runtime"
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        base.chmod(0o700)
        return base
    fallback = Path(f"/tmp/auren-runtime-{os.getuid()}")
    fallback.mkdir(mode=0o700, exist_ok=True)
    st = fallback.lstat()          # someone else may have made it first, to catch Auren's sockets
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RuntimeError(f"{fallback} isn't a private folder of yours: remove it, or set XDG_RUNTIME_DIR")
    return fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "auren"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local/state") / "auren"
CACHE_DIR = _xdg("XDG_CACHE_HOME", HOME / ".cache") / "auren"
DATA_HOME = _xdg("XDG_DATA_HOME", HOME / ".local/share") / "auren"
RUNTIME_DIR = _runtime_base() / "auren"

SOCKET = RUNTIME_DIR / "auren.sock"
TOOLS_SOCKET = RUNTIME_DIR / "tools.sock"     # the tool runtime, for the brains' MCP adapter
LOCK = RUNTIME_DIR / "daemon.lock"           # held by the running daemon: only one at a time
STATE_FILE = RUNTIME_DIR / "state.json"      # current state, for the mascot and the bar plugin
# The brain's working folder: outside $HOME on purpose (see brain/__init__.py). On macOS the runtime folder
# is in $HOME, so it goes in the temporary folder; it's made again before every use.
BRAIN_DIR = _mac_tmp() / "auren-brain" if MAC else RUNTIME_DIR / "brain"


def brain_dir() -> Path:
    """BRAIN_DIR, made now and checked to be private (the brain reads instructions from its working folder)."""
    if MAC:
        _private(BRAIN_DIR.parent)
    return _private(BRAIN_DIR)
SECRETS = CONFIG_DIR / "secrets.env"
CONFIG_FILE = CONFIG_DIR / "config.toml"    # the user's settings (settings.py)
SETTINGS = CONFIG_DIR / "settings.json"     # the old settings file, moved into config.toml once
ROUTINES_DIR = CONFIG_DIR / "routines"
DATA = Path(__file__).parent / "data"

# The Arch package's files (packaging/arch/PKGBUILD)
PACKAGE_LIB = Path("/usr/lib/auren")           # its Python environment, this code included
PACKAGE_SHARE = Path("/usr/share/auren")       # mascot/ and bar-plugin/
PACKAGE_UNITS = Path("/usr/lib/systemd/user")
PACKAGE_BIN = Path("/usr/bin")
PACKAGE_NAME = "auren"


def _packaged() -> bool:
    return Path(__file__).resolve().is_relative_to(PACKAGE_LIB)


PACKAGED = _packaged()                         # running from the Arch package, not an install.sh install


def _brew() -> bool:
    """Installed from Homebrew (packaging/homebrew/): install-macos.sh --brew recorded mode "brew"."""
    import json
    try:
        return json.loads((DATA_HOME / "install.json").read_text()).get("mode") == "brew"
    except (OSError, ValueError, AttributeError):
        return False


BREW = MAC and not PACKAGED and _brew()
REINSTALL = (f"yay -S {PACKAGE_NAME}" if PACKAGED              # what puts missing program files back
             else "auren setup" if BREW else "./install-macos.sh" if MAC else "./install.sh")


def secret(name: str) -> str | None:
    """name like ASSEMBLYAI_API_KEY."""
    if os.environ.get(name):
        return os.environ[name]
    if SECRETS.exists():
        for line in SECRETS.read_text().splitlines():
            key, _, value = line.partition("=")
            if key.strip() == name and value.strip():
                return value.strip().strip("\"'")
    cmd = (["security", "find-generic-password", "-s", "auren", "-a", name.lower(), "-w"] if MAC
           else ["secret-tool", "lookup", "auren", name.lower()])
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
