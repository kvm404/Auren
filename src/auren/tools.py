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
brain can tell the user what went wrong. Risky tools go through auren.safety: by the approval level
(`Runtime.level`) they run, or return a question and a token. A yes runs the questions waiting: the
daemon's, for a plain answer it read itself (`Runtime.answer`), or the brain's `confirm_action` in a later
user turn. Text that comes from panes, the screen or the web is wrapped as untrusted.
"""

from __future__ import annotations

import base64
import contextlib
import contextvars
import inspect
import json
import logging
import os
import re
import socketserver
import threading
import traceback
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import safety
from .hands import browser as web
from .hands import desktop, excalidraw, herdr, screen, shell
from .integrations import active, hidden_tools
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
    lock: Any = None        # BROWSER or SCREEN: held while it runs


TOOLS: dict[str, Tool] = {}

# One tool at a time on the browser, and one on the screen and keyboard (a brain can call tools in
# parallel). A confirmation is checked and run holding the same lock, so what it checked is what it acts on.
BROWSER = threading.RLock()
SCREEN = threading.RLock()


def tool(repeatable: bool = False, lock: Any = None):
    def register(fn: Callable) -> Callable:
        TOOLS[fn.__name__] = Tool(fn, repeatable, lock)
        return fn
    return register


# --- the runtime ---------------------------------------------------------------------------------

@dataclass
class Session:
    turn: int = 0
    cancelled: bool = False
    acted: bool = False


@dataclass
class Outcome:
    """What became of one question the user answered."""
    question: str
    ok: bool
    result: str
    say: str | None = None          # nothing for the brain to add: this is all that is said


@dataclass
class _Call:
    runtime: Runtime
    session: str
    lock: Any = None                        # the tool's lock, which its confirmation will hold too
    asked: bool = False                     # the tool asked for confirmation instead of acting


_call: contextvars.ContextVar[_Call] = contextvars.ContextVar("auren_tool_call")


class Runtime:
    def __init__(self, socket: Path):
        self.socket = socket
        self.profile = Profile()
        self.confirmations = safety.Confirmations()
        self.level = "ask"                  # the approval level (safety.LEVELS); the daemon keeps it in step with config.toml
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()
        self._server: socketserver.ThreadingUnixStreamServer | None = None

    def _session(self, sid: str) -> Session:
        with self._lock:
            return self._sessions.setdefault(sid, Session())

    def new_turn(self, sid: str, same_task: bool = False) -> int:
        """A new user message in this session. It ends the previous task, so a yes that covered it
        (level "task") stops covering anything; same_task: this continues that task (an answer to its
        question, or the brain going on after one), so what it covered and what already ran still count."""
        s = self._session(sid)
        s.turn, s.cancelled = s.turn + 1, False
        if not same_task:
            s.acted = False
            self.confirmations.revoke(sid)
        return s.turn

    def forget(self, sid: str) -> None:
        """A fresh conversation: nothing asked in the old one can be answered any more."""
        self.confirmations.clear(sid)

    def set_level(self, level: str) -> None:
        if level not in safety.LEVELS:
            raise ValueError("safety level must be one of " + ", ".join(safety.LEVELS))
        self.level = level
        for sid in list(self._sessions):
            self.confirmations.revoke(sid)

    def cancel(self, sid: str) -> None:
        self._session(sid).cancelled = True

    def cancelled(self, sid: str) -> bool:
        return self._session(sid).cancelled

    def acted(self, sid: str) -> bool:
        return self._session(sid).acted

    def summary(self, sid: str) -> str:
        return self.confirmations.summary(sid)

    def awaiting(self, sid: str) -> safety.Pending | None:
        return self.confirmations.latest(sid)

    def batch(self, sid: str) -> list[safety.Pending]:
        """The questions one yes or no answers, oldest first."""
        return [p for _, p in self.confirmations.batch(sid)]

    def answerable(self, sid: str) -> bool:
        """Is the user's next plain yes or no an answer to what Auren asked last? Not if they have said
        something else since: then only the brain, who knows what they meant, can tell."""
        batch = self.batch(sid)
        return bool(batch) and batch[0].turn == self._session(sid).turn

    def answer(self, sid: str, yes: bool, tokens: set[str] | None = None) -> list[Outcome]:
        """The user's yes or no to the questions waiting, read by the daemon itself (a plain spoken answer,
        a click, auren-ctl yes): no brain turn in between. The caller counts the user's message first
        (`new_turn`), which is what makes the tokens redeemable. tokens: only these (the ones the user was
        shown when they answered; a question asked since is not theirs to have approved)."""
        batch = [(t, p) for t, p in self.confirmations.batch(sid) if tokens is None or t in tokens]
        if not yes:
            self.confirmations.drop(sid, [t for t, _ in batch])
            return [Outcome(p.question, False, "the user said no") for _, p in batch]
        return self.run_confirmed(sid, [t for t, _ in batch])

    def run_confirmed(self, sid: str, tokens: list[str]) -> list[Outcome]:
        """Run the approved actions in order, each only if its target is still what was asked about. The
        first that fails stops the rest (they are dropped: the brain has to look again and ask again)."""
        s = self._session(sid)
        turn, epoch = s.turn, self.confirmations.epoch(sid)
        outcomes: list[Outcome] = []
        ran: set[str] = set()
        for i, token in enumerate(tokens):
            if s.cancelled:                            # Stop: what hasn't started doesn't
                outcomes.append(Outcome("", False, "error: the user cancelled this request"))
                self.confirmations.drop(sid, tokens[i:])
                break
            try:
                p = self.confirmations.redeem(sid, token, turn)
            except PermissionError as e:
                outcomes.append(Outcome("", False, f"error: {e}"))
                break
            with p.lock or contextlib.nullcontext():
                try:
                    if s.cancelled:                    # stopped while waiting for the browser or the screen
                        raise PermissionError("the user cancelled this request")
                    if p.check:
                        p.check()
                    result = _run(p.run)
                except (PermissionError, web.BrowserError, screen.ScreenError, desktop.DesktopError,
                        excalidraw.ExcalidrawError) as e:
                    result = f"error: {e}"
            ok = not _failed(result)
            outcomes.append(Outcome(p.question, ok, result, p.say(result) if ok and p.say else None))
            if not ok:
                log.warning("approved action failed: %s %s", p.question[:200], result.splitlines()[0][:300])
                self.confirmations.drop(sid, tokens[i + 1:])
                break
            ran |= p.kinds
            s.acted = True
        if ran:
            if self.level == "task":
                self.confirmations.grant(sid, ran, epoch)
        return outcomes

    def call(self, sid: str, name: str, args: dict) -> list[str | Image]:
        s = self._session(sid)
        if s.cancelled:
            return ["error: the user cancelled this request. Stop now: don't call more tools."]
        t = TOOLS.get(name)
        if t is None:
            return [f"error: no tool called {name}"]
        if name in hidden_tools():
            return [f"error: {name} isn't available on this machine (see auren doctor)"]
        c = _Call(self, sid, t.lock)
        token = _call.set(c)
        try:
            with t.lock or contextlib.nullcontext():
                out = t.fn(**args)
        except TypeError as e:
            out = f"error: wrong arguments for {name}: {e}"
        except Exception as e:  # noqa: BLE001 - report, never crash the runtime
            log.exception("tool %s failed", name)
            out = f"error: unexpected {type(e).__name__}: {e}"
        finally:
            _call.reset(token)
        first = out[0] if isinstance(out, list) and out else out
        if isinstance(first, str) and first.startswith("error:"):     # the brain only says "it failed": keep why
            log.warning("tool %s %s: %s", name, _loggable(args), first.splitlines()[0][:300])
        if not t.repeatable and not c.asked:
            s.acted = True
        return out if isinstance(out, list) else [out]

    # --- socket ------------------------------------------------------------------------------

    def offered(self) -> list[str]:
        """The tools the brain is offered: all but those of integrations that are off or missing."""
        hidden = hidden_tools()
        return [n for n in TOOLS if n not in hidden]

    def start(self) -> None:
        """Serve tool calls on the socket, in background threads."""
        runtime = self

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                try:
                    req = json.loads(self.rfile.readline())
                    if req.get("list"):
                        self.wfile.write(json.dumps({"tools": runtime.offered()}).encode() + b"\n")
                        return
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
    except (herdr.HerdrError, desktop.DesktopError, web.BrowserError, screen.ScreenError, shell.ShellError,
            excalidraw.ExcalidrawError, PermissionError, ValueError) as e:
        return f"error: {e}"
    except Exception as e:  # noqa: BLE001 - report, never crash the runtime
        log.exception("%s failed", getattr(fn, "__name__", fn))
        return f"error: unexpected {type(e).__name__}: {e}\n{traceback.format_exc(limit=2)}"
    return out if isinstance(out, str) else json.dumps(out, ensure_ascii=False)


def _loggable(args: dict) -> str:
    """A failed call's arguments for the log, without what was typed or sent (it can be a password): its length only."""
    shown = {k: f"<{len(v)} characters>" if k == "text" and isinstance(v, str) else v for k, v in args.items()}
    return json.dumps(shown, ensure_ascii=False)[:200]


