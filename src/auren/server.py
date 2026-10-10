"""auren's tool server (MCP over stdio): the hands, as tools the brain can call.

Run by the brain's Claude Code (or Codex) process:  python -m auren.server

Every tool returns plain text (or JSON text). Errors come back as text starting
with "error:" so the brain can tell the user what went wrong instead of the
call failing. Risky tools go through auren.safety: they return a question and
a token, and only `confirm_action` in a later user turn runs them. Text that
comes from panes, the screen or the web is wrapped as untrusted.
"""

from __future__ import annotations

import json
import traceback

from mcp.server.mcpserver import Image, MCPServer

from . import safety
from .hands import browser as web, desktop, herdr, screen
from .profile import Profile

server = MCPServer(
    "auren",
    instructions="Tools for acting on the user's Omarchy/Hyprland laptop: apps, windows, herdr coding agents, "
                 "shell commands, screenshots, sound, media, reminders and system information.",
)
profile = Profile()
confirmations = safety.Confirmations()


def _run(fn, *args, **kwargs) -> str:
    try:
        out = fn(*args, **kwargs)
    except (herdr.HerdrError, desktop.DesktopError, web.BrowserError, screen.ScreenError, PermissionError,
            ValueError) as e:
        return f"error: {e}"
    except Exception as e:  # noqa: BLE001 - report, never crash the tool server
        return f"error: unexpected {type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


# --- knowing the system -------------------------------------------------------------

@server.tool()
def system_state() -> str:
    """Live snapshot: time, battery, volume, what's playing, open windows by workspace, herdr agents and panes."""
    return _run(profile.live_context)


@server.tool()
def system_lookup(topic: str, query: str = "") -> str:
    """Search the full lists: topic is one of commands (Omarchy CLI), keys (key bindings), aliases,
    functions, apps, webapps, tuis, packages, projects. query is words to match (empty lists all)."""
    if topic not in Profile.TOPICS:
        return f"error: topic must be one of {', '.join(Profile.TOPICS)}"
    return _run(profile.lookup, topic, query)


@server.tool()
def system_info(what: str) -> str:
    """One fact: battery, memory, time, disk, network, cpu, volume or media (what's playing)."""
    return _run(desktop.system_info, what)


# --- apps and windows ------------------------------------------------------------------

@server.tool()
def launch_app(app: str, workspace: str = "", follow: bool = True) -> str:
    """Open an installed app or web app by name (focuses it if already open). Optional workspace number:
    the window is put there and the user's view goes with it. Use follow=False only when the user asks to
    stay where they are ("but keep me here"). Says so if the app isn't installed."""
    return _run(desktop.launch_app, app, workspace or None, follow)


@server.tool()
def focus_window(window: str) -> str:
    """Bring a window to the front, by app name or title words."""
    return _run(desktop.focus_window, window)


@server.tool()
def move_window(window: str, workspace: str, follow: bool = False) -> str:
    """Move a window (app name or title words) to a workspace number (or 'scratchpad')."""
    return _run(desktop.move_window, window, workspace, follow)


@server.tool()
def switch_workspace(workspace: str) -> str:
    """Show a workspace: a number, 'next', 'previous' or 'back'."""
    return _run(desktop.switch_workspace, workspace)


@server.tool()
def close_window(window: str) -> str:
    """Close a window. Needs the user's confirmation (unsaved work could be lost)."""
    try:
        w = desktop.find_window(window)
    except desktop.DesktopError as e:
        return f"error: {e}"
    return json.dumps(confirmations.request("close_window", {"window": window},
                                            f"Close {w.get('class')} “{(w.get('title') or '')[:50]}”?"))


@server.tool()
def open_url(url: str, app_mode: bool = False, browser: str = "") -> str:
    """Just show a URL to the user: in the default browser (Brave), or in a named one (chrome, brave).
    app_mode=True opens it as a standalone web-app window. To read or act on a page, use browser_open."""
    return _run(desktop.open_url, url, app_mode, browser)


