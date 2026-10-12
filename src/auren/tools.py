"""The tool runtime: every tool the brain can call, and the state they share.

It runs inside the daemon (or `auren chat`) for as long as that runs, and serves tool calls on a Unix
socket. The brains reach it through a thin MCP adapter (auren.server) that they start themselves, so
Claude, Codex and any later brain share one browser tab, one screenshot and one set of confirmations.

    {"session": "daemon", "tool": "launch_app", "args": {"app": "firefox"}}      one line in
    {"content": [{"type": "text", "text": "..."}]}                                 one line out

Sessions keep turns apart: the daemon is one, each `auren chat` another. The caller starts a turn
for every user message (`new_turn`), which is what makes a confirmation from an earlier message
redeemable. `cancel` refuses further tool calls until the next message. `acted` says whether
something that shouldn't be repeated has run in this turn (then a backup brain mustn't start over).

Every tool returns plain text (or JSON text). Errors come back as text starting with "error:" so the
brain can tell the user what went wrong. Risky tools go through auren.safety: they return a question
and a token, and only `confirm_action` in a later user turn runs them. Text that comes from panes, the
screen or the web is wrapped as untrusted.
"""

from __future__ import annotations

import base64
import contextvars
import inspect
import json
import logging
import os
import socketserver
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import safety
from .hands import browser as web
from .hands import desktop, herdr, screen
from .profile import Profile

log = logging.getLogger("auren.tools")


@dataclass
class Image:
    data: bytes
    format: str = "jpeg"


@dataclass
class Tool:
    fn: Callable
    repeatable: bool        # safe to run again if a backup brain starts the request over


TOOLS: dict[str, Tool] = {}


def tool(repeatable: bool = False):
    def register(fn: Callable) -> Callable:
        TOOLS[fn.__name__] = Tool(fn, repeatable)
        return fn
    return register


# --- the runtime ---------------------------------------------------------------------------------

@dataclass
class Session:
    turn: int = 0
    cancelled: bool = False
    acted: bool = False


@dataclass
class _Call:
    runtime: Runtime
    session: str
    asked: bool = False                     # the tool asked for confirmation instead of acting


_call: contextvars.ContextVar[_Call] = contextvars.ContextVar("auren_tool_call")


class Runtime:
    def __init__(self, socket: Path):
        self.socket = socket
        self.profile = Profile()
        self.confirmations = safety.Confirmations()
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingUnixStreamServer | None = None

    def _session(self, sid: str) -> Session:
        with self._lock:
            return self._sessions.setdefault(sid, Session())

    def new_turn(self, sid: str) -> int:
        """A new user message in this session."""
        s = self._session(sid)
        s.turn, s.cancelled, s.acted = s.turn + 1, False, False
        return s.turn

    def cancel(self, sid: str) -> None:
        self._session(sid).cancelled = True

    def acted(self, sid: str) -> bool:
        return self._session(sid).acted

    def summary(self, sid: str) -> str:
        return self.confirmations.summary(sid)

    def awaiting(self, sid: str) -> safety.Pending | None:
        return self.confirmations.latest(sid)

    def call(self, sid: str, name: str, args: dict) -> list[str | Image]:
        s = self._session(sid)
        if s.cancelled:
            return ["error: the user cancelled this request. Stop now: don't call more tools."]
        t = TOOLS.get(name)
        if t is None:
            return [f"error: no tool called {name}"]
        c = _Call(self, sid)
        token = _call.set(c)
        try:
            out = t.fn(**args)
        except TypeError as e:
            out = f"error: wrong arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001 - report, never crash the runtime
            log.exception("tool %s failed", name)
            out = f"error: unexpected {type(e).__name__}: {e}"
        finally:
            _call.reset(token)
        if not t.repeatable and not c.asked:
            s.acted = True
        return out if isinstance(out, list) else [out]

    # --- socket ------------------------------------------------------------------------------

    def start(self) -> None:
        """Serve tool calls on the socket, in background threads."""
        runtime = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    req = json.loads(self.rfile.readline())
                    out = runtime.call(str(req.get("session") or "daemon"), str(req["tool"]), dict(req.get("args") or {}))
                except (ValueError, KeyError, TypeError) as e:
                    out = [f"error: bad request: {e}"]
                content = [{"type": "image", "data": base64.b64encode(x.data).decode(), "format": x.format}
                           if isinstance(x, Image) else {"type": "text", "text": str(x)} for x in out]
                self.wfile.write(json.dumps({"content": content}, ensure_ascii=False).encode() + b"\n")

        self.socket.parent.mkdir(parents=True, exist_ok=True)
        if self.socket.exists():
            self.socket.unlink()
        old = os.umask(0o177)                       # the socket is created mode 600
        try:
            self._server = socketserver.ThreadingUnixStreamServer(str(self.socket), Handler)
        finally:
            os.umask(old)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, daemon=True, name="tools").start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self.socket.exists():
            self.socket.unlink()