def _failed(result: str) -> bool:
    """Did an approved action fail? Errors say so, and so does a command that finished with a non-zero exit."""
    if result.startswith("error:"):
        return True
    try:
        d = json.loads(result)
    except ValueError:
        return False
    return isinstance(d, dict) and isinstance(d.get("exit_code"), int) and d["exit_code"] != 0


def _gate(kinds: set[str], question: str, run: Callable, check: Callable[[], None] | None = None,
          *, say: Callable[[str], str | None] | None = None) -> str:
    """A risky action of these kinds (safety.WINDOW, FILES, ...). Depending on the approval level it runs
    now, or asks the user first: `run` then happens only after a yes in their next message, and only if
    `check` (the target is still what the question was about) passes then. say: given the result, what to
    say if nothing is left for the brain to do (None: the brain should see the result)."""
    c = _call.get()
    rt = c.runtime
    if not safety.needs_ask(rt.level, kinds, rt.confirmations.granted(c.session)):
        log.info("safety %s: ran without asking: %s", rt.level, question)
        return _run(run)
    turn = rt._session(c.session).turn
    if sum(p.turn == turn for p in rt.batch(c.session)) >= safety.MAX_BATCH:
        return (f"error: {safety.MAX_BATCH} questions are already waiting for the user's answer. Don't ask more "
                "now: ask about those, and do the rest after they answer.")
    c.asked = True
    return json.dumps(rt.confirmations.request(c.session, turn, question, run, check, c.lock,
                                               kinds=kinds, say=say), ensure_ascii=False)


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
    """Close a window. Unsaved work could be lost, so the user is asked first: just call it, don't ask in words first."""
    try:
        w = desktop.find_window(window)
    except desktop.DesktopError as e:
        return f"error: {e}"
    return _gate({safety.WINDOW}, f"Close {w.get('class')} “{(w.get('title') or '')[:50]}”?",
                 lambda: desktop.close_window(w),     # that very window, checked to still be open
                 # an app that answers with its own "Close all tabs?" dialog hasn't closed: the brain must say so
                 say=lambda result: f"Closed {w.get('class')}." if result.startswith("closed") else None)


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
    """Lock the screen. The user is asked first: just call it, don't ask in words first."""
    return _gate({safety.WINDOW}, "Lock the screen now?", desktop.lock_screen, say=lambda result: "Locked.")


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
def start_agent(kind: str = "", folder: str = "~", name: str = "", task: str = "",
                wait: bool = False, timeout_s: int = 180) -> str:
    """Start a new coding agent (leave kind empty for the user's default agent, or name one: claude, codex,
    opencode, agy, grok...) in its own tab of auren's herdr workspace, in a folder, optionally with a first task. name: a short job name for the task
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
    """Run a shell command as a job (in its own tab of auren's herdr workspace when herdr is there, else in
    the background). Returns exit code and output when wait=True. Commands that delete, overwrite, need root, restart services, push, or touch secrets
    make the tool ask the user itself (needs_confirmation): just call it, don't ask in words first."""
    risk = safety.assess_command(command)
    if risk:
        return _gate(risk.kinds, f"Run `{command}`? {risk.reason[0].upper() + risk.reason[1:]}.",
                     lambda: _run_command(command, folder, name, wait, timeout_s))
    return _run_command(command, folder, name, wait, timeout_s)