@server.tool()
def open_path(path: str) -> str:
    """Open a file or folder with its default app (PDF viewer, image viewer, file manager...)."""
    return _run(desktop.open_path, path)


@server.tool()
def screenshot(target: str = "window", save_to: str = "~/Pictures") -> str:
    """Take a screenshot: target window (focused window), screen (focused monitor) or region (user drags).
    Returns the file path."""
    return _run(desktop.screenshot, target, save_to)


# --- sound, media, notifications -----------------------------------------------------------

@server.tool()
def volume(action: str, amount: int = 0) -> str:
    """Speaker volume: action up, down, mute (toggle) or set; amount in percent (up/down default 5)."""
    return _run(desktop.volume, action, amount or None)


@server.tool()
def brightness(action: str, amount: int = 10) -> str:
    """Screen brightness: action up, down or set; amount in percent."""
    return _run(desktop.brightness, action, amount)


@server.tool()
def media(action: str) -> str:
    """Control the playing media: play_pause, play, pause, next or previous."""
    return _run(desktop.media, action)


@server.tool()
def notify(text: str) -> str:
    """Show a desktop notification."""
    return _run(desktop.notify, text)


@server.tool()
def set_reminder(minutes: int, text: str) -> str:
    """Desktop reminder after a number of minutes."""
    return _run(desktop.reminder, minutes, text)


@server.tool()
def toggle(feature: str) -> str:
    """Toggle: nightlight, idle (stay awake), do not disturb, bluetooth, bar or touchpad."""
    return _run(desktop.toggle, feature)


@server.tool()
def lock_screen() -> str:
    """Lock the screen. Needs the user's confirmation."""
    return json.dumps(confirmations.request("lock_screen", {}, "Lock the screen now?"))


# --- herdr: coding agents and commands ---------------------------------------------------

@server.tool()
def list_agents() -> str:
    """Coding agents running in herdr: kind, status (idle/working/blocked/done), folder, workspace and tab."""
    return _run(herdr.list_agents)


@server.tool()
def read_agent(agent: str, hint: str = "", lines: int = 60) -> str:
    """Read what an agent's pane shows. agent is a name, pane id or kind (codex, claude, opencode...);
    hint narrows it down by project folder, workspace or tab label."""
    out = _run(herdr.read_agent, agent, hint, lines)
    return out if out.startswith("error:") else safety.untrusted(out, f"herdr agent {agent}")


@server.tool()
def prompt_agent(agent: str, text: str, hint: str = "", wait: bool = True, timeout_s: int = 180) -> str:
    """Send a prompt to an agent that is already running (the user's or auren's). With wait=True returns
    its reply when it finishes (good for quick questions); use wait=False for long work."""
    out = _run(herdr.prompt_agent, agent, text, hint, wait, timeout_s)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("reply"):
        d["reply"] = safety.untrusted(d["reply"], f"reply from {agent}")
    if d.get("screen"):
        d["screen"] = safety.untrusted(d["screen"], f"screen of {agent}")
    return json.dumps(d, ensure_ascii=False)


@server.tool()
def start_agent(kind: str = "codex", folder: str = "~", name: str = "", task: str = "",
                wait: bool = False, timeout_s: int = 180) -> str:
    """Start a new coding agent (codex by default; claude, opencode, agy, grok, muse...) in its own tab of
    auren's herdr workspace, in a folder, optionally with a first task. name: a short job name for the task
    (like "lecture-notes"). For big jobs keep wait=False and tell the user it's running."""
    out = _run(herdr.start_agent, kind, folder, name, task, wait, timeout_s)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("reply"):
        d["reply"] = safety.untrusted(d["reply"], f"reply from {d.get('job')}")
    return json.dumps(d, ensure_ascii=False)


