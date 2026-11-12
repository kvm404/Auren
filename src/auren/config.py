"""Paths and secrets.

Every path Auren uses comes from here, following the XDG base directories:

    CONFIG_DIR   ~/.config/auren          config.toml, secrets, routines, the user's own vocabulary
    STATE_DIR    ~/.local/state/auren     history, log, herdr jobs, confirmations
    CACHE_DIR    ~/.cache/auren           the machine profile
    DATA_HOME    ~/.local/share/auren     the installed program: venv, UI files, auren-ctl, voice model
    RUNTIME_DIR  $XDG_RUNTIME_DIR/auren   socket, state file, loudness level

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


def _mac_tmp() -> Path:
    """macOS's private per-user temporary folder (/var/folders/...), outside $HOME."""
    tmp = os.environ.get("TMPDIR", "")
    if not tmp.startswith("/var/folders/"):
        try:
            tmp = os.confstr("CS_DARWIN_USER_TEMP_DIR") or ""
        except (ValueError, OSError):
            tmp = ""
    return Path(tmp) if tmp.startswith("/") else Path(f"/tmp/auren-tmp-{os.getuid()}")


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
SECRETS = CONFIG_DIR / "secrets.env"
CONFIG_FILE = CONFIG_DIR / "config.toml"    # the user's settings (settings.py)
SETTINGS = CONFIG_DIR / "settings.json"     # the old settings file, moved into config.toml once
ROUTINES_DIR = CONFIG_DIR / "routines"
MODELS_DIR = DATA_HOME / "models"
DATA = Path(__file__).parent / "data"


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