def _run_command(command: str, folder: str, name: str, wait: bool, timeout_s: int) -> str:
    runner = herdr.run_command if active("herdr") else shell.run_command
    out = _run(runner, command, folder, name, wait, timeout_s)
    if out.startswith("error:"):
        return out
    d = json.loads(out)
    if d.get("output_tail"):
        d["output_tail"] = safety.untrusted(d["output_tail"], f"output of {command[:40]}")
    return json.dumps(d, ensure_ascii=False)


@tool(repeatable=True)
def job_status(job: str, wait_s: int = 0) -> str:
    """Result of a run_command job (exit code, output); optionally wait up to wait_s seconds."""
    out = _run(shell.command_result if shell.has(job) else herdr.command_result, job, wait_s)
    return out if out.startswith("error:") else safety.untrusted(out, f"job {job}")


@tool()
def close_job(job: str) -> str:
    """Close one of auren's own jobs: its herdr tab, or stop it if it runs in the background (never the
    user's)."""
    return _run(shell.close_job if shell.has(job) else herdr.close_job, job)


@tool(repeatable=True)
def show_agent(agent: str, hint: str = "") -> str:
    """Switch herdr's view to an agent's pane and bring herdr's window up so the user can watch it."""
    return _run(herdr.focus, agent, hint)


