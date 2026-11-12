"""The Chromium browsers on macOS: app bundles in the application folders, profiles under
~/Library/Application Support, and the default browser from LaunchServices. Open Browser Use's
native messaging manifests go in each browser's NativeMessagingHosts folder there, as on Linux."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..hands.browsers import Kind

__all__ = ["KINDS", "BY_KEY", "config_home", "installed", "default_desktop_id", "running", "launcher", "command",
           "flatpak", "app_path"]

HOME = Path.home()

# desktop_ids are bundle ids (lowercased); commands are app bundle names; processes are executable names.
KINDS = [
    Kind("chrome", "Google Chrome", ("com.google.chrome",), ("Google/Chrome",), ("Google Chrome",), ("Google Chrome",)),
    Kind("chromium", "Chromium", ("org.chromium.chromium",), ("Chromium",), ("Chromium",), ("Chromium",)),
    Kind("brave", "Brave", ("com.brave.browser",), ("BraveSoftware/Brave-Browser",), ("Brave Browser",),
         ("Brave Browser",)),
    Kind("helium", "Helium", ("net.imput.helium",), ("net.imput.helium",), ("Helium",), ("Helium",)),
    Kind("edge", "Microsoft Edge", ("com.microsoft.edgemac",), ("Microsoft Edge",), ("Microsoft Edge",),
         ("Microsoft Edge",)),
    Kind("vivaldi", "Vivaldi", ("com.vivaldi.vivaldi",), ("Vivaldi",), ("Vivaldi",), ("Vivaldi",)),
]
BY_KEY = {k.key: k for k in KINDS}


def config_home() -> Path:
    return HOME / "Library/Application Support"


def app_path(kind: Kind) -> Path | None:
    for d in (Path("/Applications"), HOME / "Applications"):
        for name in kind.commands:
            p = d / f"{name}.app"
            if p.is_dir():
                return p
    return None


def installed(kind: Kind) -> bool:
    return app_path(kind) is not None


def default_desktop_id() -> str:
    from .profile import default_handler
    return default_handler("https")


def running(kind: Kind) -> bool:
    return any(subprocess.run(["pgrep", "-x", p], capture_output=True).returncode == 0 for p in kind.processes)


def command(kind: Kind) -> str | None:
    """The browser's own executable: started with arguments, it hands them to a running browser."""
    app = app_path(kind)
    if app is None:
        return None
    exe = app / "Contents/MacOS" / kind.processes[0]
    return str(exe) if exe.exists() else None


def launcher(kind: Kind) -> list[str]:
    app = app_path(kind)
    return ["open", "-a", str(app)] if app else ["open", "-a", kind.commands[0]]


def flatpak(kind: Kind) -> bool:
    return False
