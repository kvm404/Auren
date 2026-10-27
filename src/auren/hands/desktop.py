"""Desktop and system tools: apps, windows, workspaces, screenshots, volume, media, reminders.

They use the same commands as the user's Omarchy key bindings, so auren behaves
like the desktop already does:
- apps: `omarchy-launch-or-focus <pattern> "uwsm-app -- <id>.desktop"` (focus if open, else start)
- web apps: `omarchy-launch-or-focus-webapp <name> <url>`
- windows and workspaces: Hyprland Lua dispatchers via `hyprctl dispatch 'hl.dsp…'`
- volume `omarchy-audio-output-volume`, brightness `omarchy-brightness-display`,
  media `omarchy-shell media …`, reminders `omarchy-reminder`, notifications
  `omarchy-notification-send`, screenshots `grim` (+ Omarchy's region picker)

Nothing here asks for confirmation itself; the safety layer decides which
tools need the user's OK (closing windows, locking, power).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from .. import notice
from ..profile import Profile

HOME = Path.home()

# Words people use for an app -> how to find it.
APP_WORDS = {
    "vs code": "visual studio code", "vscode": "visual studio code", "code": "visual studio code",
    "file manager": "files", "nautilus": "files", "files app": "files",
    "pdf viewer": "document viewer", "evince": "document viewer",
    "youtube music": "yt music",
    "calculator": "omacalc",
}


class DesktopError(RuntimeError):
    pass


def sh(cmd: list[str], timeout: float = 10, check: bool = True) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if check and p.returncode != 0:
        raise DesktopError(f"{cmd[0]} failed: {(p.stderr or p.stdout).strip()[:300]}")
    return p.stdout


def spawn(cmd: list[str]) -> None:
    """Start something detached from auren (it must outlive the tool call)."""
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def dispatch(lua: str) -> None:
    out = sh(["hyprctl", "dispatch", lua]).strip()
    if out and out != "ok":
        raise DesktopError(f"hyprland: {out[:200]}")


def _lua(s: str) -> str:
    return json.dumps(s)   # a JSON string is a valid Lua string literal


# --- scratchpad -------------------------------------------------------------------

def monitors() -> list[dict]:
    return json.loads(sh(["hyprctl", "monitors", "-j"]))


def _showing_special() -> str:
    """The special workspace showing on the focused monitor ("special:scratchpad"), or ""."""
    for m in monitors():
        special = (m.get("specialWorkspace") or {}).get("name") or ""
        if m.get("focused") and special.startswith("special:"):
            return special
    return ""


def hide_scratchpad() -> bool:
    """If a special workspace (the scratchpad, where herdr lives) is showing, hide it, so the next
    window opens on the user's normal workspace instead of inside the scratchpad."""
    special = _showing_special()
    if special:
        dispatch(f"hl.dsp.workspace.toggle_special({_lua(special.removeprefix('special:'))})")
        time.sleep(0.15)
        return True
    return False


# --- herdr's window ----------------------------------------------------------------

def _parent(pid: int) -> int:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return 0
    return int(stat.rsplit(")", 1)[1].split()[1])     # the name in (…) may hold spaces


_HERDR_VALUE_OPTIONS = {"--session", "--remote", "--remote-keybindings", "--machine"}


def _herdr_clients() -> list[int]:
    """herdr's terminal clients: `herdr [--session NAME …]` or `herdr session attach NAME`, not
    `herdr server` or a one-off command like `herdr agent list`."""
    pids = []
    for line in sh(["pgrep", "-a", "-x", "herdr"], check=False).splitlines():
        pid, _, args = line.partition(" ")
        words, skip = [], False
        for arg in args.split()[1:]:
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
    """The windows of the terminal process herdr runs in (an ancestor of a herdr client), or None if
    herdr isn't open in any window."""
    by_pid: dict[int, list[dict]] = {}
    for w in windows():
        by_pid.setdefault(w.get("pid"), []).append(w)
    for pid in _herdr_clients():
        for _ in range(6):                 # foot -> herdr, or foot -> shell -> herdr
            pid = _parent(pid)
            if pid <= 1:
                break
            if pid in by_pid:
                return by_pid[pid]
    return None


def herdr_is_open() -> bool:
    return _herdr_terminal() is not None


def herdr_window() -> dict | None:
    """The terminal window herdr runs in. A terminal that runs all its windows in one process
    (foot --server, Ghostty) can't tell which window is herdr's; then the one on a special workspace
    (the scratchpad) is taken, or none."""
    wins = _herdr_terminal() or []
    if len(wins) > 1:
        wins = [w for w in wins if w["workspace"]["name"].startswith("special:")]
    return wins[0] if len(wins) == 1 else None


