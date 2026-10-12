"""Paths and secrets.

Every path Auren uses comes from here, following the XDG base directories:

    CONFIG_DIR   ~/.config/auren          config.toml, secrets, routines, the user's own vocabulary
    STATE_DIR    ~/.local/state/auren     history, log, herdr jobs, confirmations
    CACHE_DIR    ~/.cache/auren           the machine profile
    DATA_HOME    ~/.local/share/auren     the installed program: venv, UI files, auren-ctl, voice model
    RUNTIME_DIR  $XDG_RUNTIME_DIR/auren   socket, state file, loudness level

Secrets are read from the environment, then CONFIG_DIR/secrets.env (chmod 600),
then the keyring (`secret-tool lookup auren <name>`). They are never logged.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOME = Path.home()


def _xdg(var: str, default: Path) -> Path:
    value = os.environ.get(var, "")
    return Path(value) if value.startswith("/") else default     # the spec says to ignore relative paths


def _runtime_base() -> Path:
    """$XDG_RUNTIME_DIR, or a private folder in /tmp when the session doesn't set it."""
    value = os.environ.get("XDG_RUNTIME_DIR", "")
    if value.startswith("/"):
        return Path(value)
    fallback = Path(f"/tmp/auren-runtime-{os.getuid()}")
    fallback.mkdir(mode=0o700, exist_ok=True)
    return fallback


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", HOME / ".config") / "auren"
STATE_DIR = _xdg("XDG_STATE_HOME", HOME / ".local/state") / "auren"
CACHE_DIR = _xdg("XDG_CACHE_HOME", HOME / ".cache") / "auren"
DATA_HOME = _xdg("XDG_DATA_HOME", HOME / ".local/share") / "auren"
RUNTIME_DIR = _runtime_base() / "auren"

SOCKET = RUNTIME_DIR / "auren.sock"
LOCK = RUNTIME_DIR / "daemon.lock"           # held by the running daemon: only one at a time
STATE_FILE = RUNTIME_DIR / "state.json"      # current state, for the mascot and the bar plugin
BRAIN_DIR = RUNTIME_DIR / "brain"            # the brain's working folder: outside $HOME on purpose
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
    try:
        out = subprocess.run(["secret-tool", "lookup", "auren", name.lower()], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None
