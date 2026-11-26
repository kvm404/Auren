"""The Auren window on macOS (mascot/mac-window.qml): what the bar panel (omarchy-plugin/Panel.qml) is on
Omarchy, in a window of its own with the same pages: Home, Mascot, Voice, Brain, Vocabulary, Phone and
Updates. The menu bar icon opens it (Open Auren…, or Phone… for its Phone page), and so does opening
Auren.app once setup is done.

It changes things the way the panel does: auren-ctl for the daemon's settings (voice, brain, safety,
auto-send), ~/.config/auren/mascot.json for the mascot's, the daemon's launch agent for Auren on and off,
and `auren vocab` and `auren update` (called here in Python, on a thread). The Phone page is
phone_window.py's. What it shows comes from the daemon's state.json (through the mascot's Bridge),
history.jsonl and update.json, which the menu reads too, so the window and the menu stay in step.

Everything but bridge() is plain Python, so the tests run it without Qt.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..config import CONFIG_DIR, STATE_DIR

PAGES = ("home", "mascot", "voice", "brain", "vocab", "phone", "updates")
PANEL_PREFS = CONFIG_DIR / "panel.json"       # the panel's own settings, shared with Omarchy's: notifyUpdates
HISTORY = STATE_DIR / "history.jsonl"
DAEMON_LOG = STATE_DIR / "daemon.log"
# Each mascot's voice and hello, as the panel has them (picking a mascot picks its voice: matchVoice)
MASCOTS = {"auren": ("Trevor", "Hey, I'm Auren."), "veyra": ("Juniper", "Hi, I'm Veyra.")}
BUSY = ("listening", "thinking", "working", "speaking")
UPDATE_CHECK_EVERY = 6 * 3600                  # seconds, as the panel checks (and 3 minutes after the start)


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def values(state: dict, cfg: dict) -> dict:
    """The daemon's settings for the window: from state.json while it runs, else as config.toml has them."""
    st = state if state.get("state", "off") != "off" else {}
    tts = st.get("tts") or {k: cfg.get(k, 1.0 if k != "pause" else 0.0) for k in ("speed", "volume", "pause")}
    mode = st.get("mode") or cfg.get("brain", "auto")
    return {
        "voice": st.get("voice", cfg.get("voice", True)) is not False,
        "voiceName": st.get("voiceName") or cfg.get("voiceName", ""),
        "tts": {k: float(tts.get(k, 0.0)) for k in ("speed", "volume", "pause")},
        "mode": mode,
        "answering": st.get("brain") or ("codex" if mode == "codex" else "claude"),
        "effort": st.get("effort") or cfg.get("effort", "low"),
        "codexEffort": st.get("codexEffort") or cfg.get("brain.codex_effort", "low"),
        "codexModel": st.get("codexModel") or cfg.get("brain.codex_model", ""),
        "safety": st.get("safety") or cfg.get("safety", "ask"),
        "autosend": bool(st.get("autosend", cfg.get("autosend", False))),
        "autosendAfter": float(st.get("autosendAfter") or cfg.get("autosendAfter", 5)),
    }


def codex_name(model: str) -> str:
    """Codex's model as people say it: gpt-6-luna → GPT-6 Luna; other names as they are."""
    parts = (model or "").split("-")
    if len(parts) < 3 or parts[0] != "gpt":
        return model or ""
    return f"GPT-{parts[1]} " + " ".join(w.capitalize() for w in parts[2:])


def status_line(state: dict, alive: bool, turning: str = "") -> str:
    """What Auren is doing, in a line: the menu's first item and the window's Home."""
    if not alive:
        return turning or "Auren is off"
    s = state.get("state", "idle")
    if s == "idle" and state.get("awaiting") and (state.get("until") or 0) > time.time():
        return "Waiting for your yes or no"
    return {"starting": "Auren is starting…", "listening": "Still listening" if state.get("remind") else "Listening…",
            "thinking": "Thinking…", "working": state.get("detail") or "Working…", "speaking": "Speaking…",
            "error": "Something went wrong"}.get(s, "Auren is ready")