def open_herdr(timeout: float = 8) -> dict | None:
    """Open herdr in a new terminal window on the user's workspace, as Omarchy's Super+Ctrl+Return
    does (it attaches to the running session), and wait for the window. None if it didn't appear.
    The new window is told apart by its address, which works even when the terminal runs all its
    windows in one process."""
    hide_scratchpad()
    before = {w["address"] for w in windows()}
    spawn(["omarchy-launch-terminal", "herdr"])
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.2)
        new = [w for w in windows() if w["address"] not in before]
        if new and herdr_is_open():
            time.sleep(0.8)              # let the client attach before herdr's view is switched
            return new[0]
    return None


def show_herdr(w: dict | None = None) -> bool:
    """Bring herdr's window into view and focus it, showing the special workspace it lives on (the
    scratchpad) if that's hidden. w: the window, if known. False if herdr's window can't be found."""
    w = w or herdr_window()
    if w is None:
        return False
    ws = w["workspace"]["name"]
    showing = {(m.get("specialWorkspace") or {}).get("name") for m in monitors()}
    if ws.startswith("special:") and ws not in showing:     # already showing on some monitor: leave it there
        dispatch(f"hl.dsp.workspace.toggle_special({_lua(ws.removeprefix('special:'))})")
    dispatch(f"hl.dsp.focus({{ window = {_lua('address:' + w['address'])} }})")
    return True


# --- windows ---------------------------------------------------------------------

def windows() -> list[dict]:
    return json.loads(sh(["hyprctl", "clients", "-j"]))


def find_window(query: str) -> dict:
    """A window by app name, class or title words ("spotify", "brave", "the github tab")."""
    q = APP_WORDS.get(query.strip().lower(), query.strip().lower())
    wins = [w for w in windows() if w.get("mapped", True)]
    def score(w: dict) -> int:
        cls = (w.get("class") or "").lower()
        init = (w.get("initialClass") or "").lower()
        title = (w.get("title") or "").lower()
        if q in (cls, init):
            return 3
        if re.search(rf"\b{re.escape(q)}\b", cls) or re.search(rf"\b{re.escape(q)}\b", init):
            return 2
        if re.search(rf"\b{re.escape(q)}\b", title):
            return 1
        return 0
    hits = sorted((w for w in wins if score(w)), key=lambda w: (-score(w), w.get("focusHistoryID", 99)))
    if not hits:
        open_apps = sorted({w.get("class") for w in wins if w.get("class")})
        raise DesktopError(f"no window matches '{query}'. Open windows: {', '.join(open_apps) or 'none'}")
    return hits[0]


def _describe(w: dict) -> str:
    return f"{w.get('class')} “{(w.get('title') or '')[:60]}” on workspace {w['workspace']['name']}"


def focus_window(window: str) -> str:
    w = find_window(window)
    dispatch(f"hl.dsp.focus({{ window = {_lua('address:' + w['address'])} }})")
    return f"focused {_describe(w)}"


def move_window(window: str, workspace: str | int, follow: bool = False) -> str:
    w = find_window(window)
    ws = str(workspace).strip().lower().replace("workspace", "").strip()
    ws = {"scratchpad": "special:scratchpad"}.get(ws, ws)
    dispatch(f"hl.dsp.window.move({{ workspace = {_lua(ws)}, follow = {'true' if follow else 'false'}, "
             f"window = {_lua('address:' + w['address'])} }})")
    return f"moved {w.get('class')} to workspace {ws}"


def switch_workspace(workspace: str | int) -> str:
    ws = str(workspace).strip().lower().replace("workspace", "").strip()
    ws = {"next": "e+1", "previous": "e-1", "last": "previous", "back": "previous"}.get(ws, ws)
    dispatch(f"hl.dsp.focus({{ workspace = {_lua(ws)} }})")
    return f"switched to workspace {workspace}"


def close_window(window: str | dict) -> str:
    """Ask a window to close, then check it really did: apps with several tabs or unsaved work often
    answer with their own "are you sure?" dialog instead of closing. `window` is words to find it by, or
    the window itself (as found when the user was asked)."""
    if isinstance(window, dict):
        w = window
        if w["address"] not in {x["address"] for x in windows()}:
            raise DesktopError(f"{w.get('class')} is already closed")
    else:
        w = find_window(window)
    dispatch(f"hl.dsp.window.close({{ window = {_lua('address:' + w['address'])} }})")
    for _ in range(8):
        time.sleep(0.25)
        if w["address"] not in {x["address"] for x in windows()}:
            return f"closed {_describe(w)}"
    return (f"asked {w.get('class')} to close, but it is still open: it is probably showing its own "
            "confirmation dialog (for example 'Close all tabs?'). Tell the user to answer it.")