# --- browser (the user's real, logged-in Chromium browser) ---------------------------------------------

def _same_page(before: dict) -> Callable[[], None]:
    def check() -> None:
        try:
            now = web.page()
        except web.BrowserError as e:
            raise PermissionError(f"the page is gone ({e}): read it again and ask the user again") from None
        if (now["browser"], now["tab"], now["url"]) != (before["browser"], before["tab"], before["url"]):
            raise PermissionError("the page changed since you asked: read it again and ask the user again")
    return check


@tool(repeatable=True, lock=BROWSER)
def browser_open(url: str, browser: str = "") -> str:
    """Open a page in a new auren tab of the user's real browser and read it: text plus numbered clickable
    items. url can be 'bookmark:<name or folder/name>'. browser: leave empty for the user's browser, or
    name one (chrome, brave, chromium, helium, edge, vivaldi) when the user or a routine asks for it. If
    the browser isn't open, this starts it and waits for it to connect (up to ~25 s): don't launch it first."""
    return _web(_run(web.browser_open, url, browser))


@tool(repeatable=True, lock=BROWSER)
def browser_read() -> str:
    """Read the current auren browser tab again (text and numbered clickable items)."""
    return _web(_run(web.browser_read))


@tool(lock=BROWSER)
def browser_click(target: str, expect_download: bool = False) -> str:
    """Click an item: its number from the last read, or words from its label. Returns the page after the
    click. expect_download=True waits for a file to finish downloading and returns its path. Clicks that
    buy, pay, send, post, submit or delete make the tool ask the user itself: just click, don't ask in words first."""
    try:
        label = web.label_of(target)
        if label and web.RISKY_CLICK.search(label):
            here, pinned = web.page(), web.pin(target)
    except web.BrowserError as e:
        return f"error: {e}"
    if label and web.RISKY_CLICK.search(label):
        def check() -> None:
            _same_page(here)()
            if not pinned or web.label_of(pinned) != label:
                raise PermissionError(f"“{label}” isn't there any more: read the page again and ask the user again")
        return _gate({safety.click_kind(label)}, f"Click “{label}”? That could buy, send, post or delete something.",
                     lambda: safety.untrusted(web.browser_click(pinned, None, expect_download), "web page"), check)
    return _web(_run(web.browser_click, target, None, expect_download))