def history(path: Path = HISTORY, now: float | None = None) -> dict:
    """The last exchange and today's numbers, from the end of history.jsonl, as the panel shows them:
    {"last": {transcript, reply} | None, "count", "avg" (seconds), "codex"}."""
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 256 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()[-300:]
    except OSError:
        lines = []
    t = time.localtime(now if now is not None else time.time())
    start = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    last, count, total, codex = None, 0, 0.0, 0
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if not isinstance(r, dict) or r.get("source") == "forget":
            continue
        last = r
        if float(r.get("at") or 0) >= start:
            count += 1
            total += float(r.get("think_s") or 0)
            codex += r.get("brain") == "codex"
    return {"last": {"transcript": str(last.get("transcript") or ""), "reply": str(last.get("reply") or "")} if last else None,
            "count": count, "avg": round(total / count, 1) if count else 0.0, "codex": codex}


def ago(t: float, now: float | None = None) -> str:
    if not t:
        return "never"
    s = max(0.0, (now if now is not None else time.time()) - t)
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{round(s / 60)} min ago"
    if s < 86400:
        return f"{round(s / 3600)} h ago"
    return f"{round(s / 86400)} days ago"


def update_text(upd: dict, checking: bool) -> tuple[str, str]:
    """The Updates page's headline and the line under it, as the panel says them."""
    state = upd.get("state", "")
    if state == "updating":
        return upd.get("step") or "Updating…", "Auren starts again when it's done. You can close this window."
    if checking:
        return "Checking for updates…", ""
    headline = {"available": f"Release {(upd.get('latest') or {}).get('tag') or 'update'} available",
                "current": "You're up to date with releases", "done": upd.get("message") or "Updated",
                "failed": "The update didn't finish", "error": "Couldn't check for updates",
                "unknown": "Version unknown"}.get(state, "Not checked yet")
    if state in ("failed", "error", "unknown"):
        detail = upd.get("message") or ""
    elif state == "done":
        detail = "Finished " + ago(upd.get("finished_at") or 0)
    else:
        detail = "Checked " + ago(upd.get("checked_at")) if upd.get("checked_at") else ""
    return headline, detail


def update_waiting(upd: dict) -> str:
    """The release waiting to be installed ("" if none): the menu's Update to … item."""
    if upd.get("state") != "available":
        return ""
    return str((upd.get("latest") or {}).get("tag") or "a new version")


def version_text(info: dict, upd: dict) -> str:
    """Auren 0.1.8 (Homebrew), or with the commit for a clone."""
    have = upd.get("installed") or {}
    v = have.get("version") if info.get("mode") == "brew" and have.get("version") else info.get("version", "")
    text = "Auren" + (f" {v}" if v else "")
    if info.get("mode") == "brew":
        return text + " (Homebrew)"
    return text + (f" · {info['commit'][:7]}" if info.get("commit") else "")


def update_plan(info: dict) -> str:
    """What Update now does, said before it does it."""
    if info.get("mode") == "brew":
        return ("Auren waits until it's idle, stops, and Homebrew installs the new version (brew upgrade auren-macos)."
                " Then Auren starts again, in about a minute. If that fails, the current version is set up again.")
    return ("Auren waits until it's idle, stops, installs the new version and starts again (about a minute)."
            " A failed install puts the current version back.")