def signature(name: str) -> inspect.Signature:
    """A tool's signature with its annotations resolved (for the MCP adapter's schemas)."""
    return inspect.signature(TOOLS[name].fn, eval_str=True)


# --- helpers the tools share -------------------------------------------------------------------------

def _run(fn, *args, **kwargs) -> str:
    try:
        out = fn(*args, **kwargs)
    except (herdr.HerdrError, desktop.DesktopError, web.BrowserError, screen.ScreenError, PermissionError,
            ValueError) as e:
        return f"error: {e}"
    except Exception as e:  # noqa: BLE001 - report, never crash the runtime
        return f"error: unexpected {type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def _ask(question: str, run: Callable, check: Callable[[], None] | None = None) -> str:
    """Ask the user first: `run` happens only after a yes in their next message, and only if `check`
    (the target is still what the question was about) passes then."""
    c = _call.get()
    c.asked = True
    turn = c.runtime._session(c.session).turn
    return json.dumps(c.runtime.confirmations.request(c.session, turn, question, run, check), ensure_ascii=False)


def _profile() -> Profile:
    return _call.get().runtime.profile


def _web(out: str) -> str:
    return out if out.startswith("error:") else safety.untrusted(out, "web page")


# --- knowing the system ----------------------------------------------------------------------------

@tool(repeatable=True)
def system_state() -> str:
    """Live snapshot: time, battery, volume, what's playing, open windows by workspace, herdr agents and panes."""
    return _run(_profile().live_context)


@tool(repeatable=True)
def system_lookup(topic: str, query: str = "") -> str:
    """Search the full lists: topic is one of commands (Omarchy CLI), keys (key bindings), aliases,
    functions, apps, webapps, tuis, packages, projects. query is words to match (empty lists all)."""
    if topic not in Profile.TOPICS:
        return f"error: topic must be one of {', '.join(Profile.TOPICS)}"
    return _run(_profile().lookup, topic, query)


@tool(repeatable=True)
def system_info(what: str) -> str:
    """One fact: battery, memory, time, disk, network, cpu, volume or media (what's playing)."""
    return _run(desktop.system_info, what)


# --- apps and windows ------------------------------------------------------------------------------

@tool(repeatable=True)
def launch_app(app: str, workspace: str = "", follow: bool = True) -> str:
    """Open an installed app or web app by name (focuses it if already open). Optional workspace number:
    the window is put there and the user's view goes with it. Use follow=False only when the user asks to
    stay where they are ("but keep me here"). Says so if the app isn't installed."""
    return _run(desktop.launch_app, app, workspace or None, follow)


@tool(repeatable=True)
def focus_window(window: str) -> str:
    """Bring a window to the front, by app name or title words."""
    return _run(desktop.focus_window, window)


@tool(repeatable=True)
def move_window(window: str, workspace: str, follow: bool = False) -> str:
    """Move a window (app name or title words) to a workspace number (or 'scratchpad')."""
    return _run(desktop.move_window, window, workspace, follow)


@tool(repeatable=True)
def switch_workspace(workspace: str) -> str:
    """Show a workspace: a number, 'next', 'previous' or 'back'."""
    return _run(desktop.switch_workspace, workspace)


@tool()
def close_window(window: str) -> str:
    """Close a window. Needs the user's confirmation (unsaved work could be lost)."""
    try:
        w = desktop.find_window(window)
    except desktop.DesktopError as e:
        return f"error: {e}"
    return _ask(f"Close {w.get('class')} “{(w.get('title') or '')[:50]}”?",
                lambda: desktop.close_window(w))      # that very window, checked to still be open


@tool()
def open_url(url: str, app_mode: bool = False, browser: str = "") -> str:
    """Just show a URL to the user: in the default browser, or in a named one (chrome, brave).
    app_mode=True opens it as a standalone web-app window. To read or act on a page, use browser_open."""
    return _run(desktop.open_url, url, app_mode, browser)


@tool()
def open_path(path: str) -> str:
    """Open a file or folder with its default app (PDF viewer, image viewer, file manager...)."""
    return _run(desktop.open_path, path)