# --- apps --------------------------------------------------------------------------

# Words for a kind of app: they open whichever app the user has set as the default for it.
ROLE_WORDS = {"browser": "browser", "web browser": "browser", "the browser": "browser", "terminal": "terminal",
              "file manager": "file_manager", "files": "file_manager", "editor": "editor", "text editor": "editor"}


def find_app(name: str, profile: Profile | None = None) -> dict:
    """An installed app or web app by spoken name, or a role ("browser", "terminal") for the user's default
    app. Raises with the closest options when not installed."""
    static = (profile or Profile()).static()
    apps = static["apps"]
    q = name.strip().lower()
    pool = [dict(a, kind="webapp") for a in apps["webapps"]] + [dict(a, kind="app") for a in apps["apps"]] \
        + [dict(a, kind="tui") for a in apps["tuis"]]
    role = ROLE_WORDS.get(q)
    default = (static.get("defaults") or {}).get(role) if role else None
    if isinstance(default, str):
        hit = next((a for a in pool if a["id"].lower() == default.lower()), None)
        if hit:
            return hit
    q = APP_WORDS.get(q, q)
    for test in (lambda a: a["name"].lower() == q or a["id"].lower() == q,
                 lambda a: a["name"].lower().startswith(q),
                 lambda a: re.search(rf"\b{re.escape(q)}\b", a["name"].lower() + " " + a["id"].lower())):
        hits = [a for a in pool if test(a)]
        if hits:
            return hits[0]
    missing = [m for m in apps.get("not_installed", []) if m.lower() == q]
    if missing:
        raise DesktopError(f"{missing[0]} is not installed")
    raise DesktopError(f"no installed app called '{name}'")


def _wait_new_window(before: set[str], timeout: float = 15) -> dict | None:
    end = time.time() + timeout
    while time.time() < end:
        new = [w for w in windows() if w["address"] not in before]
        if new:
            return new[0]
        time.sleep(0.25)
    return None


def launch_app(app: str, workspace: str | int | None = None, follow: bool = True) -> str:
    """Open an app (or focus it if it's already open). With a workspace, the window is moved there
    (and the view follows it unless follow=False)."""
    a = find_app(app)
    hide_scratchpad()
    before = {w["address"] for w in windows()}
    if a["kind"] == "webapp":
        spawn(["omarchy-launch-or-focus-webapp", a["name"], a["url"]])
    elif a["kind"] == "tui":
        spawn(["omarchy-launch-or-focus-tui", a["command"]])
    else:
        pattern = a.get("wmclass") or a["name"].split()[0]
        spawn(["omarchy-launch-or-focus", pattern, f"uwsm-app -- {a['id']}.desktop"])
    msg = f"opened {a['name']}"
    if workspace is not None:
        w = _wait_new_window(before)
        if w is None:     # it was already open and got focused instead
            try:
                w = find_window(a.get("wmclass") or a["name"])
            except DesktopError:
                return msg + f" (couldn't find its window to move to workspace {workspace})"
        dispatch(f"hl.dsp.window.move({{ workspace = {_lua(str(workspace))}, follow = {'true' if follow else 'false'}, "
                 f"window = {_lua('address:' + w['address'])} }})")
        msg += f" on workspace {workspace}"
    return msg


def open_url(url: str, app_mode: bool = False, browser: str = "") -> str:
    """Open a URL in the default browser, or in a named Chromium one (chrome, brave, chromium, ...)."""
    from . import browsers
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.I):
        url = "https://" + url
    hide_scratchpad()
    if browser:
        try:
            kind = browsers.resolve(browser)
        except browsers.UnknownBrowser as e:
            raise DesktopError(str(e)) from None
        exe = browsers.command(kind)
        if exe is None:
            raise DesktopError(f"{kind.name} is not installed")
        spawn(["uwsm-app", "--", exe, *([f"--app={url}"] if app_mode else [url])])
        return f"opened {url} in {browser}"
    spawn(["omarchy-launch-webapp" if app_mode else "omarchy-launch-browser", url])
    return f"opened {url}"


def open_path(path: str) -> str:
    p = Path(path).expanduser()
    if not p.exists():
        raise DesktopError(f"{p} does not exist")
    hide_scratchpad()
    spawn(["xdg-open", str(p)])
    return f"opened {p}"


# --- screenshots -------------------------------------------------------------------

