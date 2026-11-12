"""The desktop tools on macOS (hands/desktop.py has the Omarchy ones and the shared parts).

- apps: `open -a` (focuses it if it's running), found in the app folders by profile/collect
- windows: mac/windows.py (Quartz's window list, the Accessibility API)
- Spaces: macOS has no API for them; next and previous press Ctrl+Right and Ctrl+Left, a number presses
  Ctrl+<number> (on when "Switch to Desktop N" is turned on in Keyboard Shortcuts), moving windows isn't possible
- URLs and files: `open`, screenshots: `screencapture`
- volume, notifications, media: AppleScript; brightness: the brightness keys
- reminders: a detached `sleep` then a notification
- herdr: in a Terminal window (Ghostty, iTerm or Terminal); without herdr, agents start in one too
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .. import notice
from ..hands.desktop import APP_WORDS, DesktopError, find_app, find_window, sh, spawn
from ..profile import Profile
from . import windows as macwin

__all__ = ["monitors", "hide_scratchpad", "herdr_is_open", "herdr_window", "open_herdr", "show_herdr", "windows",
           "focus_window", "move_window", "switch_workspace", "close_window", "launch_app", "open_url", "open_path",
           "screenshot", "volume", "brightness", "media", "notify", "reminder", "TOGGLES", "toggle", "lock_screen",
           "system_info", "osascript", "terminal_run", "start_in_terminal", "APP_WORDS"]

HOME = Path.home()
APP_WORDS = {**APP_WORDS, "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
             "file manager": "finder", "files": "finder", "files app": "finder", "settings": "system settings",
             "preferences": "system settings", "calculator": "calculator", "pdf viewer": "preview"}


def osascript(script: str, timeout: float = 10) -> str:
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        err = (p.stderr or p.stdout).strip()
        if "-1743" in err or "Not authorized" in err:
            err += " (allow Auren under System Settings → Privacy & Security → Automation)"
        raise DesktopError(f"AppleScript failed: {err[:300]}")
    return p.stdout.strip()


def _q(s: str) -> str:
    """An AppleScript string literal."""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


# --- displays, Spaces, herdr ---------------------------------------------------------------------

def monitors() -> list[dict]:
    m = macwin.main_display()
    return [{"name": "main", "focused": True, "x": m["x"], "y": m["y"], "width": round(m["width"] * m["scale"]),
             "height": round(m["height"] * m["scale"]), "scale": m["scale"], "specialWorkspace": {}}]


def hide_scratchpad() -> bool:
    return False                 # no scratchpad on macOS


def windows() -> list[dict]:
    try:
        return macwin.list_windows()
    except Exception as e:  # noqa: BLE001 - pyobjc missing or the window server not reachable
        raise DesktopError(f"can't list windows: {e}") from None


def _parent(pid: int) -> int:
    try:
        return int(sh(["ps", "-o", "ppid=", "-p", str(pid)], check=False).strip() or 0)
    except ValueError:
        return 0


def _herdr_clients() -> list[int]:
    from ..hands.desktop import _HERDR_VALUE_OPTIONS
    pids = []
    for line in sh(["pgrep", "-lf", "herdr"], check=False).splitlines():
        pid, _, args = line.partition(" ")
        argv = args.split()
        if not argv or Path(argv[0]).name != "herdr":
            continue
        words, skip = [], False
        for arg in argv[1:]:
            if skip:
                skip = False
            elif arg.startswith("-"):
                skip = arg in _HERDR_VALUE_OPTIONS
            else:
                words.append(arg)
        if pid.isdigit() and (not words or words[:2] == ["session", "attach"]):
            pids.append(int(pid))
    return pids


def _herdr_terminal() -> list[dict] | None:
    by_pid: dict[int, list[dict]] = {}
    for w in windows():
        by_pid.setdefault(w.get("pid"), []).append(w)
    for pid in _herdr_clients():
        for _ in range(8):          # Terminal -> login -> shell -> herdr
            pid = _parent(pid)
            if pid <= 1:
                break
            if pid in by_pid:
                return by_pid[pid]
    return None


def herdr_is_open() -> bool:
    return bool(_herdr_clients())


def herdr_window() -> dict | None:
    """herdr's terminal window. Terminals run all their windows in one process, so a title that names herdr
    picks it; else the only window of that terminal."""
    wins = _herdr_terminal() or []
    named = [w for w in wins if "herdr" in w.get("title", "").lower()]
    wins = named or wins
    return wins[0] if len(wins) == 1 else None


def terminal_app() -> str:
    """The terminal to open: desktop.terminal in config.toml, else Ghostty, iTerm or Terminal (whichever is here)."""
    from ..settings import current as settings
    try:
        want = str(settings()["desktop.terminal"])
    except KeyError:
        want = "auto"
    if want != "auto":
        return want
    for name in ("Ghostty", "iTerm"):
        if any((d / f"{name}.app").exists() for d in (Path("/Applications"), HOME / "Applications")):
            return name
    return "Terminal"


def terminal_run(command: str, folder: str | Path = "~", title: str = "") -> str:
    """Open a new terminal window running `command` in `folder` (the window stays open after it ends)."""
    folder = Path(str(folder)).expanduser()
    line = f"cd {shlex.quote(str(folder))} && {command}"
    app = terminal_app()
    if app == "Ghostty":
        spawn(["open", "-na", "Ghostty", "--args", f"--working-directory={folder}",
               *([f"--title={title}"] if title else []), "-e", "/bin/zsh", "-lc", f"{command}; exec /bin/zsh -l"])
    elif app == "iTerm":
        osascript(f'tell application "iTerm" to create window with default profile command {_q("/bin/zsh -lc " + shlex.quote(line + "; exec /bin/zsh -l"))}')
        osascript('tell application "iTerm" to activate')
    else:
        osascript(f'tell application "Terminal" to do script {_q(line)}')
        osascript('tell application "Terminal" to activate')
    return app


def open_herdr(timeout: float = 8) -> dict | None:
    before = {w["address"] for w in windows()}
    terminal_run("herdr", title="herdr")
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.2)
        new = [w for w in windows() if w["address"] not in before]
        if new and herdr_is_open():
            time.sleep(0.8)
            return new[0]
    return None


def show_herdr(w: dict | None = None) -> bool:
    w = w or herdr_window()
    if w is None:
        return False
    macwin.focus(w)
    return True


def start_in_terminal(kind: str, folder: str, task: str = "") -> str:
    """Without herdr: start a coding agent in a new terminal window, with its first task."""
    from ..hands.herdr import KINDS
    from ..integrations import AGENT_COMMANDS, agent_installed, default_agent
    asked = (kind or default_agent()).strip().lower()
    kind = KINDS.get(asked)                 # only coding agents: never just any command that's installed
    if kind is None:
        raise DesktopError(f"{asked} isn't a coding agent Auren knows ({', '.join(sorted(set(KINDS.values())))})")
    if not agent_installed(kind):
        raise DesktopError(f"{kind} isn't installed")
    command = AGENT_COMMANDS.get(kind, kind)
    path = Path(folder).expanduser()
    if not path.is_dir():
        raise DesktopError(f"{path} isn't a folder")
    app = terminal_run(command + (f" {shlex.quote(task)}" if task else ""), path, title=kind)
    return json.dumps({"started": kind, "folder": str(path), "where": f"a new {app} window",
                       "note": "herdr isn't installed, so Auren can't read or prompt this agent later: the user "
                               "follows it in that window"})


# --- windows ---------------------------------------------------------------------------------------

def _describe(w: dict) -> str:
    return f"{w.get('class')} “{(w.get('title') or '')[:60]}”"


def focus_window(window: str) -> str:
    try:
        w = find_window(window)
    except DesktopError:
        app = _running_app(window)          # minimized, or on another Space: the app still comes forward
        if app is None:
            raise
        macwin.activate(app)
        return f"brought {window} to the front"
    macwin.focus(w)
    return f"focused {_describe(w)}"


def _running_app(name: str) -> int | None:
    from AppKit import NSWorkspace
    q = APP_WORDS.get(name.strip().lower(), name.strip().lower())
    for a in NSWorkspace.sharedWorkspace().runningApplications():
        if str(a.localizedName() or "").lower() == q or str(a.bundleIdentifier() or "").lower() == q:
            return int(a.processIdentifier())
    return None


def move_window(window: str, workspace: str | int, follow: bool = False) -> str:
    raise DesktopError("macOS has no way for Auren to move windows between desktops (Spaces); the user can drag "
                       "it in Mission Control")


def switch_workspace(workspace: str | int) -> str:
    from . import screen
    ws = str(workspace).strip().lower().replace("workspace", "").replace("desktop", "").strip()
    keys = {"next": "ctrl+right", "e+1": "ctrl+right", "previous": "ctrl+left", "e-1": "ctrl+left",
            "back": "ctrl+left", "last": "ctrl+left"}.get(ws)
    if keys is None and ws.isdigit() and 1 <= int(ws) <= 9:
        keys = f"ctrl+{ws}"
    if keys is None:
        raise DesktopError("on macOS, say next, previous or a desktop number 1-9")
    screen.press_code(keys)
    note = "" if keys.endswith(("left", "right")) else \
        " (works when 'Switch to Desktop N' is on in System Settings → Keyboard → Keyboard Shortcuts → Mission Control)"
    return f"switched to desktop {workspace}{note}"


def close_window(window: str | dict) -> str:
    if isinstance(window, dict):
        w = window
        if w["address"] not in {x["address"] for x in windows()}:
            raise DesktopError(f"{w.get('class')} is already closed")
    else:
        w = find_window(window)
    if not macwin.close(w):
        macwin.focus(w)
        from . import screen
        screen.press_keys("cmd+w")
    for _ in range(8):
        time.sleep(0.25)
        if w["address"] not in {x["address"] for x in windows()}:
            return f"closed {_describe(w)}"
    return (f"asked {w.get('class')} to close, but it is still open: it is probably showing its own "
            "confirmation dialog (for example 'Save changes?'). Tell the user to answer it.")


# --- apps, URLs, files ----------------------------------------------------------------------------

def launch_app(app: str, workspace: str | int | None = None, follow: bool = True) -> str:
    a = find_app(app)
    spawn(["open", "-a", a.get("path") or a["name"]])
    msg = f"opened {a['name']}"
    if workspace is not None:
        msg += " (macOS can't put it on a particular desktop)"
    return msg


def open_url(url: str, app_mode: bool = False, browser: str = "") -> str:
    from ..hands import browsers
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    if browser or app_mode:
        try:
            kind = browsers.resolve(browser or None)
        except browsers.UnknownBrowser as e:
            if browser:
                raise DesktopError(str(e)) from None
            kind = None
        exe = browsers.command(kind) if kind else None
        if exe:
            spawn([exe, *([f"--app={url}"] if app_mode else [url])])
            return f"opened {url}" + (f" in {kind.name}" if browser else "")
        if browser:
            raise DesktopError(f"{kind.name} is not installed")
    spawn(["open", url])
    return f"opened {url}"


def open_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise DesktopError(f"{p} does not exist")
    spawn(["open", str(p)])
    return f"opened {p}"


def screenshot(target: str = "window", save_to: str = "~/Pictures") -> str:
    folder = Path(save_to).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    target = target.lower()
    notice.clear()
    if target in ("region", "area", "selection"):
        sh(["screencapture", "-i", "-x", str(path)], timeout=120, check=False)
        return str(path) if path.exists() else "screenshot cancelled"
    if target in ("window", "this window", "active"):
        w = macwin.active_window()
        if not w:
            raise DesktopError("no focused window")
        sh(["screencapture", "-x", "-o", "-l", w["address"], str(path)])
    else:
        sh(["screencapture", "-x", "-m", str(path)])
    return str(path)


# --- sound, screen, media -----------------------------------------------------------------------------

def _volume_now() -> tuple[int, bool]:
    out = osascript("set s to get volume settings\nreturn (output volume of s as text) & \",\" & (output muted of s as text)")
    vol, _, muted = out.partition(",")
    return int(float(vol or 0)), muted.strip() == "true"


def volume(action: str, amount: int | None = None) -> str:
    action = action.lower()
    cur, muted = _volume_now()
    if action == "set":
        osascript(f"set volume output volume {max(0, min(100, int(amount or 0)))}")
    elif action in ("up", "down"):
        step = int(amount or 5) * (1 if action == "up" else -1)
        osascript(f"set volume output volume {max(0, min(100, cur + step))}")
    elif action in ("mute", "unmute"):
        osascript(f"set volume output muted {'false' if muted or action == 'unmute' else 'true'}")
    else:
        raise DesktopError(f"unknown volume action {action}")
    cur, muted = _volume_now()
    return f"volume {cur}%" + (" (muted)" if muted else "")


def brightness(action: str, amount: int = 10) -> str:
    """macOS has no brightness command: the brightness keys, about 6 % a press (`brightness` from Homebrew
    sets an exact level when it's installed)."""
    action = action.lower()
    if action == "set" and shutil.which("brightness"):
        sh(["brightness", f"{max(0, min(100, amount)) / 100:.2f}"])
        return f"brightness set to {amount}%"
    if action not in ("up", "down"):
        raise DesktopError("on macOS, brightness goes up or down (to set a level, install `brightness` with Homebrew)")
    code = 144 if action == "up" else 145
    for _ in range(max(1, round(amount / 6))):
        osascript(f'tell application "System Events" to key code {code}')
    return f"brightness {action}"


PLAYERS = ("Spotify", "Music")


def _player() -> str | None:
    """The media app that's playing, else one that's open."""
    open_ = [p for p in PLAYERS if osascript(f'application {_q(p)} is running') == "true"]
    for p in open_:
        try:
            if osascript(f'tell application {_q(p)} to return player state as text') == "playing":
                return p
        except DesktopError:
            continue
    return open_[0] if open_ else None


def media(action: str) -> str:
    cmd = {"play_pause": "playpause", "toggle": "playpause", "play": "play", "pause": "pause",
           "next": "next track", "previous": "previous track", "prev": "previous track"}.get(action.lower())
    if cmd is None:
        raise DesktopError(f"unknown media action {action}")
    player = _player()
    if player is None:
        return "nothing is playing (Spotify and Music aren't open)"
    osascript(f"tell application {_q(player)} to {cmd}")
    return f"{cmd} sent to {player}"


# --- notifications, reminders, toggles ------------------------------------------------------------------

def notify(text: str) -> str:
    osascript(f'display notification {_q(text)} with title "Auren"')
    return "notification shown"


def reminder(minutes: int, text: str) -> str:
    script = f'display notification {_q(text)} with title "Reminder" sound name "Glass"'
    spawn(["/bin/sh", "-c", f"sleep {int(minutes) * 60} && osascript -e {shlex.quote(script)}"])
    return f"reminder in {int(minutes)} min: {text}"


TOGGLES = {"dark mode": "dark", "dark": "dark", "light mode": "dark", "bluetooth": "bluetooth",
           "stay awake": "awake", "idle": "awake", "caffeinate": "awake"}
_awake: list[subprocess.Popen] = []


def toggle(feature: str) -> str:
    what = TOGGLES.get(feature.strip().lower())
    if what == "dark":
        osascript('tell application "System Events" to tell appearance preferences to set dark mode to not dark mode')
        return "toggled dark mode"
    if what == "bluetooth":
        if not shutil.which("blueutil"):
            raise DesktopError("switching Bluetooth needs blueutil (brew install blueutil)")
        on = sh(["blueutil", "--power"]).strip() == "1"
        sh(["blueutil", "--power", "0" if on else "1"])
        return f"bluetooth {'off' if on else 'on'}"
    if what == "awake":
        running = [p for p in _awake if p.poll() is None]
        if running:
            for p in running:
                p.terminate()
            _awake.clear()
            return "the Mac may sleep again"
        _awake.append(subprocess.Popen(["caffeinate", "-di"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL))
        return "keeping the Mac awake"
    raise DesktopError(f"unknown toggle {feature}; on macOS: dark mode, bluetooth, stay awake")


def lock_screen() -> str:
    from . import screen
    screen.press_code("ctrl+cmd+q")
    return "locking the screen"


# --- information --------------------------------------------------------------------------------------

def system_info(what: str) -> str:
    what = what.lower()
    live = Profile().live()
    if "batter" in what or "power" in what:
        p = live.get("power", {})
        if p.get("battery_percent") is None:
            return "no battery (a desktop Mac)"
        return f"battery {p.get('battery_percent')}%, {p.get('battery_status', '').lower()}" + \
            (", plugged in" if p.get("plugged_in") else "")
    if "mem" in what or "ram" in what:
        m = live["memory"]
        return f"{m['ram_free_gb']} GB of {m['ram_total_gb']} GB RAM free"
    if "time" in what or "date" in what:
        n = live["now"]
        return f"{n['time']}, {n['date']}"
    if "disk" in what or "storage" in what or "space" in what:
        total, used, free = shutil.disk_usage(HOME)
        return f"{free / 1e9:.0f} GB free of {total / 1e9:.0f} GB on the home disk"
    if "network" in what or "wifi" in what or "internet" in what:
        out = sh(["networksetup", "-getairportnetwork", "en0"], check=False).strip()
        return out or "network status unavailable"
    if "cpu" in what or "load" in what:
        return sh(["sysctl", "-n", "vm.loadavg"], check=False).strip() or "unavailable"
    if "playing" in what or "music" in what or "media" in what:
        md = live.get("media", {})
        return f"playing “{md['title']}” by {md.get('artist')} in {md.get('player')}" if md.get("title") else "nothing playing"
    if "volume" in what:
        a = live["audio"]
        return f"volume {a['volume_percent']}%" + (" (muted)" if a["muted"] else "")
    raise DesktopError("ask for battery, memory, time, disk, network, cpu, volume or media")