@tool(repeatable=True)
def screenshot(target: str = "window", save_to: str = "~/Pictures") -> str:
    """Take a screenshot: target window (focused window), screen (focused monitor) or region (user drags).
    Returns the file path."""
    return _run(desktop.screenshot, target, save_to)


# --- sound, media, notifications -------------------------------------------------------------------

@tool()
def volume(action: str, amount: int = 0) -> str:
    """Speaker volume: action up, down, mute (toggle) or set; amount in percent (up/down default 5)."""
    return _run(desktop.volume, action, amount or None)


@tool()
def brightness(action: str, amount: int = 10) -> str:
    """Screen brightness: action up, down or set; amount in percent."""
    return _run(desktop.brightness, action, amount)


@tool()
def media(action: str) -> str:
    """Control the playing media: play_pause, play, pause, next or previous."""
    return _run(desktop.media, action)


@tool()
def notify(text: str) -> str:
    """Show a desktop notification."""
    return _run(desktop.notify, text)


@tool()
def set_reminder(minutes: int, text: str) -> str:
    """Desktop reminder after a number of minutes."""
    return _run(desktop.reminder, minutes, text)


@tool()
def toggle(feature: str) -> str:
    """Toggle: nightlight, idle (stay awake), do not disturb, bluetooth, bar or touchpad."""
    return _run(desktop.toggle, feature)


@tool()
def lock_screen() -> str:
    """Lock the screen. Needs the user's confirmation."""
    return _ask("Lock the screen now?", desktop.lock_screen)


# --- herdr: coding agents and commands ---------------------------------------------------------------

@tool(repeatable=True)
def list_agents() -> str:
    """Coding agents running in herdr: kind, status (idle/working/blocked/done), folder, workspace and tab."""
    return _run(herdr.list_agents)


@tool(repeatable=True)
def read_agent(agent: str, hint: str = "", lines: int = 60) -> str:
    """Read what an agent's pane shows. agent is a name, pane id or kind (codex, claude, opencode...);
    hint narrows it down by project folder, workspace or tab label."""
    out = _run(herdr.read_agent, agent, hint, lines)
    return out if out.startswith("error:") else safety.untrusted(out, f"herdr agent {agent}")


@tool()
def prompt_agent(agent: str, text: str, hint: str = "", wait: bool = True, timeout_s: int = 180) -> str:
    """Send a prompt to an agent that is already running (the user's or auren's). The user's view switches
    to the agent so they can watch (the result's `view` says whether they can see it; don't claim it's
    showing otherwise). With wait=True returns its reply when it finishes (good for quick questions);
    use wait=False for long work."""
    out = _run(herdr.prompt_agent, agent, text, hint, wait, timeout_s, show=True)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("reply"):
        d["reply"] = safety.untrusted(d["reply"], f"reply from {agent}")
    if d.get("screen"):
        d["screen"] = safety.untrusted(d["screen"], f"screen of {agent}")
    return json.dumps(d, ensure_ascii=False)


@tool()
def start_agent(kind: str = "codex", folder: str = "~", name: str = "", task: str = "",
                wait: bool = False, timeout_s: int = 180) -> str:
    """Start a new coding agent (codex by default; claude, opencode, agy, grok, muse...) in its own tab of
    auren's herdr workspace, in a folder, optionally with a first task. name: a short job name for the task
    (like "lecture-notes"). The user's view switches to the new tab so they can watch (the result's
    `view` says whether they can see it; don't claim it's showing otherwise). For big jobs keep
    wait=False and tell the user it's running."""
    out = _run(herdr.start_agent, kind, folder, name, task, wait, timeout_s, show=True)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("reply"):
        d["reply"] = safety.untrusted(d["reply"], f"reply from {d.get('job')}")
    return json.dumps(d, ensure_ascii=False)


@tool()
def run_command(command: str, folder: str = "~", name: str = "", wait: bool = True, timeout_s: int = 60) -> str:
    """Run a shell command in its own tab of auren's herdr workspace. Returns exit code and output when
    wait=True. Commands that delete, overwrite, need root, restart services, push, or touch secrets need
    the user's confirmation first."""
    risk = safety.command_risk(command)
    if risk:
        return _ask(f"Run `{command}`? {risk[0].upper() + risk[1:]}.",
                    lambda: _run_command(command, folder, name, wait, timeout_s))
    return _run_command(command, folder, name, wait, timeout_s)


