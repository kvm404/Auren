"""Paths and secrets.

Secrets are read from the environment, then ~/.config/auren/secrets.env (chmod 600),
then the keyring (`secret-tool lookup auren <name>`). They are never logged.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

HOME = Path.home()
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config")) / "auren"
STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state")) / "auren"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "auren"
SOCKET = RUNTIME_DIR / "auren.sock"
STATE_FILE = RUNTIME_DIR / "state.json"      # current state, for the mascot and the bar plugin
SECRETS = CONFIG_DIR / "secrets.env"
SETTINGS = CONFIG_DIR / "settings.json"     # voice on/off and brain mode, changed from the bar plugin
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