def screenshot(target: str = "window", save_to: str = "~/Pictures") -> str:
    """target: window (the focused one), screen (focused monitor) or region (the user drags)."""
    folder = Path(save_to).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot-{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    target = target.lower()
    notice.clear()                       # not Auren's own status in the picture
    if target in ("region", "area", "selection"):
        out = sh(["omarchy-capture-screenshot", "region", "save"], timeout=120).strip()
        if not out:
            return "screenshot cancelled"
        shutil.move(out, path)
    else:
        if target in ("window", "this window", "active"):
            w = json.loads(sh(["hyprctl", "activewindow", "-j"]) or "{}")
            if not w:
                raise DesktopError("no focused window")
            (x, y), (wd, ht) = w["at"], w["size"]
        else:
            m = next(m for m in json.loads(sh(["hyprctl", "monitors", "-j"])) if m.get("focused"))
            x, y = m["x"], m["y"]
            wd, ht = round(m["width"] / m["scale"]), round(m["height"] / m["scale"])
        sh(["grim", "-g", f"{x},{y} {wd}x{ht}", str(path)])
    return str(path)


# --- sound, screen, media ------------------------------------------------------------

def volume(action: str, amount: int | None = None) -> str:
    """action: up, down, mute (toggle) or set (to amount %)."""
    action = action.lower()
    if action == "set":
        cur = int(float(re.search(r"([\d.]+)", sh(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"])).group(1)) * 100)
        delta = int(amount or 0) - cur
        arg = f"{delta:+d}"
    else:
        arg = {"up": f"+{amount or 5}", "down": f"-{amount or 5}", "mute": "mute-toggle",
               "unmute": "mute-toggle"}.get(action)
        if arg is None:
            raise DesktopError(f"unknown volume action {action}")
    sh(["omarchy-audio-output-volume", arg])
    return "volume " + sh(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"]).strip().lower()


def brightness(action: str, amount: int = 10) -> str:
    arg = {"up": f"+{amount}%", "down": f"{amount}%-", "set": f"{amount}%"}.get(action.lower())
    if arg is None:
        raise DesktopError(f"unknown brightness action {action}")
    sh(["omarchy-brightness-display", arg])
    return f"brightness {action} {amount}%"


def media(action: str) -> str:
    """play_pause, play, pause, next or previous, through the Omarchy shell's media service."""
    method = {"play_pause": "playPause", "toggle": "playPause", "play": "play", "pause": "pause",
              "next": "next", "previous": "previous", "prev": "previous"}.get(action.lower())
    if method is None:
        raise DesktopError(f"unknown media action {action}")
    status = json.loads(sh(["omarchy-shell", "media", "status"]))
    if not status.get("hasPlayer"):
        return "nothing is playing (no media player open)"
    sh(["omarchy-shell", "media", method])
    return f"{method} sent to {status.get('identity') or 'the player'}"


# --- notifications, reminders, toggles -------------------------------------------------

def notify(text: str) -> str:
    sh(["omarchy-notification-send", "--app-name", "auren", text])
    return "notification shown"


def reminder(minutes: int, text: str) -> str:
    sh(["omarchy-reminder", str(int(minutes)), text])
    return f"reminder in {int(minutes)} min: {text}"


TOGGLES = {"nightlight": ["omarchy-toggle-nightlight"], "night light": ["omarchy-toggle-nightlight"],
           "idle": ["omarchy-toggle-idle"], "stay awake": ["omarchy-toggle-idle", "stay-awake"],
           "do not disturb": ["omarchy-toggle-notification-silencing"],
           "notifications": ["omarchy-toggle-notification-silencing"],
           "bluetooth": ["omarchy-bluetooth-power", "toggle"], "bar": ["omarchy-toggle-bar"],
           "touchpad": ["omarchy-toggle-touchpad"]}


def toggle(feature: str) -> str:
    cmd = TOGGLES.get(feature.strip().lower())
    if cmd is None:
        raise DesktopError(f"unknown toggle {feature}; known: {', '.join(TOGGLES)}")
    sh(cmd)
    return f"toggled {feature}"


def lock_screen() -> str:
    spawn(["omarchy-system-lock"])
    return "locking the screen"


# --- information ------------------------------------------------------------------------

def system_info(what: str) -> str:
    what = what.lower()
    live = Profile().live()
    if "batter" in what or "power" in what:
        p = live["power"]
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
        return sh(["omarchy-network-status"], check=False).strip() or "network status unavailable"
    if "cpu" in what or "load" in what:
        return sh(["omarchy-system-stats"], check=False).strip() or "unavailable"
    if "playing" in what or "music" in what or "media" in what:
        md = live["media"]
        return f"playing “{md['title']}” by {md.get('artist')} in {md.get('player')}" if md.get("title") else "nothing playing"
    if "volume" in what:
        a = live["audio"]
        return f"volume {a['volume_percent']}%" + (" (muted)" if a["muted"] else "")
    raise DesktopError("ask for battery, memory, time, disk, network, cpu, volume or media")