@tool(lock=BROWSER)
def browser_type(target: str, text: str, submit: bool = False) -> str:
    """Type text into a numbered field from the last read; submit=True presses Enter. Submitting a search
    box runs straight away; submitting any other field (a message, a comment, a form) makes the tool ask the
    user itself, since it can send or post: just call it, don't ask in words first."""
    if submit:
        try:
            f = web.field_of(target)
            here = web.page() if f and not f["search"] else None
        except web.BrowserError as e:
            return f"error: {e}"
        if f is None:
            return f"error: no field number {target} (read the page again)"
        if not f["search"]:
            try:
                pinned = web.pin(target, is_field=True)
            except web.BrowserError as e:
                return f"error: {e}"

            def check() -> None:
                _same_page(here)()
                now = web.field_of(pinned) if pinned else None
                if now is None or now["label"] != f["label"]:
                    raise PermissionError(f"“{f['label'] or 'that field'}” isn't there any more: read the page "
                                          "again and ask the user again")
            short = text if len(text) <= 60 else text[:57] + "…"
            return _gate({safety.SEND}, f"Type “{short}” into “{f['label'] or 'the field'}” and press Enter? That could send or post it.",
                         lambda: safety.untrusted(web.browser_type(pinned, text, True), "web page"), check)
    return _web(_run(web.browser_type, target, text, submit))


@tool(repeatable=True, lock=BROWSER)
def browser_scroll(direction: str = "down") -> str:
    """Scroll the current auren tab down or up and read it again."""
    return _web(_run(web.browser_scroll, direction))


@tool(lock=BROWSER)
def browser_close(keep_current: bool = False) -> str:
    """Close the browser tabs auren opened (never the user's). keep_current=True leaves the last page open
    for the user to look at."""
    return _run(web.browser_close, keep_current)


@tool(repeatable=True)
def browser_bookmarks(query: str = "", browser_name: str = "") -> str:
    """List the user's bookmarks matching words (folder/name → url)."""
    return _run(web.bookmarks, query, browser_name)


@tool(repeatable=True)
def recent_downloads(minutes: int = 30, browser_name: str = "") -> str:
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


@tool(repeatable=True, lock=SCREEN)
def look_at_screen() -> list:
    """See the focused monitor as an image. Use it when other tools can't reach something (a browser
    permission popup, an app's own dialog, an app with no command) or when the user asks you to look or
    click. Coordinates for click_at and scroll_at are pixels in this image."""
    try:
        data, note = screen.look()
    except screen.ScreenError as e:
        return [f"error: {e}"]
    return [note, Image(data=data, format="jpeg")]


@tool(lock=SCREEN)
def click_at(x: int, y: int, target: str, button: str = "left", double: bool = False) -> str:
    """Click at pixel (x, y) of the latest look_at_screen image. target: what you are clicking, in words
    (for example "Allow on Chrome's download popup"). Look again afterwards to check it worked. Clicks
    that buy, pay, send, post, submit or delete make the tool ask the user itself: just click, don't ask first."""
    if web.RISKY_CLICK.search(target):
        return _gate({safety.click_kind(target)}, f"Click “{target}”?", lambda: screen.click(x, y, button, double),
                     _same_screen())
    return _run(screen.click, x, y, button, double)


