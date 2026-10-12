"""Which Chromium browser the browser tools use, and where its files are.

Open Browser Use works with Chromium browsers. The browser is `browser.name` in config.toml, or with
"auto" the system's default browser (xdg-settings) if it's one of these, else the first one installed.
Its profile (bookmarks, download folder) is `browser.profile`, or the last one used (from its
`Local State`). Each browser's Open Browser Use host listens in /tmp/open-browser-use unless
`browser.sockets` names another folder for it (for people who run one host per browser).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..settings import current as settings

DEFAULT_SOCKETS = "/tmp/open-browser-use"


@dataclass(frozen=True)
class Kind:
    key: str
    name: str
    desktop_ids: tuple[str, ...]      # launchers (.desktop files), as xdg-settings reports them
    config_dirs: tuple[str, ...]      # under ~/.config: the first that holds a `Local State` is used
    commands: tuple[str, ...]         # executables, to tell whether it's installed
    processes: tuple[str, ...]        # process names, to tell whether it's running


KINDS = [
    Kind("chrome", "Google Chrome", ("google-chrome", "google-chrome-stable", "com.google.Chrome"),
         ("google-chrome",), ("google-chrome-stable", "google-chrome"), ("chrome",)),
    Kind("chromium", "Chromium", ("chromium", "chromium-browser", "org.chromium.Chromium"),
         ("chromium",), ("chromium", "chromium-browser"), ("chromium",)),
    Kind("brave", "Brave", ("brave-browser", "brave", "com.brave.Browser"),
         ("BraveSoftware/Brave-Browser",), ("brave", "brave-browser"), ("brave",)),
    Kind("helium", "Helium", ("helium-browser", "helium", "net.imput.helium"),
         ("net.imput.helium", "helium"), ("helium-browser", "helium"), ("helium",)),
    Kind("edge", "Microsoft Edge", ("microsoft-edge", "microsoft-edge-stable"),
         ("microsoft-edge",), ("microsoft-edge-stable", "microsoft-edge"), ("msedge",)),
    Kind("vivaldi", "Vivaldi", ("vivaldi-stable", "vivaldi"),
         ("vivaldi",), ("vivaldi-stable", "vivaldi"), ("vivaldi-bin",)),
]
BY_KEY = {k.key: k for k in KINDS}
ALIASES = {"google chrome": "chrome", "google-chrome": "chrome", "brave browser": "brave", "microsoft edge": "edge"}


class UnknownBrowser(ValueError):
    pass


def _config_home() -> Path:
    v = os.environ.get("XDG_CONFIG_HOME", "")
    return Path(v) if v.startswith("/") else Path.home() / ".config"


def installed(kind: Kind) -> bool:
    return any(shutil.which(c) for c in kind.commands)


def default_desktop_id() -> str:
    try:
        out = subprocess.run(["xdg-settings", "get", "default-web-browser"], capture_output=True, text=True, timeout=5)
        return out.stdout.strip().removesuffix(".desktop")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def of_desktop_id(desktop_id: str) -> Kind | None:
    return next((k for k in KINDS if desktop_id in k.desktop_ids), None)


def resolve(name: str | None = None) -> Kind:
    """The browser to use: one named (by the brain or the user), else the configured one, else detected."""
    want = (name or "").strip().lower()
    want = ALIASES.get(want, want)
    if not want:
        want = str(settings()["browser.name"]).lower()
    if want != "auto":
        if want not in BY_KEY:
            raise UnknownBrowser(f"Auren's browser tools work with {', '.join(BY_KEY)}, not {name or want}")
        return BY_KEY[want]
    default = of_desktop_id(default_desktop_id())
    if default:
        return default
    for k in KINDS:
        if installed(k):
            return k
    raise UnknownBrowser("no Chromium browser is installed (the browser tools need one: Chrome, Chromium, Brave, ...)")


def config_dir(kind: Kind) -> Path | None:
    for d in kind.config_dirs:
        p = _config_home() / d
        if (p / "Local State").exists():
            return p
    return None


def profile_dir(kind: Kind) -> Path | None:
    """The profile folder: browser.profile, or the last one used, or Default."""
    base = config_dir(kind)
    if base is None:
        return None
    want = str(settings()["browser.profile"])
    if want != "auto":
        return base / want
    try:
        last = json.loads((base / "Local State").read_text()).get("profile", {}).get("last_used")
    except (OSError, json.JSONDecodeError):
        last = None
    for name in (last, "Default"):
        if name and (base / name).is_dir():
            return base / name
    return None


def socket_dir(kind: Kind) -> str:
    return settings()["browser.sockets"].get(kind.key) or DEFAULT_SOCKETS


def running(kind: Kind) -> bool:
    return any(subprocess.run(["pgrep", "-x", p], capture_output=True).returncode == 0 for p in kind.processes)


def launcher(kind: Kind) -> list[str]:
    """How to start it: through its launcher (uwsm-app runs .desktop files), else its command."""
    from ..profile.collect import DESKTOP_DIRS
    for d in reversed(DESKTOP_DIRS):
        for i in kind.desktop_ids:
            if (d / f"{i}.desktop").exists():
                return ["uwsm-app", "--", f"{i}.desktop"]
    cmd = next((c for c in kind.commands if shutil.which(c)), kind.commands[0])
    return ["uwsm-app", "--", cmd]


def command(kind: Kind) -> str | None:
    return next((c for c in kind.commands if shutil.which(c)), None)