@server.tool()
def run_command(command: str, folder: str = "~", name: str = "", wait: bool = True, timeout_s: int = 60) -> str:
    """Run a shell command in its own tab of auren's herdr workspace. Returns exit code and output when
    wait=True. Commands that delete, overwrite, need root, restart services, push, or touch secrets need
    the user's confirmation first."""
    risk = safety.command_risk(command)
    if risk:
        return json.dumps(confirmations.request(
            "run_command", {"command": command, "folder": folder, "name": name, "wait": wait, "timeout_s": timeout_s},
            f"Run `{command}`? {risk[0].upper() + risk[1:]}."))
    return _run_command(command, folder, name, wait, timeout_s)


def _run_command(command: str, folder: str, name: str, wait: bool, timeout_s: int) -> str:
    out = _run(herdr.run_command, command, folder, name, wait, timeout_s)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("output_tail"):
        d["output_tail"] = safety.untrusted(d["output_tail"], f"output of {command[:40]}")
    return json.dumps(d, ensure_ascii=False)


@server.tool()
def job_status(job: str, wait_s: int = 0) -> str:
    """Result of a run_command job (exit code, output); optionally wait up to wait_s seconds."""
    out = _run(herdr.command_result, job, wait_s)
    return out if out.startswith("error:") else safety.untrusted(out, f"job {job}")


@server.tool()
def close_job(job: str) -> str:
    """Close one of auren's own herdr tabs (never the user's)."""
    return _run(herdr.close_job, job)


@server.tool()
def show_agent(agent: str, hint: str = "") -> str:
    """Switch herdr's view to an agent's pane so the user can watch it."""
    return _run(herdr.focus, agent, hint)


# --- browser (the user's real, logged-in Chrome or Brave) -----------------------------------

@server.tool()
def browser_open(url: str, browser: str = "chrome") -> str:
    """Open a page in a new auren tab of the user's real browser and read it: text plus numbered clickable
    items. url can be 'bookmark:<name or folder/name>'. browser: chrome (has the user's IITM course login
    and bookmarks) or brave (the default browser). If the browser isn't open, this starts it and waits for it
    to connect (up to ~25 s): don't launch it first."""
    out = _run(web.browser_open, url, browser)
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


@server.tool()
def browser_read() -> str:
    """Read the current auren browser tab again (text and numbered clickable items)."""
    out = _run(web.browser_read)
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


@server.tool()
def browser_click(target: str, expect_download: bool = False) -> str:
    """Click an item: its number from the last read, or words from its label. Returns the page after the
    click. expect_download=True waits for a file to finish downloading and returns its path. Clicks that
    buy, pay, send, post, submit or delete need the user's confirmation."""
    try:
        label = web.label_of(target)
    except web.BrowserError as e:
        return f"error: {e}"
    if label and web.RISKY_CLICK.search(label):
        return json.dumps(confirmations.request("browser_click", {"target": target, "expect_download": expect_download},
                                                f"Click “{label}”? That could buy, send, post or delete something."))
    out = _run(web.browser_click, target, None, expect_download)
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


@server.tool()
def browser_type(target: str, text: str, submit: bool = False) -> str:
    """Type text into a numbered field from the last read; submit=True presses Enter (e.g. a search box)."""
    out = _run(web.browser_type, target, text, submit)
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


@server.tool()
def browser_scroll(direction: str = "down") -> str:
    """Scroll the current auren tab down or up and read it again."""
    out = _run(web.browser_scroll, direction)
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


@server.tool()
def browser_close(keep_current: bool = False) -> str:
    """Close the browser tabs auren opened (never the user's). keep_current=True leaves the last page open
    for the user to look at."""
    return _run(web.browser_close, keep_current)


@server.tool()
def browser_bookmarks(query: str = "", browser_name: str = "chrome") -> str:
    """List the user's bookmarks matching words (folder/name → url)."""
    return _run(web.bookmarks, query, browser_name)


@server.tool()
def recent_downloads(minutes: int = 30, browser_name: str = "chrome") -> str:
    """Files downloaded recently (path and size)."""
    return _run(web.recent_downloads, browser_name, minutes)


# --- screen control: see the screen, use the mouse and keyboard (fallback) -------------------------

RISKY_KEYS = {"alt+f4", "ctrl+q", "ctrl+w", "ctrl+shift+w", "ctrl+shift+q", "shift+delete", "super+q"}