@tool(lock=SCREEN)
def drag_at(x1: int, y1: int, x2: int, y2: int, target: str, hold: str = "") -> str:
    """Press the mouse at pixel (x1, y1) of the latest look_at_screen image, move to (x2, y2) and release:
    draw a shape (pick the tool first, then drag from one corner of the shape to the opposite one), move a
    slider, an item or a window, select text. hold: "shift" (in drawing apps: a perfect circle or square),
    "ctrl", "alt", or several joined with "+". target: what you are doing, in words. Look again afterwards.
    Drags that delete or send something (to the trash, onto Send) make the tool ask the user itself."""
    if web.RISKY_CLICK.search(target):
        return _gate({safety.click_kind(target)}, f"Drag to “{target}”?", lambda: screen.drag(x1, y1, x2, y2, hold),
                     _same_screen())
    return _run(screen.drag, x1, y1, x2, y2, hold)


@tool(lock=SCREEN)
def scroll_at(x: int, y: int, direction: str = "down", amount: int = 3) -> str:
    """Scroll the mouse wheel at pixel (x, y) of the latest look_at_screen image."""
    return _run(screen.scroll, x, y, direction, amount)


@tool(lock=SCREEN)
def type_text(text: str) -> str:
    """Type text into whatever has keyboard focus (click the field first)."""
    return _run(screen.type_text, text)


@tool(lock=SCREEN)
def press_keys(keys: str) -> str:
    """Press a key or shortcut in the focused window: 'Enter', 'Escape', 'Tab', 'ctrl+s', 'ctrl+t'...
    Shortcuts that close windows or quit apps make the tool ask the user itself."""
    if keys.strip().lower().replace(" ", "") in RISKY_KEYS:
        window = screen.key_target()        # where the keys will go (the window auren last clicked, if recent)

        def check() -> None:
            if screen.key_target() != window:
                raise PermissionError("the keys would go to a different window now: ask the user again")
        return _gate({safety.WINDOW}, f"Press {keys}? It closes a window or app.", lambda: screen.press_keys(keys), check)
    return _run(screen.press_keys, keys)


# --- Excalidraw: whole drawings at once ------------------------------------------------------------------

