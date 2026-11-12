"""What Auren knows about a Mac: the collectors of profile/collect.py that differ on macOS.

Static: the machine (sysctl, sw_vers), default apps (LaunchServices), apps (the .app bundles in the
application folders), the shell (zsh is macOS's default), Homebrew packages, Obsidian's vaults.
Live: windows (Quartz), volume and media (AppleScript), battery (pmset), memory (vm_stat).
Omarchy's commands and Hyprland's key bindings don't exist here: those lists are empty.
"""

from __future__ import annotations

import json
import os
import platform
import plistlib
import re
import shutil
from pathlib import Path

from ..profile.collect import COMMON_APPS, HOME, run, soft

__all__ = ["APP_DIRS", "machine", "defaults", "apps", "keybindings", "shell", "omarchy_commands", "services",
           "packages", "obsidian", "desktop", "audio", "media", "power", "memory", "default_handler", "app_name"]

APP_DIRS = [Path("/System/Applications"), Path("/System/Applications/Utilities"), Path("/Applications"),
            Path("/Applications/Utilities"), HOME / "Applications", HOME / "Applications/Chrome Apps.localized"]
LS_PLIST = HOME / "Library/Preferences/com.apple.LaunchServices/com.apple.launchservices.secure.plist"

# Apps that are noise for a voice assistant
NOISE = re.compile(r"^(Install .*|.* Uninstaller|Uninstall .*|Boot Camp Assistant|Migration Assistant|"
                   r"Bluetooth File Exchange|ColorSync Utility|Directory Utility|AirPort Utility|Audio MIDI Setup)$")


def _sysctl(name: str) -> str:
    return run(["sysctl", "-n", name]).strip()


@soft
def machine() -> dict:
    return {
        "hostname": platform.node(), "user": os.environ.get("USER", ""), "home": str(HOME),
        "cpu": _sysctl("machdep.cpu.brand_string"), "threads": os.cpu_count(),
        "ram_gb": round(int(_sysctl("hw.memsize")) / 1024 ** 3, 1),
        "kernel": platform.release(),
        "macos": run(["sw_vers", "-productVersion"]).strip(),
        "shell": os.environ.get("SHELL", ""),
    }


def _bundle(app: Path) -> dict:
    try:
        with open(app / "Contents/Info.plist", "rb") as f:
            return plistlib.load(f)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return {}


def default_handler(scheme: str = "https", content: str | None = None) -> str:
    """The bundle id LaunchServices opens a URL scheme (or a content type) with, lowercased, or ""."""
    try:
        with open(LS_PLIST, "rb") as f:
            handlers = plistlib.load(f).get("LSHandlers", [])
    except (OSError, plistlib.InvalidFileException, ValueError):
        return ""
    for h in handlers:
        if (content and h.get("LSHandlerContentType") == content) or (not content and h.get("LSHandlerURLScheme") == scheme):
            return str(h.get("LSHandlerRoleAll") or h.get("LSHandlerRoleViewer") or "").lower()
    return ""


def app_name(bundle_id: str) -> str | None:
    """An installed app's name for a bundle id."""
    for a in _app_bundles():
        if str(_bundle(a).get("CFBundleIdentifier", "")).lower() == bundle_id.lower():
            return a.stem
    return None


@soft
def defaults() -> dict:
    from ..integrations import default_agent
    browser = default_handler("https")
    term = "Ghostty" if (Path("/Applications/Ghostty.app")).exists() else \
        "iTerm" if Path("/Applications/iTerm.app").exists() else "Terminal"
    return {
        "browser": app_name(browser) if browser else "Safari",
        "terminal": term,
        "editor": os.environ.get("EDITOR", "nano"),
        "coding_agent": default_agent(),
        "file_manager": "Finder",
        "pdf": app_name(default_handler(content="com.adobe.pdf")) or "Preview",
        "image": "Preview",
        "text": app_name(default_handler(content="public.plain-text")) or "TextEdit",
    }


def _app_bundles() -> list[Path]:
    seen: dict[str, Path] = {}
    for d in APP_DIRS:
        if d.is_dir():
            for a in d.glob("*.app"):
                seen.setdefault(a.stem.lower(), a)
    seen.setdefault("finder", Path("/System/Library/CoreServices/Finder.app"))
    return sorted(seen.values(), key=lambda p: p.stem.lower())