@server.tool()
def look_at_screen() -> list:
    """See the focused monitor as an image. Use it when other tools can't reach something (a browser
    permission popup, an app's own dialog, an app with no command) or when the user asks you to look or
    click. Coordinates for click_at and scroll_at are pixels in this image."""
    try:
        data, note = screen.look()
    except screen.ScreenError as e:
        return [f"error: {e}"]
    return [note, Image(data=data, format="jpeg")]


@server.tool()
def click_at(x: int, y: int, target: str, button: str = "left", double: bool = False) -> str:
    """Click at pixel (x, y) of the latest look_at_screen image. target: what you are clicking, in words
    (for example "Allow on Chrome's download popup"). Look again afterwards to check it worked. Clicks
    that buy, pay, send, post, submit or delete need the user's confirmation."""
    if web.RISKY_CLICK.search(target):
        return json.dumps(confirmations.request("click_at", {"x": x, "y": y, "target": target, "button": button,
                                                             "double": double}, f"Click “{target}”?"))
    desktop.osd_safe(f"Auren: clicking {target[:40]}")
    return _run(screen.click, x, y, button, double)


@server.tool()
def drag_at(x1: int, y1: int, x2: int, y2: int, target: str, hold: str = "") -> str:
    """Press the mouse at pixel (x1, y1) of the latest look_at_screen image, move to (x2, y2) and release:
    draw a shape (pick the tool first, then drag from one corner of the shape to the opposite one), move a
    slider, an item or a window, select text. hold: "shift" (in drawing apps: a perfect circle or square),
    "ctrl", "alt", or several joined with "+". target: what you are doing, in words. Look again afterwards.
    Drags that delete or send something (to the trash, onto Send) need the user's confirmation."""
    if web.RISKY_CLICK.search(target):
        return json.dumps(confirmations.request("drag_at", {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "target": target,
                                                            "hold": hold}, f"Drag to “{target}”?"))
    return _run(screen.drag, x1, y1, x2, y2, hold)


@server.tool()
def scroll_at(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
    """Scroll the mouse wheel at pixel (x, y) of the latest look_at_screen image."""
    return _run(screen.scroll, x, y, direction, amount)


@server.tool()
def type_text(text: str) -> str:
    """Type text into whatever has keyboard focus (click the field first)."""
    return _run(screen.type_text, text)


@server.tool()
def press_keys(keys: str) -> str:
    """Press a key or shortcut in the focused window: 'Enter', 'Escape', 'Tab', 'ctrl+s', 'ctrl+t'...
    Shortcuts that close windows or quit apps need confirmation."""
    if keys.strip().lower().replace(" ", "") in RISKY_KEYS:
        return json.dumps(confirmations.request("press_keys", {"keys": keys}, f"Press {keys}? It closes a window or app."))
    return _run(screen.press_keys, keys)


# --- confirmations ----------------------------------------------------------------------------

_CONFIRMABLE = {
    "close_window": lambda a: desktop.close_window(a["window"]),
    "lock_screen": lambda a: desktop.lock_screen(),
    "run_command": lambda a: _run_command(a["command"], a["folder"], a["name"], a["wait"], a["timeout_s"]),
    "click_at": lambda a: screen.click(a["x"], a["y"], a["button"], a["double"]),
    "press_keys": lambda a: screen.press_keys(a["keys"]),
    "drag_at": lambda a: screen.drag(a["x1"], a["y1"], a["x2"], a["y2"], a["hold"]),
    "browser_click": lambda a: safety.untrusted(web.browser_click(a["target"], None, a["expect_download"]), "web page"),
}


@server.tool()
def confirm_action(token: str) -> str:
    """Run an action the user has just approved. Only call this after the user said yes in their own
    reply to your question; it fails if they haven't answered yet."""
    try:
        tool, args = confirmations.redeem(token)
    except PermissionError as e:
        return f"error: {e}"
    return _run(_CONFIRMABLE[tool], args)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