def open_file(path: Path, app: str = "") -> None:
    """Open a file on the Mac: in an app (Console for logs), else as text (TextEdit or the user's editor)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.touch()
    cmd = ["open", "-a", app, str(path)] if app else ["open", "-t", str(path)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def background(fn: Callable, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def bridge(auren, phone_ui, ctl: Callable[[str], None], write_prefs: Callable[[dict], None]):
    """The window's QObject (`settings` in mac-window.qml). auren: the mascot's Bridge (state, prefs, place,
    setup); phone_ui: the Phone page's (phone_window.bridge); ctl: runs an auren-ctl command; write_prefs:
    merges preferences into mascot.json."""
    from PySide6.QtCore import Property, QObject, Signal, Slot

    from .. import update, vocab
    from ..settings import current
    from . import service

    class Window(QObject):
        changed = Signal()
        pageChanged = Signal()                 # only when the page really changes: it scrolls to the top
        raiseWindow = Signal()
        _loaded = Signal("QVariant")           # from a thread: [what, result]

        def __init__(self):
            super().__init__()
            self._open = False
            self._page = "home"
            self._cfg: dict = {}
            self._history: dict = {"last": None, "count": 0, "avg": 0.0, "codex": 0}
            self._service = False                # the daemon's launch agent runs (it may still be starting)
            self._vocab: dict = {"mine": [], "defaults": [], "limit": 0, "message": "", "ok": True}
            self._vocab_busy = False
            self._upd: dict = {}
            self._checking = False
            self._last_check = 0.0
            self._loaded.connect(self._take)
            auren.changed.connect(self.changed)          # the daemon's state: what most of the window shows
            auren.setupChanged.connect(self.changed)
            self.reload()

        # ---- what the window shows -------------------------------------------------------------
        @Property(bool, notify=changed)
        def open(self):
            return self._open

        @Property(str, notify=pageChanged)
        def page(self):
            return self._page

        @Property("QVariant", notify=changed)
        def values(self):
            return values(auren._state, self._cfg)

        @Property(str, notify=changed)
        def codexName(self):
            return codex_name(values(auren._state, self._cfg)["codexModel"])

        @Property(bool, notify=changed)
        def serviceOn(self):
            return self._service or auren._alive

        @Property(str, notify=changed)
        def statusLine(self):
            return status_line(auren._state, auren._alive, auren._setup.get("turningText", ""))

        @Property("QVariant", notify=changed)
        def history(self):
            return self._history

        @Property("QVariant", notify=changed)
        def vocab(self):
            return self._vocab

        @Property(bool, notify=changed)
        def vocabBusy(self):
            return self._vocab_busy

        @Property("QVariant", notify=changed)
        def upd(self):
            return self._upd

        @Property(bool, notify=changed)
        def checking(self):
            return self._checking

        @Property(str, notify=changed)
        def updateHeadline(self):
            return update_text(self._upd, self._checking)[0]

        @Property(str, notify=changed)
        def updateDetail(self):
            return update_text(self._upd, self._checking)[1]

        @Property(str, notify=changed)
        def updatePlan(self):
            return update_plan(update.installed())

        @Property(str, notify=changed)
        def versionText(self):
            return version_text(update.installed(), self._upd)

        @Property(bool, notify=changed)
        def notifyUpdates(self):
            return _read_json(PANEL_PREFS).get("notifyUpdates", True) is not False

        # ---- keeping up --------------------------------------------------------------------------
        def reload(self) -> None:
            """config.toml, update.json and the history: every couple of seconds while the window is open."""
            try:
                self._cfg = current()
            except Exception:  # noqa: BLE001 - a broken config.toml: keep the last one
                pass
            self._upd = update.status()
            if self._open:
                self._history = history()
            self.changed.emit()

        def poll_service(self) -> None:
            """Whether the daemon's launch agent runs (launchctl takes a moment: on a thread)."""
            if self._open:
                background(lambda: self._loaded.emit(["service", service.running("daemon")]))

        def tick(self) -> None:
            """Every few hours: look for an update, as the bar panel does (with its notification, if wanted)."""
            if time.time() - self._last_check >= UPDATE_CHECK_EVERY:
                self._check(notify=self.notifyUpdates)

        @Slot("QVariant")
        def _take(self, item) -> None:
            what, result = item
            if what == "service":
                self._service = bool(result)
            elif what == "vocab":
                self._vocab, self._vocab_busy = result, False
            elif what == "checked":
                self._checking = False
                self._upd = update.status()
            self.changed.emit()

        # ---- the window --------------------------------------------------------------------------
        @Slot(str)
        def openPage(self, page: str) -> None:
            self._open = True
            self.setPage(page or self._page)
            self.reload()
            self.poll_service()
            auren.check(force=True)
            self.raiseWindow.emit()
            try:                                 # an accessory app has to bring itself forward
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:  # noqa: BLE001 - not on macOS (tests)
                pass

        @Slot()
        def close(self) -> None:
            self._open = False
            phone_ui.hidePage()
            self.changed.emit()

        @Slot(str)
        def setPage(self, page: str) -> None:
            old, self._page = self._page, page if page in PAGES else "home"
            if self._page == "phone" and self._open:
                phone_ui.showPage()
            else:
                phone_ui.hidePage()                # leaving the Phone page ends a pairing, as the panel does
            if self._page == "vocab":
                self.vocabRun("list", "")
            if self._page == "updates" and not self._checking and time.time() - (self._upd.get("checked_at") or 0) > 1800:
                self._check()
            if self._page != old:
                self.pageChanged.emit()
            self.changed.emit()

        @Slot()
        def focused(self) -> None:
            """The window came to the front: the user may be back from System Settings."""
            auren.check(force=True)
            self.reload()
            self.poll_service()

        # ---- Home ----------------------------------------------------------------------------------
        @Slot()
        def toggleAuren(self) -> None:
            """Auren on or off: its launch agent, as the panel's On does with systemd."""
            if self._upd.get("state") == "updating":         # the update stops and starts Auren itself
                return
            on = self._service or auren._alive
            self._service = not on
            self.changed.emit()
            background(lambda: (service.stop("daemon") if on else service.start("daemon"),
                                self._loaded.emit(["service", service.running("daemon")])))

        @Slot()
        def restartAuren(self) -> None:
            auren.restart()

        @Slot()
        def openSetup(self) -> None:
            auren.openSetup()

        @Slot()
        def openConfig(self) -> None:
            from ..settings import CONFIG_FILE, ensure
            try:
                ensure()
            except Exception:  # noqa: BLE001 - open it as it is
                pass
            open_file(CONFIG_FILE)

        @Slot()
        def openLog(self) -> None:
            open_file(DAEMON_LOG, "Console")

        # ---- the mascot -------------------------------------------------------------------------------
        @Slot(str, "QVariant")
        def setPref(self, name: str, value) -> None:
            write_prefs({name: value})

        @Slot(str)
        def setMascot(self, kind: str) -> None:
            """Pick a mascot; with Voice follows mascot on, it speaks with its own voice and says hello."""
            if kind not in MASCOTS:
                return
            if auren._prefs.get("mascot") != kind:
                write_prefs({"mascot": kind})
            v = values(auren._state, self._cfg)
            if auren._prefs.get("matchVoice", True) is not False and auren._alive and v["voice"] \
                    and auren._state.get("state") == "idle":
                voice, hello = MASCOTS[kind]
                ctl(f"voice-name {voice} {hello}")

        @Slot(float)
        def setSize(self, size: float) -> None:
            p = auren._place
            auren.savePlace(p.get("x", -1), p.get("y", -1), max(0.6, min(2.2, size)), bool(p.get("hidden")))

        @Slot(bool)
        def setShown(self, shown: bool) -> None:
            auren.set_hidden(not shown)

        @Slot()
        def resetPlace(self) -> None:
            auren.reset_place()

        # ---- vocabulary ------------------------------------------------------------------------------
        @Slot(str, str)
        def vocabRun(self, cmd: str, word: str) -> None:
            """list, add or remove a word, as `auren vocab` does (it tells the daemon)."""
            if self._vocab_busy or cmd not in ("list", "add", "remove"):
                return
            if cmd == "add" and not word.strip():
                self._vocab = {**self._vocab, "message": "Type a word first.", "ok": False}
                self.changed.emit()
                return
            self._vocab_busy = True
            self.changed.emit()

            def run():
                try:
                    result = vocab.run(cmd, word.strip())
                except Exception as e:  # noqa: BLE001 - say it, the window stays as it is
                    result = {**self._vocab, "message": f"Couldn't reach Auren's vocabulary: {e}", "ok": False}
                self._loaded.emit(["vocab", result])
            background(run)

        # ---- updates ------------------------------------------------------------------------------------
        def _check(self, notify: bool = False) -> None:
            if self._checking or self._upd.get("state") == "updating":
                return
            self._checking, self._last_check = True, time.time()
            self.changed.emit()

            def run():
                try:
                    update.check(notify=notify)
                except Exception as e:  # noqa: BLE001 - update.json says what failed, the log the rest
                    print(f"auren mascot: the update check failed: {e!r}", file=sys.stderr, flush=True)
                self._loaded.emit(["checked", None])
            background(run)

        @Slot()
        def checkUpdates(self) -> None:
            self._check(notify=False)

        @Slot()
        def startUpdate(self) -> None:
            """auren update apply: in the background, out of Auren's launch agents (it stops and starts them)."""
            def run():
                try:
                    update.start()
                except Exception as e:  # noqa: BLE001
                    print(f"auren mascot: the update didn't start: {e!r}", file=sys.stderr, flush=True)
                self._loaded.emit(["checked", None])
            background(run)

        @Slot()
        def openUpdateLog(self) -> None:
            open_file(Path(self._upd.get("log") or update.LOG), "Console")

        @Slot(bool)
        def setNotifyUpdates(self, on: bool) -> None:
            PANEL_PREFS.parent.mkdir(parents=True, exist_ok=True)
            PANEL_PREFS.write_text(json.dumps({**_read_json(PANEL_PREFS), "notifyUpdates": bool(on)}, indent=2) + "\n")
            self.changed.emit()

    return Window()
