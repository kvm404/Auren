"""The Auren window on macOS (mac/settings_window.py, mascot/mac-window.qml) and the menu bar menu: the plain
helpers anywhere, the Qt bridges where PySide6 is (the macOS job)."""

import json
import time
from pathlib import Path

import pytest

from auren.mac import settings_window as sw

ROOT = Path(__file__).parents[1]
MASCOT_SRC = ROOT / "src/auren/mac/mascot.py"


# --- what the window shows ---------------------------------------------------------------------------

def test_the_window_shows_the_running_daemons_settings_else_config_toml():
    cfg = {"voice": False, "voiceName": "Hugh", "speed": 1.2, "volume": 0.5, "pause": 0.2, "brain": "codex",
           "effort": "high", "safety": "task", "autosend": True, "autosendAfter": 8}
    off = sw.values({"state": "off", "voiceName": "Trevor"}, cfg)
    assert off["voiceName"] == "Hugh" and not off["voice"] and off["mode"] == "codex" and off["answering"] == "codex"
    assert off["tts"] == {"speed": 1.2, "volume": 0.5, "pause": 0.2} and off["autosendAfter"] == 8.0
    on = sw.values({"state": "idle", "voice": True, "voiceName": "Trevor", "mode": "auto", "brain": "claude",
                    "tts": {"speed": 1.0, "volume": 0.9, "pause": 0.1}, "safety": "ask"}, cfg)
    assert on["voice"] and on["voiceName"] == "Trevor" and on["mode"] == "auto" and on["safety"] == "ask"


def test_codex_models_are_named_as_people_say_them():
    assert sw.codex_name("gpt-6-luna") == "GPT-6 Luna"
    assert sw.codex_name("gpt-5.6-luna") == "GPT-5.6 Luna"
    assert sw.codex_name("o4-mini") == "o4-mini" and sw.codex_name("") == ""


def test_the_status_line_says_what_auren_is_doing():
    assert sw.status_line({}, False) == "Auren is off"
    assert sw.status_line({}, False, "Turning on Screen Recording…") == "Turning on Screen Recording…"
    assert sw.status_line({"state": "idle"}, True) == "Auren is ready"
    assert sw.status_line({"state": "thinking"}, True) == "Thinking…"
    assert sw.status_line({"state": "working", "detail": "Opening Safari"}, True) == "Opening Safari"
    asking = {"state": "idle", "awaiting": True, "until": time.time() + 60}
    assert sw.status_line(asking, True) == "Waiting for your yes or no"
    assert sw.status_line({**asking, "until": time.time() - 1}, True) == "Auren is ready"


def test_the_history_gives_the_last_exchange_and_todays_numbers(tmp_path):
    now = time.time()
    path = tmp_path / "history.jsonl"
    rows = [{"at": now - 3 * 86400, "transcript": "old", "reply": "x", "think_s": 9, "brain": "claude"},
            {"at": now - 60, "transcript": "hi", "reply": "hello", "think_s": 2, "brain": "claude"},
            {"at": now - 30, "transcript": "open safari", "reply": "Done.", "think_s": 4, "brain": "codex"},
            {"at": now - 10, "source": "forget"}]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    h = sw.history(path, now)
    assert h["last"] == {"transcript": "open safari", "reply": "Done."}
    if time.localtime(now - 60).tm_mday == time.localtime(now).tm_mday:      # not just after midnight
        assert h["count"] == 2 and h["avg"] == 3.0 and h["codex"] == 1
    assert sw.history(tmp_path / "none.jsonl") == {"last": None, "count": 0, "avg": 0.0, "codex": 0}


def test_the_updates_page_says_it_as_the_panel_does():
    now = time.time()
    upd = {"state": "available", "latest": {"tag": "v0.1.8"}, "checked_at": now - 300}
    assert sw.update_text(upd, False) == ("Release v0.1.8 available", "Checked 5 min ago")
    assert sw.update_text(upd, True)[0] == "Checking for updates…"
    assert sw.update_text({"state": "updating", "step": "Installing…"}, False)[0] == "Installing…"
    assert sw.update_text({"state": "error", "message": "offline"}, False) == ("Couldn't check for updates", "offline")
    assert sw.update_text({}, False) == ("Not checked yet", "")