def _run_command(command: str, folder: str, name: str, wait: bool, timeout_s: int) -> str:
    out = _run(herdr.run_command, command, folder, name, wait, timeout_s)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("output_tail"):
        d["output_tail"] = safety.untrusted(d["output_tail"], f"output of {command[:40]}")
    return json.dumps(d, ensure_ascii=False)


@tool(repeatable=True)
def job_status(job: str, wait_s: int = 0) -> str:
    """Result of a run_command job (exit code, output); optionally wait up to wait_s seconds."""
    out = _run(herdr.command_result, job, wait_s)
    return out if out.startswith("error:") else safety.untrusted(out, f"job {job}")


@tool()
def close_job(job: str) -> str:
    """Close one of auren's own herdr tabs (never the user's)."""
    return _run(herdr.close_job, job)


@tool(repeatable=True)
def show_agent(agent: str, hint: str = "") -> str:
    """Switch herdr's view to an agent's pane and bring herdr's window up so the user can watch it."""
    return _run(herdr.focus, agent, hint)


# --- browser (the user's real, logged-in Chrome or Brave) ---------------------------------------------

def _same_page(before: dict) -> Callable[[], None]:
    def check() -> None:
        try:
            now = web.page()
        except web.BrowserError as e:
            raise PermissionError(f"the page is gone ({e}): read it again and ask the user again") from None
        if (now["browser"], now["tab"], now["url"]) != (before["browser"], before["tab"], before["url"]):
            raise PermissionError("the page changed since you asked: read it again and ask the user again")
    return check


@tool(repeatable=True)
def browser_open(url: str, browser: str = "chrome") -> str:
    """Open a page in a new auren tab of the user's real browser and read it: text plus numbered clickable
    items. url can be 'bookmark:<name or folder/name>'. browser: chrome or brave (the user's routines may
    say which). If the browser isn't open, this starts it and waits for it
    to connect (up to ~25 s): don't launch it first."""
    return _web(_run(web.browser_open, url, browser))


@tool(repeatable=True)
def browser_read() -> str:
    """Read the current auren browser tab again (text and numbered clickable items)."""
    return _web(_run(web.browser_read))


@tool()
def browser_click(target: str, expect_download: bool = False) -> str:
    """Click an item: its number from the last read, or words from its label. Returns the page after the
    click. expect_download=True waits for a file to finish downloading and returns its path. Clicks that
    buy, pay, send, post, submit or delete need the user's confirmation."""
    try:
        label = web.label_of(target)
        if label and web.RISKY_CLICK.search(label):
            here = web.page()
    except web.BrowserError as e:
        return f"error: {e}"
    if label and web.RISKY_CLICK.search(label):
        def check() -> None:
            _same_page(here)()
            if web.label_of(target) != label:
                raise PermissionError(f"“{label}” isn't there any more: read the page again and ask the user again")
        return _ask(f"Click “{label}”? That could buy, send, post or delete something.",
                    lambda: safety.untrusted(web.browser_click(target, None, expect_download), "web page"), check)
    return _web(_run(web.browser_click, target, None, expect_download))


@tool()
def browser_type(target: str, text: str, submit: bool = False) -> str:
    """Type text into a numbered field from the last read; submit=True presses Enter. Submitting a search
    box runs straight away; submitting any other field (a message, a comment, a form) needs the user's
    confirmation, since it can send or post."""
    if submit:
        try:
            f = web.field_of(target)
            here = web.page() if f and not f["search"] else None
        except web.BrowserError as e:
            return f"error: {e}"
        if f is None:
            return f"error: no field number {target} (read the page again)"
        if not f["search"]:
            short = text if len(text) <= 60 else text[:57] + "…"
            return _ask(f"Type “{short}” into “{f['label'] or 'the field'}” and press Enter? That could send or post it.",
                        lambda: safety.untrusted(web.browser_type(target, text, True), "web page"), _same_page(here))
    return _web(_run(web.browser_type, target, text, submit))


@tool(repeatable=True)
def browser_scroll(direction: str = "down") -> str:
    """Scroll the current auren tab down or up and read it again."""
    return _web(_run(web.browser_scroll, direction))


@tool()
def browser_close(keep_current: bool = False) -> str:
    """Close the browser tabs auren opened (never the user's). keep_current=True leaves the last page open
    for the user to look at."""
    return _run(web.browser_close, keep_current)


