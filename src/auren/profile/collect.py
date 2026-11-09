"""Collect facts about this Omarchy laptop.

Two kinds of facts:
- static: changes rarely (apps, web apps, key bindings, aliases, Omarchy
  commands, projects, defaults). Cached on disk and rebuilt when stale.
- live: changes all the time (windows, herdr panes, volume, battery). Read on
  every request; each collector is fast and read-only.

Every collector is read-only and fails soft: on error it returns
{"error": "..."} instead of raising, so one missing tool never breaks the
profile.
"""

from __future__ import annotations

import configparser
import json
import os
import platform
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

HOME = Path.home()
# Where people keep projects, when config.toml's projects.folders doesn't say.
PROJECT_FOLDERS = ["~/Projects", "~/projects", "~/Code", "~/code", "~/src", "~/dev", "~/Developer", "~/work",
                   "~/repos", "~/git", "~/Documents/GitHub"]

DESKTOP_DIRS = [  # later entries win, so user launchers override system ones
    Path("/usr/share/applications"),
    Path("/var/lib/flatpak/exports/share/applications"),
    HOME / ".local/share/flatpak/exports/share/applications",
    HOME / ".local/share/applications",
]

# Launchers that are noise for a voice assistant (helpers, consoles, settings panes).
NOISE_APPS = re.compile(r"^(Foot (Client|Server)|OpenJDK .*|Hardware Locality .*|Print Settings)$")

# Apps people commonly ask for, to state plainly when one isn't installed.
COMMON_APPS = {
    "firefox": "Firefox", "google-chrome": "Google Chrome", "chromium": "Chromium", "spotify": "Spotify",
    "discord": "Discord", "slack": "Slack", "telegram": "Telegram", "signal-desktop": "Signal",
    "vlc": "VLC", "gimp": "GIMP", "libreoffice": "LibreOffice", "zoom": "Zoom", "steam": "Steam",
    "code": "VS Code", "obsidian": "Obsidian", "thunderbird": "Thunderbird",
}


def run(cmd: list[str], timeout: float = 5, shell_env: dict | None = None) -> str:
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                         env={**os.environ, **(shell_env or {})})
    if out.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {out.returncode}: {out.stderr.strip()[:200]}")
    return out.stdout


