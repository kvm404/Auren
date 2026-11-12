"""The mascot and the menu bar icon on macOS, in one PySide6 process (`auren mascot`, run by the
com.auren.mascot launch agent).

On Omarchy the mascot is a Quickshell instance and the settings live in a bar plugin. Here the same QML
characters run in mascot/mac.qml, fed by `Bridge` (the daemon's state file, the loudness file, the
mascot's preferences and place), and a menu bar icon gives the panel's quick settings: talk, voice,
brain, safety, auto-send, mascot, setup check, quit. It needs no permissions of its own.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from ..config import CONFIG_DIR, DATA_HOME, RUNTIME_DIR, STATE_DIR, STATE_FILE
from ..live import LEVEL_FILE

PREFS = CONFIG_DIR / "mascot.json"
PLACE = STATE_DIR / "mascot.json"
PID_FILE = RUNTIME_DIR / "mascot.pid"
PREF_DEFAULTS = {"idleOpacity": 0.45, "fadeAfter": 12, "sleepAfter": 240, "bubble": True, "sleepAnimation": True,
                 "mascot": "auren"}
VOICES = ["F2", "M3", "F1", "M1"]          # decision 0001's picks
GLYPHS = {"cmd": "⌘", "shift": "⇧", "alt": "⌥", "ctrl": "⌃"}


def key_label(combo: str) -> str:
    """'ctrl+alt+a' -> '⌃⌥A', in the order macOS menus write modifiers."""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    names = {"control": "ctrl", "option": "alt", "opt": "alt", "command": "cmd"}
    mods = {names.get(p, p) for p in parts[:-1]}
    return "".join(GLYPHS[m] for m in ("ctrl", "alt", "shift", "cmd") if m in mods) + (parts[-1].upper() if parts else "")


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


def ctl_path() -> list[str]:
    own = DATA_HOME / "bin/auren-ctl"
    return [str(own)] if own.exists() else [sys.executable, "-m", "auren.ctl"]


def run_ctl(command: str) -> None:
    subprocess.Popen([*ctl_path(), *command.split()], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def main() -> int:
    from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
    from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

    from .. import settings as auren_settings
    from . import service

    class Bridge(QObject):
        changed = Signal()
        levelChanged = Signal()

        def __init__(self):
            super().__init__()
            self._state: dict = {"state": "off"}
            self._raw = ""
            self._alive = False
            self._level = 0.0
            self._prefs = dict(PREF_DEFAULTS)
            self._place = {"x": -1, "y": -1, "size": 1, "hidden": False}
            self._key = "⌃⌥A"
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

        @Slot()
        def startDaemon(self) -> None:
            service.start("daemon")

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
                ns.setHidesOnDeactivate_(False)     # Qt.Tool makes an NSPanel, which hides when Auren isn't active
            except Exception:  # noqa: BLE001 - not on macOS (tests), or AppKit changed: an ordinary window then
                pass

        def set_hidden(self, hidden: bool) -> None:
            self.savePlace(self._place.get("x", -1), self._place.get("y", -1), self._place.get("size", 1), hidden)

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
    bridge = Bridge()
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("auren", bridge)
    qml = Path(os.environ.get("AUREN_MASCOT_DIR") or DATA_HOME / "mascot") / "mac.qml"
    if not qml.exists():
        qml = Path(__file__).resolve().parents[3] / "mascot" / "mac.qml"      # a checkout
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        print(f"auren mascot: couldn't load {qml}", file=sys.stderr)
        return 1

    timers = []
    for ms, fn in ((250, bridge.poll_state), (50, bridge.poll_level), (2000, bridge.reload_files)):
        t = QTimer()
        t.timeout.connect(fn)
        t.start(ms)
        timers.append(t)

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

    tray = QSystemTrayIcon(icon("#D77757"))
    tray.setToolTip("Auren")
    menu = QMenu()

    def item(label: str, fn, parent=menu, checkable: bool = False, group=None):
        a = QAction(label, parent)
        a.setCheckable(checkable)
        a.triggered.connect(fn)
        if group is not None:
            group.addAction(a)
        parent.addAction(a)
        return a

    talk = item("Talk", lambda: run_ctl("talk"))
    item("Fresh conversation", lambda: run_ctl("talk-fresh"))
    item("Stop", lambda: run_ctl("cancel"))
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
    levels = choices("Safety", [("ask", "Ask every time"), ("task", "Ask once per task"), ("free", "Full access")], "safety")

    mascot_menu = menu.addMenu("Mascot")
    mascot_group = QActionGroup(mascot_menu)

    def pick(name: str) -> None:
        _write_json(PREFS, {**_read_json(PREFS), "mascot": name})
        bridge.reload_files()
    kinds = {n: item(n.title(), lambda checked=False, n=n: pick(n), mascot_menu, True, mascot_group) for n in ("auren", "veyra")}
    mascot_menu.addSeparator()
    shown = item("Show mascot", lambda checked=False: bridge.set_hidden(not checked), mascot_menu, True)
    item("Reset size and place", lambda: (_write_json(PLACE, {"x": -1, "y": -1, "size": 1, "hidden": False}),
                                          bridge.reload_files()), mascot_menu)
    menu.addSeparator()
    item("Check setup (auren doctor)", lambda: service.open_in_terminal("auren doctor"))
    item("Edit settings", lambda: subprocess.Popen(["open", "-t", str(CONFIG_DIR / "config.toml")]))
    item("Restart Auren", lambda: (service.restart("daemon")))
    item("Quit Auren", lambda: (service.stop("daemon"), app.quit()))

    def refresh() -> None:
        try:
            s = auren_settings.load()
        except Exception:  # noqa: BLE001
            return
        talk.setText(f"Talk ({key_label(str(s.get('keys.talk', 'ctrl+alt+a')))})")
        voice_on.setChecked(bool(s["voice"]))
        autosend.setChecked(bool(s["autosend"]))
        for options, value in ((voices, s["voiceName"]), (brains, s["brain"]), (levels, s["safety"])):
            if value in options:
                options[value].setChecked(True)
        kinds.get(bridge._prefs.get("mascot", "auren"), kinds["auren"]).setChecked(True)
        shown.setChecked(not bridge._place.get("hidden", False))
        alive = bridge._alive
        tray.setIcon(icon("#D77757" if alive else "#8e8e93"))
        tray.setToolTip("Auren: " + (bridge._state.get("state", "idle") if alive else "off"))

    menu.aboutToShow.connect(refresh)
    bridge.changed.connect(refresh)
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