@tool(repeatable=True)
def browser_bookmarks(query: str = "", browser_name: str = "chrome") -> str:
    """List the user's bookmarks matching words (folder/name → url)."""
    return _run(web.bookmarks, query, browser_name)


@tool(repeatable=True)
def recent_downloads(minutes: int = 30, browser_name: str = "chrome") -> str:
    """Files downloaded recently (path and size)."""
    return _run(web.recent_downloads, browser_name, minutes)


# --- screen control: see the screen, use the mouse and keyboard (fallback) ------------------------------

RISKY_KEYS = {"alt+f4", "ctrl+q", "ctrl+w", "ctrl+shift+w", "ctrl+shift+q", "shift+delete", "super+q"}


def _same_screen() -> Callable[[], None]:
    """The confirmed click lands where the user was asked about: same screenshot, same window in front."""
    view, window = screen.view_id(), screen.active_window()

    def check() -> None:
        if screen.view_id() != view:
            raise PermissionError("you looked at the screen again since asking: ask the user again about what you see now")
        if screen.active_window() != window:
            raise PermissionError("a different window is in front now: look at the screen again and ask again")
    return check


@tool(repeatable=True)
def look_at_screen() -> list:
    """See the focused monitor as an image. Use it when other tools can't reach something (a browser
    permission popup, an app's own dialog, an app with no command) or when the user asks you to look or
    click. Coordinates for click_at and scroll_at are pixels in this image."""
    try:
        data, note = screen.look()
    except screen.ScreenError as e:
        return [f"error: {e}"]
    return [note, Image(data=data, format="jpeg")]


@tool()
def click_at(x: int, y: int, target: str, button: str = "left", double: bool = False) -> str:
    """Click at pixel (x, y) of the latest look_at_screen image. target: what you are clicking, in words
    (for example "Allow on Chrome's download popup"). Look again afterwards to check it worked. Clicks
    that buy, pay, send, post, submit or delete need the user's confirmation."""
    if web.RISKY_CLICK.search(target):
        return _ask(f"Click “{target}”?", lambda: screen.click(x, y, button, double), _same_screen())
    desktop.osd_safe(f"Auren: clicking {target[:40]}")
    return _run(screen.click, x, y, button, double)


@tool()
def drag_at(x1: int, y1: int, x2: int, y2: int, target: str, hold: str = "") -> str:
    """Press the mouse at pixel (x1, y1) of the latest look_at_screen image, move to (x2, y2) and release:
    draw a shape (pick the tool first, then drag from one corner of the shape to the opposite one), move a
    slider, an item or a window, select text. hold: "shift" (in drawing apps: a perfect circle or square),
    "ctrl", "alt", or several joined with "+". target: what you are doing, in words. Look again afterwards.
    Drags that delete or send something (to the trash, onto Send) need the user's confirmation."""
    if web.RISKY_CLICK.search(target):
        return _ask(f"Drag to “{target}”?", lambda: screen.drag(x1, y1, x2, y2, hold), _same_screen())
    return _run(screen.drag, x1, y1, x2, y2, hold)


@tool()
def scroll_at(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
    """Scroll the mouse wheel at pixel (x, y) of the latest look_at_screen image."""
    return _run(screen.scroll, x, y, direction, amount)


@tool()
def type_text(text: str) -> str:
    """Type text into whatever has keyboard focus (click the field first)."""
    return _run(screen.type_text, text)


@tool()
def press_keys(keys: str) -> str:
    """Press a key or shortcut in the focused window: 'Enter', 'Escape', 'Tab', 'ctrl+s', 'ctrl+t'...
    Shortcuts that close windows or quit apps need confirmation."""
    if keys.strip().lower().replace(" ", "") in RISKY_KEYS:
        window = screen.active_window()

        def check() -> None:
            if screen.active_window() != window:
                raise PermissionError("a different window is in front now: ask the user again")
        return _ask(f"Press {keys}? It closes a window or app.", lambda: screen.press_keys(keys), check)
    return _run(screen.press_keys, keys)


# --- confirmations -------------------------------------------------------------------------------------

@tool()
def confirm_action(token: str) -> str:
    """Run an action the user has just approved. Only call this after the user said yes in their own
    reply to your question; it fails if they haven't answered yet, or if what it would act on has
    changed since (then look again and ask again)."""
    c = _call.get()
    try:
        p = c.runtime.confirmations.redeem(c.session, token, c.runtime._session(c.session).turn)
    except (PermissionError, web.BrowserError, screen.ScreenError, desktop.DesktopError) as e:
        return f"error: {e}"
    return _run(p.run)
