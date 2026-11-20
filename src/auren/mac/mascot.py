"""The mascot and the menu bar icon on macOS, in one PySide6 process (`auren mascot`, run by the
com.auren.mascot launch agent).

On Omarchy the mascot is a Quickshell instance and the settings live in a bar plugin. Here the same QML
characters run in mascot/mac.qml, fed by `Bridge` (the daemon's state file, the loudness file, the
mascot's preferences and place). The same process has three more things:

- the setup window (mascot/mac-setup.qml, fed by Bridge and setup.py), until Auren has what it needs
- the Auren window (mascot/mac-window.qml, fed by settings_window.py, and phone_window.py for its Phone
  page): the bar panel's pages and controls
- the menu bar icon: what Auren is doing, talk, stop, the quick settings (voice, auto-send, brain, safety,
  mascot), the two windows, an update when one is waiting, restart and quit

Opening Auren.app (`auren open`) shows the setup window while setup isn't done, else the Auren window. It
needs no permissions of its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..config import CONFIG_DIR, DATA_HOME, RUNTIME_DIR, STATE_DIR, STATE_FILE
from ..live import LEVEL_FILE
from ..settings import key_label  # noqa: F401 (the menu and tests use it here)
from ..voice import VOICES  # noqa: F401 (decision 0001's picks, the default first; the menu and tests use it)

PREFS = CONFIG_DIR / "mascot.json"
PLACE = STATE_DIR / "mascot.json"
PID_FILE = RUNTIME_DIR / "mascot.pid"
PREF_DEFAULTS = {"idleOpacity": 0.45, "fadeAfter": 12, "sleepAfter": 240, "bubble": True, "sleepAnimation": True,
                 "mascot": "auren", "matchVoice": True}
def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def screen_area(areas: list[tuple[dict, dict]], x: float, y: float) -> dict:
    """Where the mascot may sit near (x, y): the usable part (no menu bar or Dock) of the screen that holds
    the point, else of the nearest one. areas: (screen's whole rect, its usable rect) for each screen,
    as {"x", "y", "width", "height"}."""
    if not areas:
        return {"x": 0, "y": 0, "width": 1440, "height": 900}

    def away(whole: dict) -> float:
        dx = max(whole["x"] - x, 0, x - (whole["x"] + whole["width"]))
        dy = max(whole["y"] - y, 0, y - (whole["y"] + whole["height"]))
        return dx * dx + dy * dy
    return min(areas, key=lambda a: away(a[0]))[1]


def ctl_path() -> list[str]:
    own = DATA_HOME / "bin/auren-ctl"
    return [str(own)] if own.exists() else [sys.executable, "-m", "auren.ctl"]


def run_ctl(command: str) -> None:
    subprocess.Popen([*ctl_path(), *command.split()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def background(fn, *args) -> None:
    """launchctl and the daemon's socket can take seconds: never on the window's thread."""
    threading.Thread(target=fn, args=args, daemon=True).start()


def make_bridge():
    """The QObject mac.qml and mac-setup.qml read as `auren`: the daemon's state, the mascot's preferences
    and place, and the setup window's steps. Made here, not in main(), so tests can make one."""
    from PySide6.QtCore import Property, QObject, Signal, Slot
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication

    from .. import settings as auren_settings
    from . import service
    from . import setup as auren_setup
    from .app import SETUP_REQUEST, SETUP_REQUEST_TTL

    class Bridge(QObject):
        changed = Signal()
        levelChanged = Signal()
        setupChanged = Signal()
        screensChanged = Signal()           # a screen came, went, or its Dock or menu bar moved
        placeReset = Signal()               # the menu's Reset: back to the default corner
        checked = Signal("QVariant")        # from the checking thread to this one: [started at, result]
        raiseSetup = Signal()               # bring the open setup window to the front
        showWindow = Signal()               # Auren.app was opened and setup is done: the Auren window

        def __init__(self):
            super().__init__()
            self._state: dict = {"state": "off"}
            self._raw = ""
            self._alive = False
            self._level = 0.0
            self._prefs = dict(PREF_DEFAULTS)
            self._place = {"x": -1, "y": -1, "size": 1, "hidden": False}
            self._key = "⌃⌥A"
            self._setup_open = False
            self._setup: dict = {"running": True, "microphone": "unknown", "accessibility": False, "screen": False,
                                 "talkKeys": False, "key": False, "claude": False, "codex": False, "brain": "auto",
                                 "brainReady": False, "ready": False, "restart": False, "starting": False,
                                 "turningOn": [], "turningText": "", "keysTurningOn": False}
            self._checking = False
            self._first_check = True
            self._open_app = False              # Auren.app was opened: show a window once the check says which
            self._trust_after = 0.0             # checks started before this predate a change made here
            self.checked.connect(self._take_check)
            self.reload_files()

        def reload_files(self) -> None:
            self._prefs = {**PREF_DEFAULTS, **_read_json(PREFS)}
            self._place = {**self._place, **_read_json(PLACE)}
            try:
                self._key = key_label(str(auren_settings.load().get("keys.talk", "ctrl+alt+a")))
            except Exception:  # noqa: BLE001 - a broken config.toml: keep the last one
                pass
            self.changed.emit()

        def poll_state(self) -> None:
            try:                                 # Auren.app was opened (auren open)
                age = time.time() - SETUP_REQUEST.stat().st_mtime
            except OSError:
                age = None
            if age is not None:
                SETUP_REQUEST.unlink(missing_ok=True)
                if age < SETUP_REQUEST_TTL:      # not one left from when the mascot couldn't start
                    self._open_app = True
                    self.check(force=True)
            try:
                raw = STATE_FILE.read_text()
            except OSError:
                raw = ""
            fresh = raw != self._raw
            if fresh:
                self._raw = raw
                try:
                    self._state = json.loads(raw) if raw else {"state": "off"}
                except ValueError:
                    pass
            alive = False
            pid = self._state.get("pid")
            if self._state.get("state") != "off" and isinstance(pid, int):
                try:
                    os.kill(pid, 0)          # it could have crashed without writing "off"
                    alive = True
                except OSError:
                    alive = False
            if fresh or alive != self._alive:
                self._alive = alive
                self.changed.emit()

        def poll_level(self) -> None:
            if self._state.get("state") not in ("listening", "speaking"):
                if self._level:
                    self._level = 0.0
                    self.levelChanged.emit()
                return
            try:
                v = max(0.0, min(1.0, float(LEVEL_FILE.read_text().strip() or 0)))
            except (OSError, ValueError):
                v = 0.0
            if v != self._level:
                self._level = v
                self.levelChanged.emit()

        @Property("QVariant", notify=changed)
        def state(self):
            return self._state

        @Property(bool, notify=changed)
        def alive(self):
            return self._alive

        @Property(float, notify=levelChanged)
        def level(self):
            return self._level

        @Property("QVariant", notify=changed)
        def prefs(self):
            return self._prefs

        @Property("QVariant", notify=changed)
        def place(self):
            return self._place

        @Property(str, notify=changed)
        def talkKey(self):
            return self._key

        @Slot(str)
        def ctl(self, command: str) -> None:
            run_ctl(command)

        @Slot(float, float, float, bool)
        def savePlace(self, x: float, y: float, size: float, hidden: bool) -> None:
            self._place = {"x": round(x), "y": round(y), "size": round(size, 3), "hidden": hidden}
            _write_json(PLACE, self._place)
            self.changed.emit()

        @Property(int, notify=screensChanged)
        def screens(self):
            return len(QGuiApplication.screens())

        @Slot(float, float, result="QVariant")
        def screenArea(self, x: float, y: float):
            """The usable part of the screen at (x, y), or of the nearest screen (mac.qml keeps the mascot in it)."""
            def rect(r) -> dict:
                return {"x": r.x(), "y": r.y(), "width": r.width(), "height": r.height()}
            return screen_area([(rect(s.geometry()), rect(s.availableGeometry())) for s in QGuiApplication.screens()],
                               x, y)

        @Slot()
        def startDaemon(self) -> None:
            background(service.start, "daemon")

        @Slot(QObject)
        def pin(self, window) -> None:
            """Float above everything on every Space, full-screen apps too (Qt has no flag for it)."""
            from PySide6.QtGui import QGuiApplication
            if QGuiApplication.platformName() != "cocoa":     # offscreen (tests): winId isn't an NSView
                return
            try:
                import objc
                from AppKit import (
                    NSStatusWindowLevel,
                    NSWindowCollectionBehaviorCanJoinAllSpaces,
                    NSWindowCollectionBehaviorFullScreenAuxiliary,
                    NSWindowCollectionBehaviorStationary,
                )
                view = objc.objc_object(c_void_p=int(window.winId()))
                ns = view.window()
                if ns is None:
                    return
                ns.setLevel_(NSStatusWindowLevel)
                ns.setCollectionBehavior_(NSWindowCollectionBehaviorCanJoinAllSpaces
                                          | NSWindowCollectionBehaviorStationary
                                          | NSWindowCollectionBehaviorFullScreenAuxiliary)
                ns.setHasShadow_(False)
                ns.setHidesOnDeactivate_(False)     # Auren is never the active app: it must not hide then
                if window.isVisible():
                    ns.orderFrontRegardless()      # in front even though Auren isn't the active app
            except Exception as e:  # noqa: BLE001 - AppKit changed: an ordinary window then, said in the log
                print(f"auren mascot: couldn't pin {window.title()}: {e!r}", file=sys.stderr, flush=True)

        # ---- the setup window ----------------------------------------------------------------
        def check(self, force: bool = False) -> None:
            """Ask for every step's state, on a thread: while the setup window is open, once at start, and
            when asked (force: the Auren window, the menu, a window coming to the front)."""
            if self._checking or not (force or self._setup_open or self._first_check or self._open_app):
                return
            self._checking = True
            started = time.monotonic()

            def run():
                try:
                    result = auren_setup.check()
                except Exception as e:  # noqa: BLE001 - the window keeps its last state
                    print(f"auren mascot: setup check failed: {e!r}", file=sys.stderr, flush=True)
                    result = None
                self.checked.emit([started, result])
            background(run)

        def _changed_here(self, settle: float = 0.0) -> None:
            """The window changed a step itself: don't let a check that was already running (or one that
            starts before the change lands, `settle` seconds) put the old state back."""
            self._trust_after = time.monotonic() + settle
            self.setupChanged.emit()

        @Slot("QVariant")
        def _take_check(self, item) -> None:
            self._checking = False
            started, result = item
            if self._open_app:                   # Auren.app was opened: setup while it isn't done, else Auren
                self._open_app = False
                if result is not None and result.get("ready") and not result.get("restart"):
                    self.showWindow.emit()
                else:
                    self.openSetup()
            if result is None or started < self._trust_after:
                return
            first, self._first_check = self._first_check, False
            self._setup = result
            self.setupChanged.emit()
            if first and not result.get("ready") and not auren_setup.done():
                self.openSetup()                 # the first run: show what's left

        @Property(bool, notify=setupChanged)
        def setupOpen(self):
            return self._setup_open

        @Property("QVariant", notify=setupChanged)
        def setup(self):
            return self._setup

        @Property(str, notify=setupChanged)
        def brainText(self):
            return auren_setup.brain_text({"claude": False, "codex": False, **self._setup})[0]

        @Property(str, notify=setupChanged)
        def setupNeeds(self):
            """What setup still needs, in a line ("" when nothing): the Auren window's Home says it."""
            return auren_setup.needs(self._setup)

        @Slot()
        def recheck(self) -> None:
            """A window came to the front (the user may be back from System Settings): check now."""
            self.check(force=True)

        @Property(str, notify=setupChanged)
        def brainAction(self):
            return auren_setup.brain_text({"claude": False, "codex": False, **self._setup})[1]

        @Slot()
        def openSetup(self) -> None:
            self._setup_open = True
            self.setupChanged.emit()
            self.raiseSetup.emit()
            self.check()
            try:                                 # an accessory app has to bring itself forward
                from AppKit import NSApplication
                NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
            except Exception:  # noqa: BLE001 - not on macOS (tests)
                pass

        @Slot()
        def closeSetup(self) -> None:
            if self._setup.get("ready"):
                auren_setup.mark_done()
            self._setup_open = False
            self.setupChanged.emit()

        @Slot(str)
        def allow(self, name: str) -> None:
            run_ctl(f"permissions {name}")

        @Slot(str, result=str)
        def saveKey(self, value: str) -> str:
            why = auren_setup.save_key(value)
            if not why:
                self._setup = {**self._setup, "key": True}
                self._changed_here()
            return why

        @Slot(str)
        def useBrain(self, mode: str) -> None:
            run_ctl(f"brain {mode}")
            self._setup = {**self._setup, "brain": mode,
                           "brainReady": bool(self._setup.get(mode) if mode != "auto"
                                             else self._setup.get("claude") or self._setup.get("codex"))}
            self._changed_here(2.0)             # auren-ctl writes config.toml a moment later

        @Slot()
        def copyInstall(self) -> None:
            QApplication.clipboard().setText(auren_setup.INSTALL_CODEX)

        @Slot(str)
        def openUrl(self, url: str) -> None:
            subprocess.Popen(["open", url])

        @Slot()
        def restart(self) -> None:
            self._setup = {**self._setup, "running": False, "restart": False}
            self._changed_here(3.0)             # until the daemon is on its way back
            background(service.restart, "daemon")

        def set_hidden(self, hidden: bool) -> None:
            self.savePlace(self._place.get("x", -1), self._place.get("y", -1), self._place.get("size", 1), hidden)

        def reset_place(self) -> None:
            """Back to the default corner, at the normal size."""
            _write_json(PLACE, {"x": -1, "y": -1, "size": 1, "hidden": False})
            self.reload_files()
            self.placeReset.emit()

        def write_prefs(self, values: dict) -> None:
            """Change mascot preferences (mascot.json): the mascot, the window and the menu follow at once."""
            _write_json(PREFS, {**_read_json(PREFS), **values})
            self.reload_files()

    return Bridge()


def main() -> int:
    from PySide6.QtCore import QMetaObject, Qt, QTimer, QUrl
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence, QPainter, QPixmap
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from .. import settings as auren_settings
    from . import phone_window, service, settings_window

    app = QApplication(sys.argv)
    app.setApplicationName("Auren")
    app.setQuitOnLastWindowClosed(False)
    try:                                     # a menu bar app: no Dock icon
        from AppKit import NSApplication, NSApplicationActivationPolicyAccessory
        NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    except ImportError:
        pass

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(os.getpid()))
    bridge = make_bridge()

    def watch(screen) -> None:
        screen.availableGeometryChanged.connect(lambda *_: bridge.screensChanged.emit())
        screen.geometryChanged.connect(lambda *_: bridge.screensChanged.emit())
    for scr in app.screens():
        watch(scr)
    app.screenAdded.connect(lambda scr: (watch(scr), bridge.screensChanged.emit()))
    app.screenRemoved.connect(lambda *_: bridge.screensChanged.emit())
    phone_ui = phone_window.bridge(lambda: bridge._alive)
    app.aboutToQuit.connect(phone_ui.stop)
    window_ui = settings_window.bridge(bridge, phone_ui, run_ctl, bridge.write_prefs)
    bridge.showWindow.connect(lambda: window_ui.openPage("home"))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("auren", bridge)
    engine.rootContext().setContextProperty("phone", phone_ui)
    engine.rootContext().setContextProperty("settings", window_ui)
    phone_ui.setParent(engine)              # they go after the windows that read them, not before
    window_ui.setParent(engine)
    qml = Path(os.environ.get("AUREN_MASCOT_DIR") or DATA_HOME / "mascot") / "mac.qml"
    if not qml.exists():
        qml = Path(__file__).resolve().parents[3] / "mascot" / "mac.qml"      # a checkout
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print(f"auren mascot: couldn't load {qml}", file=sys.stderr)
        return 1
    engine.load(QUrl.fromLocalFile(str(qml.with_name("mac-setup.qml"))))
    engine.load(QUrl.fromLocalFile(str(qml.with_name("mac-window.qml"))))

    def window_check() -> None:              # the Auren window's Home says when setup needs something
        if window_ui._open:
            bridge.check(force=True)

    # The first update check a few minutes after the start, then every few hours (settings_window.tick)
    window_ui._last_check = time.time() - settings_window.UPDATE_CHECK_EVERY + 180
    timers = []
    for ms, fn in ((250, bridge.poll_state), (50, bridge.poll_level), (2000, bridge.reload_files), (1200, bridge.check),
                   (1000, phone_ui.poll), (2000, window_ui.reload), (3000, window_ui.poll_service), (5000, window_check),
                   (60000, window_ui.tick)):
        t = QTimer()
        t.timeout.connect(fn)
        t.start(ms)
        timers.append(t)

    # launchd stops the mascot with SIGTERM: quit through Qt, so aboutToQuit ends a pairing that's open. The
    # timer lets Python run its signal handlers while Qt waits.
    import signal
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: app.quit())
    wake = QTimer()
    wake.timeout.connect(lambda: None)
    wake.start(500)
    timers.append(wake)

    # ---- the menu bar icon ----------------------------------------------------------------------
    def icon(color: str) -> QIcon:
        pm = QPixmap(36, 36)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(QColor(0, 0, 0, 0))
        p.drawRoundedRect(6, 8, 24, 20, 7, 7)           # Auren's head
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(12, 15, 5, 6)
        p.drawEllipse(20, 15, 5, 6)
        p.end()
        return QIcon(pm)

    def dot(color: str) -> QIcon:            # the menu's mark on Set up Auren… while it needs you
        pm = QPixmap(32, 32)
        pm.fill(QColor(0, 0, 0, 0))
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setBrush(QColor(color))
        p.setPen(QColor(0, 0, 0, 0))
        p.drawEllipse(9, 9, 14, 14)
        p.end()
        return QIcon(pm)

    tray = QSystemTrayIcon(icon("#D77757"))
    tray.setToolTip("Auren")
    menu = QMenu()

    def item(label: str, fn, parent=menu, checkable: bool = False, group=None):
        a = QAction(label, parent)
        a.setCheckable(checkable)
        a.setMenuRole(QAction.MenuRole.NoRole)          # stays where it is (Qt moves "Quit", "Settings"… otherwise)
        a.triggered.connect(fn)
        if group is not None:
            group.addAction(a)
        parent.addAction(a)
        return a

    status = item("Auren is off", lambda: window_ui.openPage("home"))
    status.setEnabled(False)
    menu.addSeparator()
    talk = item("Talk", lambda: run_ctl("talk"))
    fresh = item("Fresh conversation", lambda: run_ctl("talk-fresh"))
    stop = item("Stop", lambda: run_ctl("cancel"))
    menu.addSeparator()
    voice_on = item("Speak replies", lambda checked=False: run_ctl(f"voice {'on' if checked else 'off'}"), checkable=True)
    autosend = item("Send when I stop talking", lambda checked=False: run_ctl(f"autosend {'on' if checked else 'off'}"),
                    checkable=True)

    def choices(title: str, options: list[tuple[str, str]], command: str) -> dict[str, QAction]:
        sub = menu.addMenu(title)
        group = QActionGroup(sub)
        return {value: item(label, lambda checked=False, v=value: run_ctl(f"{command} {v}"), sub, True, group)
                for value, label in options}

    voices = choices("Voice", [(v, v) for v in VOICES], "voice-name")
    brains = choices("Brain", [("auto", "Auto (Claude, then Codex)"), ("claude", "Claude Code"), ("codex", "Codex")], "brain")
    levels = choices("Safety", [("ask", "Ask every time"), ("task", "Ask once per task"), ("free", "Auto"),
                                ("never", "Full access")], "safety")

    mascot_menu = menu.addMenu("Mascot")
    mascot_group = QActionGroup(mascot_menu)
    kinds = {n: item(n.title(), lambda checked=False, n=n: window_ui.setMascot(n), mascot_menu, True, mascot_group)
             for n in ("auren", "veyra")}
    mascot_menu.addSeparator()
    shown = item("Show mascot", lambda checked=False: bridge.set_hidden(not checked), mascot_menu, True)
    item("Reset size and place", bridge.reset_place, mascot_menu)
    menu.addSeparator()
    open_window = item("Open Auren…", lambda: window_ui.openPage(""))
    open_window.setShortcut(QKeySequence("Ctrl+,"))     # shown as ⌘, (Qt's Ctrl is the Command key on a Mac)
    item("Phone…", lambda: window_ui.openPage("phone"))
    setup_item = item("Set up Auren…", bridge.openSetup)
    update_item = item("Update Auren…", lambda: window_ui.openPage("updates"))
    menu.addSeparator()
    item("Restart Auren", bridge.restart)

    def quit_auren() -> None:
        tray.hide()
        background(lambda: (service.stop("daemon"),
                            QMetaObject.invokeMethod(app, "quit", Qt.ConnectionType.QueuedConnection)))
    item("Quit Auren", quit_auren)

    def refresh() -> None:
        alive, st, setup_now = bridge._alive, bridge._state, bridge._setup
        status.setText(settings_window.status_line(st, alive, setup_now.get("turningText", "")))
        stop.setEnabled(alive and st.get("state") in settings_window.BUSY)
        for a in (talk, fresh):
            a.setEnabled(alive)
        needs = not setup_now.get("ready") or setup_now.get("restart")
        setup_item.setText("Finish setting up Auren…" if needs else "Set up Auren…")
        setup_item.setIcon(dot("#FF9F0A") if needs else QIcon())
        waiting = settings_window.update_waiting(window_ui._upd)
        update_item.setVisible(bool(waiting))
        update_item.setText(f"Update to {waiting}…")
        tray.setIcon(icon("#D77757" if alive else "#8e8e93"))
        tray.setToolTip("Auren: " + settings_window.status_line(st, alive))
        try:
            s = auren_settings.load()
        except Exception:  # noqa: BLE001 - a broken config.toml: the rest of the menu as it was
            return
        talk.setText(f"Talk ({key_label(str(s.get('keys.talk', 'ctrl+alt+a')))})")
        fresh.setText(f"Fresh conversation ({key_label(str(s.get('keys.talk_fresh', 'ctrl+alt+shift+a')))})")
        voice_on.setChecked(bool(s["voice"]))
        autosend.setChecked(bool(s["autosend"]))
        for options, value in ((voices, s["voiceName"]), (brains, s["brain"]), (levels, s["safety"])):
            if value in options:
                options[value].setChecked(True)
        kinds.get(bridge._prefs.get("mascot", "auren"), kinds["auren"]).setChecked(True)
        shown.setChecked(not bridge._place.get("hidden", False))

    menu.aboutToShow.connect(lambda: (bridge.check(force=True), refresh()))
    bridge.changed.connect(refresh)
    bridge.setupChanged.connect(refresh)
    tray.setContextMenu(menu)
    tray.show()
    refresh()

    try:
        return app.exec()
    finally:
        try:
            if PID_FILE.read_text().strip() == str(os.getpid()):
                PID_FILE.unlink()
        except OSError:
            pass