@soft
def apps() -> dict:
    """Apps (.app bundles) by name; Chrome's installed web apps count as apps (they are bundles too)."""
    out = {"apps": [], "webapps": [], "tuis": []}
    for a in _app_bundles():
        if NOISE.match(a.stem):
            continue
        info = _bundle(a)
        item = {"name": str(info.get("CFBundleDisplayName") or a.stem), "id": str(info.get("CFBundleIdentifier") or a.stem),
                "path": str(a)}
        if item["name"] != a.stem:              # say "open Visual Studio Code" and "open Code" alike
            item["about"] = a.stem
        out["apps"].append(item)
    have = " ".join(i["id"].lower() + " " + i["name"].lower() for i in out["apps"])
    out["not_installed"] = [label for key, label in COMMON_APPS.items()
                            if key not in have and label.lower() not in have]
    return out


@soft
def keybindings() -> list[dict]:
    return []


@soft
def shell() -> dict:
    """Aliases and functions from an interactive zsh (macOS's default shell), else bash."""
    sh = "zsh" if shutil.which("zsh") and os.environ.get("SHELL", "/bin/zsh").endswith("zsh") else "bash"
    aliases = []
    for line in run([sh, "-ic", "alias"], timeout=10).splitlines():
        m = re.match(r"(?:alias )?([^=]+)='?(.*?)'?$", line)
        if m:
            aliases.append({"name": m.group(1), "value": m.group(2).replace("'\\''", "'")})
    if sh == "zsh":
        names = run(["zsh", "-ic", "print -l ${(k)functions}"], timeout=10).split()
    else:
        names = [line.split()[-1] for line in run(["bash", "-ic", "declare -F"], timeout=10).splitlines()]
    return {"aliases": aliases, "functions": [f for f in names if not f.startswith(("_", "+", "."))]}


@soft
def omarchy_commands() -> list[dict]:
    return []


@soft
def services() -> list[str]:
    return []


@soft
def packages() -> list[str]:
    brew = shutil.which("brew") or next((p for p in ("/opt/homebrew/bin/brew", "/usr/local/bin/brew") if os.path.exists(p)), None)
    return run([brew, "leaves"], timeout=20).split() if brew else []


@soft
def obsidian() -> list[str]:
    cfg = HOME / "Library/Application Support/obsidian/obsidian.json"
    vaults = json.loads(cfg.read_text()).get("vaults", {}) if cfg.exists() else {}
    return [v["path"] for v in vaults.values() if v.get("path")]


# --- live ---------------------------------------------------------------------------------------------

@soft
def desktop() -> dict:
    from . import windows as macwin
    front = macwin.frontmost_pid()
    seen_front = False
    out = []
    for w in macwin.list_windows():
        focused = w["pid"] == front and not seen_front
        seen_front = seen_front or focused
        out.append({"app": w["class"] or "?", "title": w["title"][:80], "focused": focused, "address": w["address"]})
    return {"windows": out}


@soft
def audio() -> dict:
    out = run(["osascript", "-e", 'set s to get volume settings\nreturn (output volume of s as text) & "," & '
               '(output muted of s as text)']).strip()
    vol, _, muted = out.partition(",")
    return {"volume_percent": int(float(vol)) if vol and vol != "missing value" else None, "muted": muted == "true"}


@soft
def media() -> dict:
    from ..hands import spotify
    n = spotify.now_playing()
    if n.get("title"):
        return {"playing": n.get("status") == "Playing", "player": "Spotify", "title": n["title"], "artist": n.get("artist")}
    return {"playing": False}


@soft
def power() -> dict:
    text = run(["pmset", "-g", "batt"])
    out = {"plugged_in": "AC Power" in text}
    m = re.search(r"(\d+)%;\s*([\w ]+?);", text)
    if m:
        out["battery_percent"] = int(m.group(1))
        out["battery_status"] = m.group(2).strip().title()
    return out


@soft
def memory() -> dict:
    page = int(re.search(r"page size of (\d+)", text := run(["vm_stat"])).group(1))
    pages = {k.strip().lower(): int(v) for k, v in re.findall(r"^([^:]+):\s+(\d+)\.", text, re.M)}
    free = sum(pages.get(k, 0) for k in ("pages free", "pages inactive", "pages speculative", "pages purgeable"))
    total = int(_sysctl("hw.memsize"))
    return {"ram_free_gb": round(free * page / 1024 ** 3, 1), "ram_total_gb": round(total / 1024 ** 3, 1)}