def test_the_menu_offers_an_update_only_when_one_is_waiting():
    assert sw.update_waiting({"state": "available", "latest": {"tag": "v0.1.8"}}) == "v0.1.8"
    assert sw.update_waiting({"state": "current"}) == "" and sw.update_waiting({}) == ""


def test_the_version_line_names_homebrew_or_the_commit():
    assert sw.version_text({"mode": "brew", "version": "0.1.7"}, {}) == "Auren 0.1.7 (Homebrew)"
    assert sw.version_text({"mode": "brew", "version": "0.1.7"}, {"installed": {"version": "0.1.8"}}) == "Auren 0.1.8 (Homebrew)"
    assert sw.version_text({"mode": "clone", "version": "0.1.7", "commit": "abcdef123"}, {}) == "Auren 0.1.7 · abcdef1"
    assert "brew upgrade auren-macos" in sw.update_plan({"mode": "brew"}) and "brew" not in sw.update_plan({})


def test_logs_open_in_console_and_settings_as_text(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(sw.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    sw.open_file(tmp_path / "logs/daemon.log", "Console")
    sw.open_file(tmp_path / "config.toml")
    assert opened == [["open", "-a", "Console", str(tmp_path / "logs/daemon.log")], ["open", "-t", str(tmp_path / "config.toml")]]
    assert (tmp_path / "logs/daemon.log").exists()


def test_every_page_the_window_has_is_one_the_bridge_knows():
    qml = (ROOT / "mascot/mac-window.qml").read_text()
    for page in sw.PAGES:
        assert f'"{page}"' in qml, page
    assert "mac-phone" not in qml and "Omarchy" not in qml.split("*/")[-1].split("import")[-1]


def test_the_windows_pieces_are_one_shared_library():
    qmldir = (ROOT / "mascot/mac/qmldir").read_text()
    assert "singleton Theme" in qmldir
    for line in qmldir.splitlines():
        parts = line.split()
        if len(parts) == 3:                  # Name 1.0 File.qml
            assert (ROOT / "mascot/mac" / parts[2]).exists(), parts[2]
    for name in ("home", "mascot", "voice", "brain", "vocab", "phone", "updates", "file", "log", "restart"):
        assert (ROOT / f"mascot/mac/icons/{name}.svg").exists(), name
    for qml in ("mac-window.qml", "mac-setup.qml"):
        assert 'import "mac"' in (ROOT / "mascot" / qml).read_text()


def test_the_setup_window_looks_again_when_it_comes_to_the_front():
    qml = (ROOT / "mascot/mac-setup.qml").read_text()
    assert "onActiveChanged: if (active) auren.recheck()" in qml
    assert "screenAsked" not in qml and "turningText" in qml
    assert "onActiveChanged: if (active) settings.focused()" in (ROOT / "mascot/mac-window.qml").read_text()


# --- the menu bar menu ---------------------------------------------------------------------------------

def test_the_menu_has_its_items_in_order():
    src = MASCOT_SRC.read_text()
    order = ["status = item(", 'talk = item("Talk"', 'fresh = item("Fresh conversation"', 'stop = item("Stop"',
             'voice_on = item("Speak replies"', 'autosend = item("Send when I stop talking"',
             'choices("Voice"', 'choices("Brain"', 'choices("Safety"', 'addMenu("Mascot")',
             'item("Open Auren…"', 'item("Phone…"', 'item("Set up Auren…"', 'item("Update Auren…"',
             'item("Restart Auren"', 'item("Quit Auren"']
    at = [src.find(o) for o in order]
    assert -1 not in at, [o for o, i in zip(order, at, strict=True) if i < 0]
    assert at == sorted(at)
    assert "Edit settings" not in src
    assert 'QKeySequence("Ctrl+,")' in src and "MenuRole.NoRole" in src


def test_the_menu_shows_stop_only_while_busy_and_marks_setup():
    src = MASCOT_SRC.read_text()
    assert 'stop.setEnabled(alive and st.get("state") in settings_window.BUSY)' in src
    assert "status.setEnabled(False)" in src
    assert '"Finish setting up Auren…" if needs' in src
    assert "update_item.setVisible(bool(waiting))" in src
    assert "menu.aboutToShow.connect(lambda: (bridge.check(force=True), refresh()))" in src


def test_auren_open_shows_setup_until_it_is_done_then_the_window():
    src = MASCOT_SRC.read_text()
    assert 'bridge.showWindow.connect(lambda: window_ui.openPage("home"))' in src
    assert 'result.get("ready") and not result.get("restart")' in src


# --- the Qt bridges (where PySide6 is) -----------------------------------------------------------------

def _qt():
    pytest.importorskip("PySide6")
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


def _until(app, cond, secs=5):
    end = time.time() + secs
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    return cond()


@pytest.fixture
def window(tmp_path, monkeypatch):
    """The window's bridge, with a stand-in for the mascot's Bridge and the Phone page's, in throwaway files."""
    app = _qt()
    from PySide6.QtCore import QObject, Signal

    from auren import settings, update, vocab
    from auren.mac import service
    monkeypatch.setattr(sw, "PANEL_PREFS", tmp_path / "panel.json")
    monkeypatch.setattr(sw, "HISTORY", tmp_path / "history.jsonl")
    monkeypatch.setattr(update, "status", lambda: {"state": "current", "checked_at": time.time()})
    monkeypatch.setattr(update, "installed", lambda: {"mode": "brew", "version": "0.1.7"})
    monkeypatch.setattr(settings, "current", lambda: {"voice": True, "brain": "auto"})
    calls = []
    monkeypatch.setattr(service, "running", lambda name: True)
    monkeypatch.setattr(service, "start", lambda name: calls.append(("start", name)))
    monkeypatch.setattr(service, "stop", lambda name: calls.append(("stop", name)))
    monkeypatch.setattr(vocab, "run", lambda cmd, word="": calls.append(("vocab", cmd, word)) or
                        {"ok": True, "message": "", "mine": ["herdr"], "defaults": [], "limit": 80})
    monkeypatch.setattr(update, "check", lambda notify=False: calls.append(("check", notify)))

    class Auren(QObject):
        changed = Signal()
        setupChanged = Signal()

        def __init__(self):
            super().__init__()
            self._state = {"state": "idle", "voice": True}
            self._alive = True
            self._prefs = {"mascot": "auren", "matchVoice": True}
            self._place = {"x": 10, "y": 20, "size": 1, "hidden": False}
            self._setup = {}

        def check(self, force=False):
            calls.append(("check-setup", force))

        def savePlace(self, *a):
            calls.append(("place", *a))

        def set_hidden(self, hidden):
            calls.append(("hidden", hidden))

        def reset_place(self):
            calls.append(("reset",))

        def restart(self):
            calls.append(("restart",))

        def openSetup(self):
            calls.append(("setup",))

    class Phone:
        def showPage(self):
            calls.append(("phone", "show"))

        def hidePage(self):
            calls.append(("phone", "hide"))

    prefs, ctl = [], []
    auren = Auren()
    ui = sw.bridge(auren, Phone(), ctl.append, prefs.append)
    return app, ui, auren, calls, prefs, ctl


def test_opening_a_page_shows_the_window_and_checks_setup(window):
    app, ui, auren, calls, _, _ = window
    raised = []
    ui.raiseWindow.connect(lambda: raised.append(1))
    ui.openPage("phone")
    assert ui.open and ui.page == "phone" and raised and ("phone", "show") in calls and ("check-setup", True) in calls
    ui.setPage("vocab")                               # leaving the Phone page ends a pairing
    assert calls.count(("phone", "hide")) >= 1 and _until(app, lambda: ui.vocab["mine"] == ["herdr"])
    ui.setPage("nowhere")
    assert ui.page == "home"
    ui.close()
    assert not ui.open


def test_the_window_turns_auren_off_and_on_through_its_launch_agent(window):
    app, ui, auren, calls, _, _ = window
    ui.toggleAuren()
    assert _until(app, lambda: ("stop", "daemon") in calls)
    auren._alive = False
    ui._service = False
    ui.toggleAuren()
    assert _until(app, lambda: ("start", "daemon") in calls)
    ui._upd = {"state": "updating"}                      # the update stops and starts Auren itself
    n = len(calls)
    ui.toggleAuren()
    time.sleep(0.05)
    assert len(calls) == n


def test_picking_a_mascot_picks_its_voice_once_auren_is_idle(window):
    app, ui, auren, calls, prefs, ctl = window
    ui.setMascot("veyra")
    assert prefs == [{"mascot": "veyra"}] and ctl == ["voice-name Juniper Hi, I'm Veyra."]
    auren._prefs = {"mascot": "veyra", "matchVoice": False}
    ui.setMascot("auren")
    assert ctl == ["voice-name Juniper Hi, I'm Veyra."]
    ui.setMascot("nobody")
    assert prefs == [{"mascot": "veyra"}, {"mascot": "auren"}]


def test_the_mascot_page_writes_prefs_and_place(window):
    app, ui, auren, calls, prefs, _ = window
    ui.setPref("bubble", False)
    ui.setSize(9.0)
    ui.setShown(False)
    ui.resetPlace()
    assert prefs == [{"bubble": False}]
    assert ("place", 10, 20, 2.2, False) in calls and ("hidden", True) in calls and ("reset",) in calls


def test_the_vocabulary_page_asks_for_a_word_first(window):
    app, ui, _, calls, _, _ = window
    ui.vocabRun("add", "  ")
    assert ui.vocab["message"] == "Type a word first." and not [c for c in calls if c[0] == "vocab"]
    ui.vocabRun("add", " Hyprland ")
    assert _until(app, lambda: ("vocab", "add", "Hyprland") in calls and not ui.vocabBusy)
    ui.vocabRun("drop", "x")
    assert ("vocab", "drop", "x") not in calls


def test_the_updates_page_checks_and_remembers_the_notification_choice(window):
    app, ui, _, calls, _, _ = window
    ui.checkUpdates()
    assert ui.checking and _until(app, lambda: not ui.checking) and ("check", False) in calls
    assert ui.notifyUpdates
    ui.setNotifyUpdates(False)
    assert not ui.notifyUpdates and json.loads(sw.PANEL_PREFS.read_text()) == {"notifyUpdates": False}
    ui._last_check = 0
    ui.tick()
    assert _until(app, lambda: ("check", False) in calls[calls.index(("check", False)) + 1:] or not ui.checking)
    assert ui.versionText == "Auren 0.1.7 (Homebrew)"


def test_the_window_follows_the_mascots_bridge(window):
    app, ui, auren, *_ = window
    seen = []
    ui.changed.connect(lambda: seen.append(1))
    auren.changed.emit()
    auren.setupChanged.emit()
    assert len(seen) == 2 and ui.statusLine == "Auren is ready"


def test_auren_app_opens_setup_until_it_is_done_else_the_window(tmp_path, monkeypatch):
    """The real Bridge: `auren open` leaves a request; the check that follows says which window."""
    _qt()
    from auren.mac import app as mac_app
    from auren.mac import mascot
    from auren.mac import setup as auren_setup
    monkeypatch.setattr(mascot, "PREFS", tmp_path / "prefs.json")
    monkeypatch.setattr(mascot, "PLACE", tmp_path / "place.json")
    monkeypatch.setattr(mascot, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(mac_app, "SETUP_REQUEST", tmp_path / "setup.request")
    monkeypatch.setattr(auren_setup, "done", lambda: True)
    monkeypatch.setattr(auren_setup, "check", lambda: None)     # openSetup's own check: nothing to ask here
    b = mascot.make_bridge()
    shown, setups = [], []
    b.showWindow.connect(lambda: shown.append(1))
    b.raiseSetup.connect(lambda: setups.append(1))
    b._first_check = False
    b._open_app = True
    b._take_check([time.monotonic(), {"running": True, "ready": False, "restart": False}])
    assert setups and not shown and b.setupOpen
    b._setup_open = False
    b._open_app = True
    ready = {"running": True, "microphone": "granted", "accessibility": True, "key": True, "brainReady": True,
             "ready": True, "restart": False}
    b._take_check([time.monotonic(), ready])
    assert shown == [1] and len(setups) == 1
    b._open_app = True
    b._take_check([time.monotonic(), None])            # the check failed: setup says what's wrong
    assert len(setups) == 2
    assert b.setupNeeds == ""                          # the last good check said ready


def test_the_page_signal_comes_only_when_the_page_changes(window):
    """The window scrolls to the top and cancels Update's confirmation on pageChanged: not on every refresh."""
    app, ui, auren, *_ = window
    pages = []
    ui.pageChanged.connect(lambda: pages.append(ui.page))
    ui.openPage("updates")
    ui.reload()
    auren.changed.emit()
    ui.setPage("updates")
    ui.focused()
    assert pages == ["updates"]
    ui.setPage("voice")
    assert pages == ["updates", "voice"]
    qml = (ROOT / "mascot/mac-window.qml").read_text()
    assert qml.count("function onPageChanged()") == 2