@tool(lock=SCREEN)
def excalidraw_draw(elements: list[dict] | None = None, mermaid: str = "", place: str = "beside",
                    clear: bool = False) -> str:
    """Draw in Excalidraw: a whole picture or diagram in one call, placed exactly (never draw it with
    drag_at). Opens Excalidraw if it isn't open, and scrolls the drawing into view. Give one of:
    - mermaid: a Mermaid definition, for anything made of boxes and arrows (flowchart, sequenceDiagram,
      classDiagram, stateDiagram, erDiagram): Excalidraw lays it out itself. Other Mermaid types arrive as a
      picture, not shapes. Colour flowchart nodes with classDef and class, fill light and stroke dark in the
      same colour: blue fill:#a5d8ff,stroke:#1971c2; green #b2f2bb/#2f9e44; yellow #ffec99/#f08c00; red
      #ffc9c9/#e03131; violet #d0bfff/#6741d9; orange #ffd8a8/#e8590c; gray #e9ecef/#343a40. (Rounded) nodes
      look friendlier than [square] ones; keep {decision} labels to a word or two.
    - elements: a list of elements in canvas pixels (x right, y down), for pictures, mind maps, layouts and
      anything Mermaid can't do:
      {"type": "rectangle"|"ellipse"|"diamond", "id": "a", "x": 0, "y": 0, "width": 160, "height": 80,
       "label": "text inside"}  (without width and height it fits its label)
      {"type": "text", "x": 0, "y": 0, "text": "words", "fontSize": 20}  (x, y: its top-left corner)
      {"type": "arrow", "start": "a", "end": "b", "label": "optional"}: joins two shapes (or texts) edge to
       edge and stays bound to them; "elbow": true for right angles; or "points": [[x, y], ...] for its path
      {"type": "line", "points": [[x, y], ...]}: open, or closed (last point = first) to fill it like a
       shape; "smooth": true turns the corners into curves
      {"type": "freedraw", "points": [[x, y], ...]}, {"type": "frame", "name": "...", "children": [ids]}
      Style, on any element: strokeColor, backgroundColor (black, white, transparent; red, pink, purple,
      violet, blue, cyan, teal, green, yellow, orange, gray, brown, each also as light-<colour> and
      dark-<colour>; or #rrggbb), fillStyle (solid, hachure, cross-hatch, zigzag), strokeWidth (1, 2, 4),
      strokeStyle (solid, dashed, dotted), roughness (0 clean, 1 sketchy, 2 wild), opacity (0-100), font
      (hand, normal, code), fontSize, rounded (corners; on by default for boxes with a label), rotate (degrees), group (the same name
      on several elements makes them move as one), startArrowhead/endArrowhead (arrow, triangle, bar, dot,
      or null).
    place: "beside" puts a new drawing next to what's on the canvas already (never on top of it); "exact"
    keeps your coordinates as canvas coordinates, to add to a drawing you read with excalidraw_read.
    clear: empty the canvas first, to redraw your own drawing. If the canvas holds work you didn't draw,
    the tool asks the user itself: just call it."""
    if not clear:
        return _run(excalidraw.draw, elements, mermaid, False, place)
    try:
        before = excalidraw.scene()
    except (excalidraw.ExcalidrawError, screen.ScreenError, desktop.DesktopError):
        before = []
    if not before:
        # empty, closed (opening it may bring back a drawing) or unreadable: clear nothing, draw beside
        return _run(excalidraw.draw, elements, mermaid, False, place)
    if excalidraw.drawn_by_auren(before):
        return _run(excalidraw.draw, elements, mermaid, True, place)
    seen = excalidraw.fingerprint(before)

    def check() -> None:
        if excalidraw.fingerprint(excalidraw.scene()) != seen:
            raise PermissionError("the canvas changed since you asked: ask the user again")
    n = f"{len(before)} element{'s' if len(before) != 1 else ''}"
    return _gate({safety.FILES}, f"Clear the Excalidraw canvas ({n}; Excalidraw's undo brings them back) and draw "
                 "the new drawing?", lambda: excalidraw.draw(elements, mermaid, True, place), check)


@tool(repeatable=True, lock=SCREEN)
def excalidraw_read() -> str:
    """What's on the open Excalidraw canvas: every element (ids, positions, sizes, labels, colours, arrow
    ends), in the form excalidraw_draw takes, and whether you drew all of it. Read before changing or adding
    to a drawing that's already there."""
    try:
        elements = excalidraw.scene()
    except (excalidraw.ExcalidrawError, screen.ScreenError, desktop.DesktopError) as e:
        return f"error: {e}"
    if not elements:
        return "The Excalidraw canvas is empty."
    return safety.untrusted(json.dumps(excalidraw.summary(elements), ensure_ascii=False), "Excalidraw canvas")


# --- confirmations -------------------------------------------------------------------------------------

@tool()
def confirm_action(token: str) -> str:
    """Run the actions the user has just approved: pass every token you asked them about, separated by
    spaces. Only call this after the user said yes in their own reply to your question; it fails if they
    haven't answered yet, or if what it would act on has changed since (then look again and ask again)."""
    c = _call.get()
    tokens = [t for t in re.split(r"[\s,]+", token) if t]
    if not tokens:
        return "error: no token given"
    outcomes = c.runtime.run_confirmed(c.session, tokens)
    if len(tokens) == 1:
        return outcomes[0].result
    lines = [f"{o.question}: {o.result}" if o.question else o.result for o in outcomes]
    if len(outcomes) < len(tokens) or not all(o.ok for o in outcomes):
        lines.append("The ones after a failure were not run: look again and ask the user again.")
    return "\n".join(lines)