def soft(fn):
    """Decorator: return {"error": ...} instead of raising."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001 - a collector must never take the profile down
            return {"error": f"{type(e).__name__}: {e}"[:300]}
    wrapper.__name__ = fn.__name__
    return wrapper


# --- static ----------------------------------------------------------------------

@soft
def machine() -> dict:
    mem_kb = int(re.search(r"MemTotal:\s+(\d+)", Path("/proc/meminfo").read_text()).group(1))
    cpu = re.search(r"model name\s*:\s*(.+)", Path("/proc/cpuinfo").read_text())
    return {
        "hostname": platform.node(), "user": os.environ.get("USER", ""), "home": str(HOME),
        "cpu": cpu.group(1).strip() if cpu else platform.processor(), "threads": os.cpu_count(),
        "ram_gb": round(mem_kb / 1024 / 1024, 1),
        "kernel": platform.release(),
        "omarchy": run(["omarchy", "version"]).strip() if shutil.which("omarchy") else None,
        "shell": os.environ.get("SHELL", ""),
    }


@soft
def defaults() -> dict:
    def mime(t: str) -> str | None:
        try:
            return run(["xdg-mime", "query", "default", t]).strip().removesuffix(".desktop") or None
        except Exception:  # noqa: BLE001
            return None

    agent_file = HOME / ".config/omarchy/defaults/agent"
    editor_file = HOME / ".local/state/omarchy/defaults/editor"
    terminal = None
    if shutil.which("xdg-terminal-exec"):
        terminal = run(["xdg-terminal-exec", "--print-id"]).strip().removesuffix(".desktop")
    return {
        "browser": run(["xdg-settings", "get", "default-web-browser"]).strip().removesuffix(".desktop"),
        "terminal": terminal,
        "editor": editor_file.read_text().strip() if editor_file.exists() else "nvim",
        "coding_agent": agent_file.read_text().strip() if agent_file.exists() else None,
        "file_manager": mime("inode/directory"),
        "pdf": mime("application/pdf"),
        "image": mime("image/png"),
        "video": mime("video/mp4"),
        "audio": mime("audio/mpeg"),
        "text": mime("text/plain"),
        "markdown": mime("text/markdown"),
    }


def _desktop_entries() -> dict[str, Path]:
    entries: dict[str, Path] = {}
    for d in DESKTOP_DIRS:
        if d.is_dir():
            for f in d.glob("*.desktop"):
                entries[f.stem] = f
    return entries


@soft
def apps() -> dict:
    """Launchable apps, split into regular apps, web apps and terminal (TUI) apps."""
    out = {"apps": [], "webapps": [], "tuis": []}
    for desktop_id, path in sorted(_desktop_entries().items()):
        cp = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            cp.read(path, encoding="utf-8")
            e = cp["Desktop Entry"]
        except Exception:  # noqa: BLE001 - malformed launcher files exist in the wild
            continue
        if e.get("Type", "Application") != "Application":
            continue
        if e.get("NoDisplay", "").lower() == "true" or e.get("Hidden", "").lower() == "true":
            continue
        exec_ = e.get("Exec", "")
        item = {"name": e.get("Name", desktop_id), "id": desktop_id}
        if NOISE_APPS.match(item["name"]):
            continue
        if generic := e.get("GenericName") or e.get("Comment"):
            item["about"] = generic[:80]
        if wm := e.get("StartupWMClass"):
            item["wmclass"] = wm
        web = re.search(r"omarchy-launch-webapp\s+(\S+)|--app=(\S+)", exec_)
        if web:
            item["url"] = (web.group(1) or web.group(2)).strip("\"'")
            out["webapps"].append(item)
        elif e.get("Terminal", "").lower() == "true" or re.search(r"omarchy-launch-(tui|or-focus-tui)", exec_):
            item["command"] = exec_
            out["tuis"].append(item)
        else:
            out["apps"].append(item)
    have = " ".join(i["id"].lower() + " " + i["name"].lower() for i in out["apps"] + out["webapps"])
    out["not_installed"] = [label for key, label in COMMON_APPS.items()
                            if key not in have and label.lower() not in have]
    return out


@soft
def keybindings() -> list[dict]:
    """Hyprland key bindings as Omarchy's keybindings menu shows them ("SUPER + K → Keybindings")."""
    text = run(["omarchy-menu-keybindings", "--print"], timeout=20)
    binds = []
    for line in text.splitlines():
        if "→" in line:
            keys, action = line.split("→", 1)
            binds.append({"keys": " ".join(keys.split()), "action": action.strip()})
    return binds


@soft
def shell() -> dict:
    """Aliases and functions from an interactive bash, as the user's terminal has them."""
    aliases = []
    for line in run(["bash", "-ic", "alias"], timeout=10).splitlines():
        m = re.match(r"alias ([^=]+)='(.*)'$", line)
        if m:
            aliases.append({"name": m.group(1), "value": m.group(2).replace("'\\''", "'")})
    functions = [l.split()[-1] for l in run(["bash", "-ic", "declare -F"], timeout=10).splitlines()]
    functions = [f for f in functions if not f.startswith("_") and f not in ("command_not_found_handle",)]
    return {"aliases": aliases, "functions": functions}


@soft
def omarchy_commands() -> list[dict]:
    data = json.loads(run(["omarchy", "commands", "--json"], timeout=20))
    return [{"route": c["route"], "args": c.get("args") or "", "summary": c.get("summary", ""),
             "sudo": bool(c.get("requires_sudo"))}
            for c in data.get("commands", []) if not c.get("hidden")]


def project_folders() -> list[Path]:
    from ..settings import current
    configured = current()["projects.folders"]
    folders = [Path(os.path.expanduser(f)) for f in (configured or PROJECT_FOLDERS)]
    seen, out = set(), []
    for f in folders:
        if f.is_dir() and f.resolve() not in seen:
            seen.add(f.resolve())
            out.append(f)
    return out


@soft
def projects() -> list[dict]:
    items = []
    for d in (c for f in project_folders() for c in f.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        item = {"name": d.name, "path": str(d), "modified": int(d.stat().st_mtime)}
        for readme in ("README.md", "readme.md", "README"):
            if (d / readme).exists():
                for line in (d / readme).read_text(errors="ignore").splitlines():
                    line = line.strip("# ").strip()
                    if line and not line.startswith(("!", "[", "<")):
                        item["about"] = line[:90]
                        break
                break
        items.append(item)
    return sorted(items, key=lambda p: -p["modified"])


@soft
def obsidian() -> list[str]:
    cfg = HOME / ".config/obsidian/obsidian.json"
    vaults = json.loads(cfg.read_text()).get("vaults", {}) if cfg.exists() else {}
    return [v["path"] for v in vaults.values() if v.get("path")]


def bookmark_file() -> tuple[str, Path] | None:
    """The browser tools' browser and its Bookmarks file."""
    from ..hands import browser
    try:
        link = browser._browser()           # the same browser and profile the tools would use
    except browser.BrowserError:
        return None
    profile = link.profile_path()
    return (link.label(), profile / "Bookmarks") if profile else None


@soft
def bookmarks() -> dict[str, list[str]]:
    """Bookmark names of the browser the browser tools use, as "Folder/Name" (no URLs: they can hold
    login tokens)."""
    found = bookmark_file()
    files = dict([found]) if found else {}
    out = {}
    for browser, path in files.items():
        if not path.exists():
            continue
        names: list[str] = []
        def walk(node, folders, names=names):
            if node.get("type") == "folder":
                for c in node.get("children", []):
                    walk(c, folders + [node.get("name", "")])
            elif node.get("url"):
                names.append("/".join(f for f in folders[1:] + [node.get("name", "")] if f))
        for root in json.loads(path.read_text()).get("roots", {}).values():
            if isinstance(root, dict):
                walk(root, [])
        out[browser] = names
    return out


@soft
def services() -> list[str]:
    out = run(["systemctl", "--user", "list-units", "--type=service", "--state=running", "--no-legend", "--plain"])
    names = [l.split()[0].removesuffix(".service") for l in out.splitlines() if l.strip()]
    boring = re.compile(r"^(dbus|at-spi|xdg-|gvfs|pipewire|wireplumber|dconf|gnome-keyring|p11-kit|"
                        r"app-|snap\.|evolution|uwsm|xdg-desktop-portal|obex|filter-chain|systemd-)")
    return [n for n in names if not boring.match(n)]


@soft
def packages() -> list[str]:
    return run(["pacman", "-Qeq"], timeout=10).split()


# --- live ------------------------------------------------------------------------

@soft
def hyprland() -> dict:
    clients = json.loads(run(["hyprctl", "clients", "-j"]))
    active = json.loads(run(["hyprctl", "activewindow", "-j"]) or "{}")
    monitors = json.loads(run(["hyprctl", "monitors", "-j"]))
    windows = []
    for c in clients:
        ws = c.get("workspace", {})
        windows.append({
            "app": c.get("class") or c.get("initialClass") or "?",
            "title": (c.get("title") or "")[:80],
            "workspace": ws.get("name") if str(ws.get("id", "")).startswith("-") else ws.get("id"),
            "focused": c.get("address") == active.get("address"),
            "floating": c.get("floating", False),
            "address": c.get("address"),
        })
    return {
        "windows": sorted(windows, key=lambda w: (str(w["workspace"]), w["app"])),
        "active_workspace": next((m["activeWorkspace"]["id"] for m in monitors if m.get("focused")), None),
        "monitors": [{"name": m["name"], "size": f"{m['width']}x{m['height']}", "scale": m["scale"]} for m in monitors],
    }


@soft
def herdr() -> dict | None:
    from ..integrations import active
    if not active("herdr"):
        return None                      # not set up here: left out of the snapshot

    def get(*args) -> dict:
        return json.loads(run(["herdr", *args]))["result"]

    workspaces = get("workspace", "list")["workspaces"]
    tabs = get("tab", "list")["tabs"]
    panes = get("pane", "list")["panes"]
    agents = {a["pane_id"]: a for a in get("agent", "list")["agents"]}
    out = []
    for w in workspaces:
        wtabs = []
        for t in [t for t in tabs if t["workspace_id"] == w["workspace_id"]]:
            tpanes = []
            for p in [p for p in panes if p["tab_id"] == t["tab_id"]]:
                a = agents.get(p["pane_id"])
                tpanes.append({
                    "pane_id": p["pane_id"], "cwd": p.get("foreground_cwd") or p.get("cwd"),
                    "title": p.get("terminal_title_stripped", "")[:60],
                    "agent": a["agent"] if a else None, "status": a.get("agent_status") if a else None,
                    "focused": p.get("focused", False),
                })
            wtabs.append({"tab_id": t["tab_id"], "label": t.get("label"), "panes": tpanes})
        out.append({"workspace_id": w["workspace_id"], "label": w.get("label"), "focused": w.get("focused"),
                    "tabs": wtabs})
    return {"workspaces": out}


@soft
def audio() -> dict:
    out = run(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).strip()   # "Volume: 0.55 [MUTED]"
    m = re.search(r"([\d.]+)", out)
    return {"volume_percent": round(float(m.group(1)) * 100) if m else None, "muted": "MUTED" in out}


@soft
def power() -> dict:
    supplies = Path("/sys/class/power_supply")
    bat = next((p for p in supplies.glob("BAT*")), None) if supplies.exists() else None
    ac = next((p for p in supplies.iterdir() if (p / "online").exists()), None) if supplies.exists() else None
    out = {}
    if bat:
        out["battery_percent"] = int((bat / "capacity").read_text())
        out["battery_status"] = (bat / "status").read_text().strip()
    if ac:
        out["plugged_in"] = (ac / "online").read_text().strip() == "1"
    return out


@soft
def memory() -> dict:
    info = dict(re.findall(r"(\w+):\s+(\d+)", Path("/proc/meminfo").read_text()))
    return {"ram_free_gb": round(int(info["MemAvailable"]) / 1024 / 1024, 1),
            "ram_total_gb": round(int(info["MemTotal"]) / 1024 / 1024, 1)}


@soft
def media() -> dict:
    """What's playing, from the Omarchy shell's MPRIS service."""
    d = json.loads(run(["omarchy-shell", "media", "status"]))
    if not d.get("hasMedia"):
        return {"playing": False}
    return {"playing": d.get("playing", False), "player": d.get("identity"), "title": d.get("title"),
            "artist": d.get("artist")}


def now() -> dict:
    t = datetime.now().astimezone()
    return {"time": t.strftime("%H:%M"), "date": t.strftime("%A %d %B %Y"), "timezone": t.strftime("%Z %z")}


STATIC = {
    "machine": machine, "defaults": defaults, "apps": apps, "keybindings": keybindings, "shell": shell,
    "omarchy_commands": omarchy_commands, "projects": projects, "obsidian_vaults": obsidian,
    "services": services, "packages": packages, "bookmarks": bookmarks,
}
LIVE = {"now": now, "hyprland": hyprland, "herdr": herdr, "audio": audio, "media": media, "power": power,
        "memory": memory}


def collect(names: dict) -> dict:
    out = {}
    for key, fn in names.items():
        start = time.perf_counter()
        value = fn()
        if value is not None:            # None: doesn't apply on this machine
            out[key] = value
        out.setdefault("_timing_ms", {})[key] = round((time.perf_counter() - start) * 1000)
    return out
