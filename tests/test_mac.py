"""The parts of the macOS port that run anywhere: parsing, labels, plists, the installer's lists."""

import re
import subprocess
import sys
import types
from pathlib import Path

import pytest

from auren.mac import hotkey, mascot, service, update
from auren.settings import _combo

ROOT = Path(__file__).parents[1]
INSTALL = ROOT / "install-macos.sh"


@pytest.mark.parametrize("combo,mods,code", [
    ("ctrl+alt+a", {"ctrl", "alt"}, 0),
    ("Control+Option+Shift+A", {"ctrl", "alt", "shift"}, 0),
    ("cmd+shift+space", {"cmd", "shift"}, 49),
    ("ctrl+alt+f5", {"ctrl", "alt"}, 96),
])
def test_talk_keys_parse_to_modifiers_and_a_key_code(combo, mods, code):
    assert hotkey.parse(combo) == (frozenset(mods), code)


@pytest.mark.parametrize("combo", ["a", "ctrl+alt", "ctrl+alt+nokey", "hyper+a"])
def test_a_talk_key_that_cant_be_listened_for_is_refused(combo):
    with pytest.raises(ValueError):
        hotkey.parse(combo)


def test_the_talk_key_setting_is_checked_and_written_plainly():
    assert _combo("Ctrl + Alt + A") == "ctrl+alt+a"
    for bad in ("a", "ctrl+alt", "ctrl+alt+nokey", "ctrl+shift"):
        with pytest.raises(ValueError):
            _combo(bad)


@pytest.mark.parametrize("combo,label", [
    ("ctrl+alt+a", "⌃⌥A"), ("ctrl+alt+shift+a", "⌃⌥⇧A"), ("cmd+shift+space", "⇧⌘SPACE"), ("option+control+b", "⌃⌥B"),
])
def test_the_menu_writes_talk_keys_the_mac_way(combo, label):
    assert mascot.key_label(combo) == label


def test_fresh_conversation_keys_are_named_for_the_platform(monkeypatch):
    from auren import settings
    monkeypatch.setattr(settings, "current", lambda: {"keys.talk_fresh": "ctrl+alt+shift+a"})
    monkeypatch.setattr(settings.sys, "platform", "darwin")
    assert settings.fresh_key() == "⌃⌥⇧A"
    monkeypatch.setattr(settings.sys, "platform", "linux")
    assert settings.fresh_key() == "Super+Shift+A"


def test_the_launch_agents_run_the_installed_auren_with_a_full_path(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "DATA_HOME", tmp_path / "share")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    p = service.plist("daemon", "/opt/homebrew/bin:/usr/bin:/custom/bin:/usr/bin")
    assert p["Label"] == "com.auren.daemon"
    assert p["ProgramArguments"] == [str(tmp_path / "share/venv/bin/auren"), "daemon"]
    path = p["EnvironmentVariables"]["PATH"].split(":")
    assert path[:2] == [str(Path.home() / ".local/bin"), str(tmp_path / "share/bin")]
    assert "/custom/bin" in path and len(path) == len(set(path)) and "/bin" in path
    assert p["KeepAlive"] == {"SuccessfulExit": False}
    assert p["StandardOutPath"].endswith("daemon.out.log")


def test_the_launch_agents_look_for_the_voice_where_it_was_downloaded(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "DATA_HOME", tmp_path / "share")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    for var in service.HF_VARS:
        monkeypatch.delenv(var, raising=False)
    assert not set(service.HF_VARS) & set(service.plist("daemon")["EnvironmentVariables"])
    monkeypatch.setenv("HF_HOME", "/Volumes/big/hf")
    monkeypatch.setenv("XDG_CACHE_HOME", "/Volumes/big/cache")
    monkeypatch.setenv("HF_HUB_CACHE", "relative")             # meaningless to launchd: left out
    env = service.plist("daemon")["EnvironmentVariables"]
    assert env["HF_HOME"] == "/Volumes/big/hf" and env["XDG_CACHE_HOME"] == "/Volumes/big/cache"
    assert "HF_HUB_CACHE" not in env


def test_update_s_systemctl_calls_are_answered_by_launchd(monkeypatch):
    calls = []
    monkeypatch.setattr(service, "running", lambda n: n == "daemon")
    monkeypatch.setattr(service, "stop", lambda n: calls.append(("stop", n)) or True)
    monkeypatch.setattr(service, "restart", lambda n: calls.append(("restart", n)) or True)
    assert update.launchctl_like("is-active", "--quiet", "auren") == 0
    assert update.launchctl_like("is-active", "--quiet", "auren-mascot") == 1
    assert update.launchctl_like("stop", "auren", "auren-mascot") == 0
    assert update.launchctl_like("restart", "auren") == 0
    assert calls == [("stop", "daemon"), ("stop", "mascot"), ("restart", "daemon")]


def test_the_mac_installer_is_valid_bash():
    subprocess.run(["bash", "-n", str(INSTALL)], check=True)


def test_the_mac_installers_browser_list_matches_auren_s():
    from auren.mac import browsers
    text = INSTALL.read_text()
    block = text[text.index("# >>> browsers"):text.index("# <<< browsers")]
    rows = [tuple(r.split("|")) for r in re.findall(r'"([a-z]+\|[^"]+)"', block)]
    assert rows == [(k.key, k.commands[0]) for k in browsers.KINDS]


def test_the_mac_installers_default_talk_keys_are_auren_s():
    from auren import settings
    text = INSTALL.read_text()
    assert "config set keys.talk ctrl+alt+a" in text and "config set keys.talk_fresh ctrl+alt+shift+a" in text
    if any(k.path == "keys.talk" for k in settings.KEYS):          # only on a Mac
        defaults = {k.path: k.default for k in settings.KEYS}
        assert (defaults["keys.talk"], defaults["keys.talk_fresh"]) == ("ctrl+alt+a", "ctrl+alt+shift+a")


@pytest.mark.parametrize("keys", ["cmd+q", "shift+cmd+w", "alt+cmd+escape", "option+command+esc", "cmd+alt+shift+q",
                                  "shift+option+cmd+backspace", "Ctrl+Shift+Q"])
def test_quit_close_and_delete_shortcuts_are_risky_in_any_order(keys):
    from auren.tools import _risky
    assert _risky(keys)


@pytest.mark.parametrize("keys", ["cmd+c", "cmd+v", "cmd+shift+t", "ctrl+alt+a"])
def test_ordinary_shortcuts_are_not_risky(keys):
    from auren.tools import _risky
    assert not _risky(keys)


@pytest.mark.parametrize("kind", ["rm", "bash", "sh -c", "python3"])
def test_without_herdr_only_coding_agents_open_in_a_terminal(kind, monkeypatch):
    from auren.mac import desktop
    monkeypatch.setattr(desktop, "terminal_run", lambda *a, **k: pytest.fail("ran a command"))
    with pytest.raises(desktop.DesktopError, match="isn't a coding agent"):
        desktop.start_in_terminal(kind, "/tmp", "anything")


def test_a_coding_agent_opens_with_its_own_command(monkeypatch, tmp_path):
    import json

    from auren import integrations
    from auren.mac import desktop
    ran = []
    monkeypatch.setattr(integrations, "agent_installed", lambda kind: True)
    monkeypatch.setattr(desktop, "terminal_run", lambda line, path, title: ran.append(line) or "Terminal")
    out = json.loads(desktop.start_in_terminal("cursor", str(tmp_path), "fix the 'tests'"))
    assert out["started"] == "cursor" and ran == ["cursor-agent 'fix the '\"'\"'tests'\"'\"''"]


# --- Auren.app and its setup window ----------------------------------------------------------------

def test_the_app_icon_is_a_valid_icns_with_every_size():
    import struct

    from auren.mac import app
    data = (app.SOURCE / "Auren.icns").read_bytes()
    assert data[:4] == b"icns" and struct.unpack(">I", data[4:8])[0] == len(data)
    kinds, i = [], 8
    while i < len(data):
        kind, n = data[i:i + 4], struct.unpack(">I", data[i + 4:i + 8])[0]
        assert data[i + 8:i + 12] == b"\x89PNG"
        kinds.append(kind)
        i += n
    assert sorted(kinds) == sorted(k for k, _ in app.ICNS_TYPES)


def test_the_app_names_auren_and_asks_for_the_microphone_in_its_own_words():
    import plistlib

    from auren.mac import app
    info = plistlib.loads((app.SOURCE / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == app.BUNDLE_ID and info["CFBundleExecutable"] == "Auren"
    assert info["LSUIElement"] is True and info["NSMicrophoneUsageDescription"]
    assert info["NSAppleEventsUsageDescription"]


def test_the_app_is_rebuilt_only_when_its_sources_change(tmp_path, monkeypatch):
    from auren.mac import app
    built = []

    def fake_build(dest, source=app.SOURCE):
        (dest / "Contents/MacOS").mkdir(parents=True)
        exe = dest / "Contents/MacOS/Auren"
        exe.write_text("#!/bin/sh\n")
        exe.chmod(0o755)
        built.append(dest)
    monkeypatch.setattr(app, "build", fake_build)
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(app, "forget_permissions", lambda: None)
    target, stamp = tmp_path / "Applications/Auren.app", tmp_path / "app.sha256"
    assert app.install(app=target, stamp=stamp) is True
    assert app.install(app=target, stamp=stamp) is False          # same sources: the permissions stay
    stamp.write_text("something older\n")
    assert app.install(app=target, stamp=stamp) is True
    assert len(built) == 2 and app.installed(target)


def test_the_launch_agents_run_through_the_app_when_it_is_there(monkeypatch, tmp_path):
    from auren.mac import app
    monkeypatch.setattr(service, "DATA_HOME", tmp_path / "share")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(app, "installed", lambda *a: True)
    p = service.plist("mascot")
    assert p["ProgramArguments"] == [str(app.executable()), "mascot"]
    assert p["EnvironmentVariables"]["AUREN_PYTHON"] == str(tmp_path / "share/venv/bin/python")
    monkeypatch.setattr(app, "installed", lambda *a: False)
    assert service.plist("mascot")["ProgramArguments"][0] == str(tmp_path / "share/venv/bin/auren")


def _setup_check(monkeypatch, status, found=(), brain="auto", key=True, restarted=None):
    from auren import config, settings
    from auren.mac import setup
    monkeypatch.setattr(config, "secret", lambda name: "k" if key else None)
    monkeypatch.setattr(settings, "current", lambda: {"brain": brain})
    monkeypatch.setattr(setup, "_brain_path", lambda: None)
    return setup.check(status, which=lambda name, path=None: f"/opt/homebrew/bin/{name}" if name in found else None,
                       restarted=restarted)


ALLOWED = {"microphone": "granted", "accessibility": True, "screen": False, "talk_keys": True}


def test_setup_is_ready_with_the_permissions_a_key_and_a_brain(monkeypatch):
    s = _setup_check(monkeypatch, ALLOWED, found=("codex",))
    assert s["ready"] and s["brainReady"] and not s["restart"] and s["running"]


@pytest.mark.parametrize("change", [{"microphone": "not asked"}, {"accessibility": False}])
def test_setup_is_not_ready_without_the_microphone_or_accessibility(monkeypatch, change):
    assert not _setup_check(monkeypatch, {**ALLOWED, **change}, found=("codex",))["ready"]


def test_setup_needs_a_key_and_a_brain(monkeypatch):
    assert not _setup_check(monkeypatch, ALLOWED, found=("codex",), key=False)["ready"]
    assert not _setup_check(monkeypatch, ALLOWED, found=())["ready"]
    assert not _setup_check(monkeypatch, ALLOWED, found=("codex",), brain="claude")["brainReady"]


def test_setup_says_when_auren_is_off_or_needs_a_restart(monkeypatch):
    from auren.mac import setup
    monkeypatch.setattr(setup, "_daemon_status", lambda: None)
    off = _setup_check(monkeypatch, None, found=("codex",), restarted={})
    assert not off["running"] and not off["ready"] and not off["restart"] and not off["starting"]
    restart = _setup_check(monkeypatch, {**ALLOWED, "talk_keys": False}, found=("codex",))
    assert restart["restart"]


@pytest.mark.parametrize("found,brain,action", [
    ((), "auto", "copy"), (("codex",), "claude", "codex"), (("claude",), "codex", "claude"),
    (("codex",), "auto", ""), (("claude", "codex"), "auto", ""),
])
def test_the_brain_row_offers_what_fixes_it(found, brain, action):
    from auren.mac import setup
    text, got = setup.brain_text({"claude": "claude" in found, "codex": "codex" in found, "brain": brain})
    assert got == action and text


def test_the_key_is_saved_privately_and_replaces_an_old_one(tmp_path):
    from auren.mac import setup
    path = tmp_path / "secrets.env"
    path.write_text("OTHER=1\nASSEMBLYAI_API_KEY=old\n")
    assert setup.save_key("  0123456789abcdef0123456789abcdef \n", path) == ""
    assert path.read_text() == "OTHER=1\nASSEMBLYAI_API_KEY=0123456789abcdef0123456789abcdef\n"
    assert path.stat().st_mode & 0o777 == 0o600
    assert setup.save_key("not a key!", path) != ""


def _fake_build(built):
    def build(dest, source=None):
        (dest / "Contents/MacOS").mkdir(parents=True)
        exe = dest / "Contents/MacOS/Auren"
        exe.write_text(f"#!/bin/sh\n# build {len(built)}\n")
        exe.chmod(0o755)
        built.append(dest)
    return build


def test_a_rebuilt_app_replaces_the_old_and_has_macos_ask_again(tmp_path, monkeypatch):
    from auren.mac import app, setup
    built, ran = [], []
    monkeypatch.setattr(app, "build", _fake_build(built))
    monkeypatch.setattr(app.subprocess, "run", lambda cmd, **k: ran.append(cmd[0]))
    monkeypatch.setattr(setup, "DONE", tmp_path / "setup.json")
    target, stamp = tmp_path / "Applications/Auren.app", tmp_path / "app.sha256"
    app.install(app=target, stamp=stamp)
    assert "tccutil" not in ran                       # a first install has no old grants
    setup.mark_done()
    assert app.install(force=True, app=target, stamp=stamp)
    assert "# build 1" in app.executable(target).read_text()
    assert ran.count("tccutil") == len(app.TCC_SERVICES) and not setup.done()   # the window opens again
    assert [p.name for p in target.parent.iterdir()] == ["Auren.app"]          # no temp folder left


def test_a_failed_build_keeps_the_old_app(tmp_path, monkeypatch):
    from auren.mac import app
    built = []
    monkeypatch.setattr(app, "build", _fake_build(built))
    monkeypatch.setattr(app.subprocess, "run", lambda *a, **k: None)
    target, stamp = tmp_path / "Applications/Auren.app", tmp_path / "app.sha256"
    app.install(app=target, stamp=stamp)

    def broken(dest, source=None):
        raise app.AppError("clang failed")
    monkeypatch.setattr(app, "build", broken)
    with pytest.raises(app.AppError):
        app.install(force=True, app=target, stamp=stamp)
    assert app.current(target, stamp) and "# build 0" in app.executable(target).read_text()


def test_restart_reloads_an_agent_whose_program_or_path_changed(monkeypatch, tmp_path):
    import plistlib
    monkeypatch.setattr(service, "AGENTS_DIR", tmp_path)
    app = "/Users/a/Applications/Auren.app/Contents/MacOS/Auren"
    with open(service.plist_path("daemon"), "wb") as f:
        plistlib.dump({"ProgramArguments": [app, "daemon"],
                       "EnvironmentVariables": {"PATH": "/Users/a/.local/bin:/opt/homebrew/bin:/usr/bin"}}, f)
    loaded = {"program": "/Users/a/.local/share/auren/venv/bin/auren", "path": "/Users/a/.local/bin:/usr/bin"}
    calls = []
    monkeypatch.setattr(service, "loaded", lambda n: True)
    monkeypatch.setattr(service, "stop", lambda n: calls.append("stop") or True)
    monkeypatch.setattr(service, "start", lambda n: calls.append("start") or True)

    def launchctl(*args):
        calls.append(args[0])
        out = (f"com.auren.daemon = {{\n\tstate = running\n\tprogram = {loaded['program']}\n"
               f"\tenvironment = {{\n\t\tPATH => {loaded['path']}\n\t\tXPC_SERVICE_NAME => com.auren.daemon\n\t}}\n}}\n")
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(service, "_launchctl", launchctl)
    assert service.restart("daemon") and calls[-2:] == ["stop", "start"]    # kickstart would keep the old one
    calls.clear()
    loaded["program"] = app                     # the same program, but Codex was installed since: a new PATH
    assert service.restart("daemon") and calls[-2:] == ["stop", "start"]
    calls.clear()
    loaded["path"] = "/Users/a/.local/bin:/opt/homebrew/bin:/usr/bin"
    assert service.restart("daemon") and calls[-1] == "kickstart" and "stop" not in calls


def test_setup_offers_no_restart_for_a_key_no_restart_fixes(monkeypatch):
    from auren import config, settings
    from auren.mac import setup
    monkeypatch.setattr(config, "secret", lambda name: "k")
    monkeypatch.setattr(settings, "current", lambda: {"brain": "auto", "keys.talk": "ctrl+nothing"})
    monkeypatch.setattr(setup, "_brain_path", lambda: None)
    s = setup.check({**ALLOWED, "talk_keys": False}, which=lambda name, path=None: "/bin/codex" if name == "codex" else None,
                    restarted={})
    assert not s["restart"]


def test_the_key_goes_through_a_symlinked_secrets_file(tmp_path):
    from auren.mac import setup
    real, link = tmp_path / "dotfiles/secrets.env", tmp_path / "secrets.env"
    real.parent.mkdir()
    real.write_text("OTHER=1\n")
    link.symlink_to(real)
    (real.parent / "secrets.env.tmp").write_text("left over")
    assert setup.save_key("0123456789abcdef0123456789abcdef", link) == ""
    assert link.is_symlink() and "ASSEMBLYAI_API_KEY=0123456789abcdef0123456789abcdef" in real.read_text()
    assert real.stat().st_mode & 0o777 == 0o600


def test_accessibility_shows_macos_prompt_first_then_the_settings_page(monkeypatch):
    from auren.mac import permissions
    opened, prompted = [], []
    monkeypatch.setattr(permissions, "_prompted", set())
    monkeypatch.setattr(permissions, "accessibility", lambda prompt=False: prompted.append(prompt) and False)
    monkeypatch.setattr(permissions, "status", dict)
    monkeypatch.setattr(permissions.subprocess, "Popen", lambda cmd, **k: opened.append(cmd))
    permissions.request_one("accessibility")
    assert True in prompted and not opened
    permissions.request_one("accessibility")
    assert opened == [["open", permissions.PANES["accessibility"]]]


def test_the_mascot_stays_on_one_screen_of_two(monkeypatch):
    laptop = ({"x": 0, "y": 0, "width": 1470, "height": 956}, {"x": 0, "y": 33, "width": 1470, "height": 850})
    monitor = ({"x": 1470, "y": -200, "width": 1920, "height": 1080}, {"x": 1470, "y": -170, "width": 1920, "height": 1050})
    both = [laptop, monitor]
    assert mascot.screen_area(both, 100, 100) == laptop[1]
    assert mascot.screen_area(both, 2000, 500) == monitor[1]
    assert mascot.screen_area(both, 1000, 1500) == laptop[1]       # below the laptop: the nearest screen
    assert mascot.screen_area(both, 3600, 0) == monitor[1]
    assert mascot.screen_area([], 5, 5)["width"] > 0


def test_the_talk_keys_start_once_accessibility_is_allowed(monkeypatch):
    import asyncio

    from auren.daemon import Daemon
    from auren.mac import permissions
    answers = iter([False, False, True])
    monkeypatch.setattr(permissions, "accessibility", lambda: next(answers))

    class D:
        _hotkeys = None
    d = D()
    d._listen_for_keys = lambda: setattr(d, "_hotkeys", "tap")
    asyncio.run(Daemon._keys_once_allowed(d, every=0.01))       # returns only once the keys are on
    assert d._hotkeys == "tap"


# --- the mascot on macOS -----------------------------------------------------------------------

def test_shared_mascots_use_a_font_macos_has():
    """"monospace" doesn't exist on macOS: Qt then searches every font mid-layout (the mascot log's
    binding loop). The shared mascots ask for Menlo there."""
    root = Path(__file__).resolve().parent.parent / "mascot"
    for f in [root / "mac.qml", *(root / "mascots").rglob("*.qml")]:
        for line in f.read_text().splitlines():
            if 'font.family: "monospace"' in line:
                raise AssertionError(f"{f.name}: {line.strip()}")

# --- fixes from the Codex review ---------------------------------------------------------------------

def test_closing_without_a_close_button_presses_cmd_w_only_in_that_window(monkeypatch):
    from auren.mac import desktop, screen
    w = {"address": "7", "class": "Preview", "title": "report.pdf", "pid": 1}
    pressed, focused = [], []
    monkeypatch.setattr(desktop, "windows", lambda: [w])
    monkeypatch.setattr(desktop.macwin, "close", lambda w: False)              # no close button
    monkeypatch.setattr(screen, "focus", lambda address: focused.append(address))
    monkeypatch.setattr(screen, "press_keys", lambda keys: pressed.append(keys))
    monkeypatch.setattr(screen, "_active_window", lambda: "3")                 # Chrome stayed in front
    with pytest.raises(desktop.DesktopError, match="didn't come to the front"):
        desktop.close_window(w)
    assert focused == ["7"] and pressed == []                                  # nothing sent to Chrome
    monkeypatch.setattr(screen, "_active_window", lambda: "7")
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "windows", lambda: [w] if not pressed else [])
    assert desktop.close_window(w).startswith("closed") and pressed == ["cmd+w"]


def test_stopping_speech_tears_the_stream_down_once(monkeypatch):
    import sys
    import threading
    import types

    calls = []

    class Stream:
        def __init__(self, **kw):
            pass

        def start(self):
            pass

        def stop(self):
            calls.append("stop")

        def abort(self):
            calls.append("abort")

        def close(self):
            calls.append("close")
    monkeypatch.setitem(sys.modules, "sounddevice", types.SimpleNamespace(RawOutputStream=Stream))
    from auren.mac.audio import Player
    p = Player(44100)
    p.stdin.write(b"\0" * 400)
    p.stdin.close()
    t = threading.Thread(target=p.wait)          # the speaking thread, waiting for it to play out
    t.start()
    p.kill()                                     # Stop pressed meanwhile
    t.join(2)
    assert calls == ["abort", "close"]           # aborted (not played out), closed once


def test_the_clipboard_comes_back_with_every_type_and_the_password_marker(monkeypatch):
    import sys
    import types

    from auren.mac import clipboard

    class Board:
        def __init__(self):
            self.items = {"public.utf8-plain-text": b"hunter2", clipboard.CONCEALED: b"", "public.rtf": b"{\\rtf1}"}

        def types(self):
            return list(self.items)

        def dataForType_(self, t):
            return self.items.get(t)

        def clearContents(self):
            self.items = {}

        def declareTypes_owner_(self, types, owner):
            self.items = {}

        def setData_forType_(self, data, t):
            self.items[t] = bytes(data)
            return True
    board = Board()
    monkeypatch.setattr(clipboard, "_board", lambda: board)
    fake = types.SimpleNamespace(NSData=types.SimpleNamespace(dataWithBytes_length_=lambda d, n: d,
                                                              data=lambda: b""),
                                 NSPasteboardTypeString="public.utf8-plain-text", NSPasteboardTypePNG="public.png")
    monkeypatch.setitem(sys.modules, "Foundation", fake)
    monkeypatch.setitem(sys.modules, "AppKit", fake)
    saved = clipboard.save()
    before = dict(board.items)
    clipboard.copy(b"typed by auren", None)
    clipboard.restore(saved, b"typed by auren")
    assert board.items == before                 # the password is still marked concealed, the rich text is back


def test_a_mistyped_service_name_does_nothing(monkeypatch, capsys):
    from auren import cli
    monkeypatch.setattr(service, "uninstall", lambda n: pytest.fail("uninstalled " + n))
    assert cli._service(["uninstall", "masoct"]) == 2
    assert "no service called masoct" in capsys.readouterr().err


def test_the_menu_bar_offers_every_safety_level():
    """main's #50 added "never" (Full access) and renamed "free" to Auto: the Mac menu offers them too."""
    from auren import safety
    src = (Path(__file__).resolve().parent.parent / "src/auren/mac/mascot.py").read_text()
    menu = re.search(r'choices\("Safety", (\[.*?\]), "safety"\)', src, re.S).group(1)
    assert [v for v, _ in eval(menu)] == list(safety.LEVELS)
    assert ("free", "Auto") in eval(menu) and ("never", "Full access") in eval(menu)


# --- the phone remote on macOS: the menu bar icon's Phone window ------------------------------------

@pytest.fixture
def phone_files(tmp_path, monkeypatch):
    from auren import phone
    d = tmp_path / "phone"
    for name, value in {"PHONE_DIR": d, "DEVICES": d / "devices.json", "PAIRING": d / "pairing.json",
                        "INBOX": d / "inbox.jsonl", "FILES": d / "files", "STATUS": tmp_path / "phone.json",
                        "PAIR_QR": tmp_path / "phone-pair.png", "APP_QR": tmp_path / "phone-app.png",
                        "TAILSCALE_SEEN": d / "tailscale.json"}.items():
        monkeypatch.setattr(phone, name, value)
    return phone


def test_the_menu_bar_opens_the_phone_page_of_the_auren_window():
    src = (ROOT / "src/auren/mac/mascot.py").read_text()
    assert 'item("Phone…", lambda: window_ui.openPage("phone"))' in src
    assert 'qml.with_name("mac-window.qml")' in src and (ROOT / "mascot/mac-window.qml").exists()
    assert not (ROOT / "mascot/mac-phone.qml").exists()
    assert 'setContextProperty("phone", phone_ui)' in src and "aboutToQuit.connect(phone_ui.stop)" in src


def test_the_phone_window_says_where_auren_listens():
    from auren.mac import phone_window as pw
    on = {"on": True, "addrs": [{"ip": "100.64.1.2", "via": "tailscale", "ok": True},
                                {"ip": "192.168.1.23", "via": "lan", "ok": True},
                                {"ip": "10.0.0.2", "via": "lan", "ok": False}]}
    assert pw.status_text(on, alive=True) == "Listening on Tailscale (100.64.1.2) and Wi-Fi (192.168.1.23)."
    assert pw.status_text(on, alive=False) == "Auren is off, so the phone can't reach it."
    assert pw.status_text({}, True).startswith("Not listening: no phone is paired")
    assert pw.status_text({"on": False, "idle": True}, True).startswith("Not listening: no phone is paired")
    assert pw.status_text({"on": False, "why": "nowhere to listen"}, True) == "Not listening: nowhere to listen."
    assert pw.tailscale_warn({"missing": {"tailscale": "Tailscale is off: turn it on"}}).startswith(
        "Tailscale is off on this Mac")
    assert pw.tailscale_warn({"missing": {"tailscale": "Tailscale isn't installed"}}) == ""
    assert pw.tailscale_warn({}) == ""


def test_the_phone_window_lists_phones_and_how_they_are_connected():
    import time

    from auren.mac import phone_window as pw
    st = {"on": True, "connected": [{"id": "a1", "name": "Pixel", "via": "tailscale"}]}
    rows = pw.phone_rows([{"id": "a1", "name": "Pixel", "created": time.time(), "lastSeen": time.time()},
                          {"id": "b2", "name": "Tablet", "created": time.time() - 7200, "lastSeen": 0}], st)
    assert rows[0] == {"id": "a1", "name": "Pixel", "connected": True,
                       "detail": "Connected over Tailscale · paired just now"}
    assert rows[1]["detail"] == "Last seen never · paired 2 h ago" and not rows[1]["connected"]
    assert not pw.phone_rows([{"id": "a1", "name": "Pixel"}], {**st, "on": False})[0]["connected"]


def test_the_phone_window_shows_qr_codes_from_the_runtime_folder():
    import sys

    from auren.mac import phone_window as pw
    path = "/Users/me/Library/Application Support/auren-runtime/auren/phone-pair.png"
    assert pw.file_url(path, 1795000000.5) == ("file:///Users/me/Library/Application%20Support/auren-runtime/auren/"
                                               "phone-pair.png?1795000000.5")
    assert pw.file_url("") == ""
    program, args = pw.pair_command()                  # the venv's Python, as the mascot runs on it
    assert program == sys.executable and args == ["-m", "auren.cli", "phone", "pair", "--json"]


def test_the_phone_windows_pairing_follows_auren_phone_pair():
    import json

    from auren.mac import phone_window as pw
    p = pw.Pairing()
    assert p.start() and p.stage == "starting" and not p.start()          # one at a time
    uri = "auren://pair?v=1&hosts=192.168.1.23%2C100.64.1.2&port=8735&code=s3cret-code_x&fp=ab&pc=mac"
    assert p.line("not json") == "" and p.stage == "starting"
    assert p.line(json.dumps({"uri": uri, "qr": "/r/phone-pair.png", "expires": 1.0, "warn": "",
                              "where": [{"ip": "192.168.1.23", "via": "lan"}, {"ip": "100.64.1.2", "via": "tailscale"}]}))
    assert p.stage == "showing" and p.code() == "s3cret-code_x"
    assert p.where() == "Wi-Fi (192.168.1.23) or Tailscale (100.64.1.2)"
    assert p.line(json.dumps({"paired": {"id": "a1", "name": "Pixel 9"}})) == "paired"
    assert p.note == "Paired Pixel 9." and not p.busy
    p.exited()
    assert p.stage == "paired"                                             # a pairing that ended well stays so
    p.start()
    p.line(json.dumps({"uri": uri, "qr": "/r/q.png", "expires": 1.0}))
    assert p.cancel() == ("s3cret-code_x", "/r/q.png") and p.stage == "" and p.code() == ""   # to end it
    p.start()
    p.line(json.dumps({"error": "The phone server is off: the Auren daemon isn't running (auren service start)"}))
    assert p.stage == "error" and "auren service start" in p.note
    p.start()
    assert p.exited() == ("", "")                                          # died without a word
    assert p.stage == "error" and "auren doctor" in p.note
    p.start()
    p.line(json.dumps({"uri": uri, "qr": "/r/q2.png", "expires": 1.0}))
    assert p.exited() == ("s3cret-code_x", "/r/q2.png") and p.stage == "error"   # died with a code up
    p.start()
    p.line(json.dumps({"expired": True}))
    assert p.stage == "expired" and "pair again" in p.note


def test_get_the_app_text_names_the_version():
    from auren.mac import phone_window as pw
    assert pw.app_text({"version": "0.1.8"}).startswith("It downloads the Auren app 0.1.8 for Android.")
    assert pw.app_text({}).startswith("It opens Auren's latest release")


def test_the_phone_window_pairs_cancels_and_unpairs_for_real(phone_files, monkeypatch, tmp_path):
    """The Qt bridge itself, with a stand-in for auren phone pair --json (where PySide6 is: the macOS job)."""
    pytest.importorskip("PySide6")
    import json
    import os
    import sys
    import time

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication

    from auren.mac import phone_window as pw
    app = QCoreApplication.instance() or QCoreApplication([])
    phone = phone_files
    code, _ = phone.start_pairing()
    dev, _ = phone.pair(code, "Pixel")
    child = tmp_path / "pair.py"
    child.write_text("import json, signal, sys, time\n"
                     "signal.signal(signal.SIGTERM, lambda *_: (print(json.dumps({'cancelled': True}), flush=True),"
                     " sys.exit(130)))\n"
                     f"print(json.dumps({{'uri': 'auren://pair?code=abc', 'qr': {str(tmp_path / 'q.png')!r},"
                     " 'expires': time.time() + 300, 'where': [{'ip': '192.168.1.23', 'via': 'lan'}]}), flush=True)\n"
                     "time.sleep(30)\n")
    monkeypatch.setattr(pw, "pair_command", lambda: (sys.executable, [str(child)]))
    ended = []
    monkeypatch.setattr(phone, "end_pairing", ended.append)
    ui = pw.bridge(lambda: True)

    def until(cond, secs=10):
        end = time.time() + secs
        while not cond() and time.time() < end:
            app.processEvents()
            time.sleep(0.02)
        return cond()
    ui.showPage()
    assert ui.open and [p["name"] for p in ui.phones] == ["Pixel"]
    phone.STATUS.write_text(json.dumps({"on": True, "addrs": [{"ip": "192.168.1.23", "via": "lan", "ok": True}]}))
    os.utime(phone.STATUS, ns=(1, 1))
    ui.poll()
    assert ui.status == "Listening on Wi-Fi (192.168.1.23)."
    ui.startPairing()
    assert until(lambda: ui.pairStage == "showing"), ui.pairStage
    assert ui.pairWhere == "Wi-Fi (192.168.1.23)" and ui.pairQr.startswith("file://")
    proc = ui._proc
    ui.cancelPairing()
    assert ui.pairStage == "" and until(lambda: ended == ["abc"])
    assert ui.stopping
    ui.startPairing()                                   # not while the old one stops: it would share its cleanup
    assert ui.pairStage == "" and ui._proc is None
    assert until(lambda: not ui.stopping) and proc.state() == proc.ProcessState.NotRunning
    ui.unpair(dev["id"])
    assert ui.unpairAsk == dev["id"] and phone.devices()                  # asks once more
    ui.unpair(dev["id"])
    assert until(lambda: ui.phones == []) and phone.devices() == []
    ui.hidePage()
    assert not ui.open


def test_the_phone_window_cleans_up_whatever_ends_the_pairing(phone_files, monkeypatch, tmp_path):
    from auren.mac import phone_window as pw
    phone = phone_files
    ended = []
    monkeypatch.setattr(phone, "end_pairing", ended.append)
    mine = phone.pair_qr_file()
    mine.write_bytes(b"png")
    other = tmp_path / "picture.png"
    other.write_bytes(b"png")
    pw.cleanup("abc", str(mine))
    pw.cleanup("", str(other))                        # not a pairing's QR file: it stays
    assert ended == ["abc"] and not mine.exists() and other.exists()


def test_a_pairing_child_that_dies_takes_its_code_and_qr_with_it(phone_files, monkeypatch, tmp_path):
    """auren phone pair --json killed or crashed (no cleanup of its own): the window ends the code it showed
    and deletes its QR code, and the next pairing can start at once."""
    pytest.importorskip("PySide6")
    import json
    import sys
    import time

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QCoreApplication

    from auren.mac import phone_window as pw
    app = QCoreApplication.instance() or QCoreApplication([])
    phone = phone_files
    qr = phone.pair_qr_file()
    qr.parent.mkdir(parents=True, exist_ok=True)
    qr.write_bytes(b"png")
    child = tmp_path / "pair.py"
    child.write_text("import json, os, time\n"
                     f"print(json.dumps({{'uri': 'auren://pair?code=xyz', 'qr': {str(qr)!r}, 'expires': time.time() + 300,"
                     " 'where': []}), flush=True)\n"
                     "time.sleep(0.3)\nos._exit(9)\n")
    monkeypatch.setattr(pw, "pair_command", lambda: (sys.executable, [str(child)]))
    ended = []
    monkeypatch.setattr(phone, "end_pairing", ended.append)
    ui = pw.bridge(lambda: True)
    ui.showPage()
    ui.startPairing()
    end = time.time() + 10
    while not (ended and not qr.exists()) and time.time() < end:
        app.processEvents()
        time.sleep(0.02)
    assert ended == ["xyz"] and not qr.exists()
    assert ui.pairStage == "error" and not ui.stopping and ui._proc is None
    ui.startPairing()
    assert ui.pairStage == "starting"
    ui.stop()
    assert json.dumps(ended)


def test_the_mascot_quits_through_qt_on_sigterm():
    """launchd stops the mascot with SIGTERM: Qt's aboutToQuit must run so an open pairing ends."""
    src = (ROOT / "src/auren/mac/mascot.py").read_text()
    assert "signal.signal(sig, lambda *_: app.quit())" in src and "(signal.SIGTERM, signal.SIGINT)" in src


# --- noticing permissions without a restart by hand ----------------------------------------------

def _grants(tmp_path, live, new, clock=None):
    from auren.mac import permissions
    asked = []

    def ask():
        asked.append(1)
        return dict(new)
    g = permissions.Grants(ask=ask, live=lambda: dict(live), path=tmp_path / "restart.json", ttl=2.0,
                           clock=clock or (lambda: 0.0))
    return g, asked


def test_a_new_process_is_asked_only_while_something_is_missing(tmp_path):
    g, asked = _grants(tmp_path, {"microphone": "granted", "accessibility": True, "screen": True}, {})
    assert g.check()["waiting"] == [] and asked == []


def test_a_permission_a_new_process_has_is_waiting_for_a_restart(tmp_path):
    now = [0.0]
    g, asked = _grants(tmp_path, {"microphone": "granted", "accessibility": True, "screen": False},
                       {"microphone": "granted", "screen": True}, clock=lambda: now[0])
    s = g.check()
    assert s["waiting"] == ["screen"] and s["screen"] is True and len(asked) == 1
    g.check()
    assert len(asked) == 1                      # the answer is kept for a couple of seconds
    now[0] = 2.5
    g.check()
    assert len(asked) == 2
    g.forget_fresh()                            # an Allow click: asked again at once
    g.check()
    assert len(asked) == 3


def test_accessibility_the_daemon_still_sees_as_off_waits_for_a_restart(tmp_path):
    """Turned off (or reset) and on again, Accessibility can stay off for the running daemon: a new process
    sees it, so the window shows it allowed and the daemon restarts for it."""
    g, asked = _grants(tmp_path, {"microphone": "granted", "accessibility": False, "screen": True},
                       {"microphone": "granted", "accessibility": True, "screen": True})
    s = g.check()
    assert s["waiting"] == ["accessibility"] and s["accessibility"] is True and len(asked) == 1


def test_a_restart_is_remembered_until_it_worked_or_the_grant_is_gone(tmp_path):
    from auren.mac import permissions
    live = {"microphone": "granted", "accessibility": True, "screen": False}
    g, _ = _grants(tmp_path, live, {"screen": True})
    g.mark(["screen"])
    assert g.tried() == ["screen"] and permissions.restarted(g.path)["at"] > 0
    g.check()
    assert g.tried() == ["screen"]              # still waiting after the restart: the Restart button's case
    g._ask = lambda: {"screen": False}          # turned off in System Settings: a later Allow is a new grant
    g.forget_fresh()
    g.check()
    assert g.tried() == [] and not g.path.exists()
    g.mark(["screen", "keys"])
    live["screen"] = True                       # the restart turned it on
    g.check()
    assert g.tried() == ["keys"]
    g.done("keys")
    assert g.tried() == []


def test_the_setup_window_says_what_turns_on_by_itself():
    from auren.mac import permissions
    assert permissions.turning_on([]) == ""
    assert permissions.turning_on(["screen"]) == "Turning on Screen Recording…"
    assert permissions.turning_on(["microphone", "screen", "screen"]) == "Turning on the microphone and Screen Recording…"


def test_the_permissions_child_prints_one_json_line():
    src = (ROOT / "src/auren/mac/permissions.py").read_text()
    assert 'if __name__ == "__main__":' in src and '"-m", "auren.mac.permissions"' in src


def test_setup_says_turning_on_while_the_daemon_restarts_for_a_grant(monkeypatch):
    import time

    from auren.mac import setup
    s = _setup_check(monkeypatch, {**ALLOWED, "waiting": ["screen"], "screen": True, "restarting": ["screen"]},
                     found=("codex",))
    assert s["turningOn"] == ["screen"] and s["turningText"] == "Turning on Screen Recording…" and not s["restart"]
    monkeypatch.setattr(setup, "_daemon_status", lambda: None)   # gone for the restart: on its way back
    back = _setup_check(monkeypatch, None, found=("codex",), restarted={"for": ["screen"], "at": time.time()})
    assert back["starting"] and back["turningOn"] == ["screen"] and not back["restart"]
    old = _setup_check(monkeypatch, None, found=("codex",), restarted={"for": ["screen"], "at": time.time() - 300})
    assert not old["starting"] and old["turningOn"] == []
    assert setup.needs(back) == "Turning on Screen Recording…"


def test_setup_says_turning_on_accessibility_while_the_daemon_restarts_for_it(monkeypatch):
    s = _setup_check(monkeypatch, {**ALLOWED, "talk_keys": False, "keys_waiting": True, "waiting": ["accessibility"],
                                   "restarting": ["accessibility"]}, found=("codex",))
    assert s["accessibility"] and s["turningText"] == "Turning on Accessibility…" and not s["restart"]


def test_setup_offers_restart_only_when_the_daemons_own_restart_didnt_help(monkeypatch):
    stuck = _setup_check(monkeypatch, {**ALLOWED, "screen": True, "waiting": ["screen"], "restart": ["screen"]},
                         found=("codex",))
    assert stuck["restart"] and stuck["turningOn"] == []
    from auren.mac import setup
    assert setup.needs(stuck) == "Restart Auren to turn on what you allowed."


def test_the_talk_keys_about_to_start_are_no_reason_to_restart(monkeypatch):
    waiting = _setup_check(monkeypatch, {**ALLOWED, "talk_keys": False, "keys_waiting": True}, found=("codex",))
    assert waiting["keysTurningOn"] and not waiting["restart"]
    restarting = _setup_check(monkeypatch, {**ALLOWED, "talk_keys": False, "restarting": ["keys"]}, found=("codex",))
    assert restarting["keysTurningOn"] and not restarting["restart"]
    failed = _setup_check(monkeypatch, {**ALLOWED, "talk_keys": False}, found=("codex",))
    assert failed["restart"] and not failed["keysTurningOn"]


def test_setup_finds_a_brain_on_the_launch_agents_path(monkeypatch):
    from auren import config, settings
    from auren.mac import setup
    monkeypatch.setattr(config, "secret", lambda name: "k")
    monkeypatch.setattr(settings, "current", lambda: {"brain": "auto"})
    monkeypatch.setattr(setup, "_brain_path", lambda: "/Users/a/.local/bin:/usr/bin")
    seen = []

    def which(name, path=None):
        seen.append(path)
        return "/Users/a/.local/bin/claude" if name == "claude" and path and ".local/bin" in path else None
    s = setup.check(ALLOWED, which=which, restarted={})
    assert s["claude"] and s["brainReady"] and seen == ["/Users/a/.local/bin:/usr/bin"] * 2


def test_the_launch_agents_path_has_where_claude_installs_itself(monkeypatch, tmp_path):
    """Claude Code's installer puts it in ~/.local/bin (or ~/.claude/local): found there once installed later."""
    monkeypatch.setattr(service, "DATA_HOME", tmp_path / "share")
    monkeypatch.setattr(service, "STATE_DIR", tmp_path / "state")
    path = service.plist("daemon", "/usr/bin")["EnvironmentVariables"]["PATH"].split(":")
    assert str(Path.home() / ".local/bin") in path and str(Path.home() / ".claude/local") in path


def test_setup_needs_says_what_is_missing_in_a_line():
    from auren.mac import setup
    base = {"running": True, "microphone": "granted", "accessibility": True, "key": True, "brainReady": True}
    assert setup.needs(base) == ""
    assert setup.needs({**base, "running": False}) == "Auren isn't running."
    assert setup.needs({**base, "key": False}) == "Needs a speech-to-text key."
    assert setup.needs({**base, "microphone": "not asked", "brainReady": False}) == "Needs the microphone and a brain."


def test_only_the_launch_agents_daemon_restarts_itself(monkeypatch):
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.auren.daemon")
    assert service.managed("daemon") and not service.managed("mascot")
    monkeypatch.delenv("XPC_SERVICE_NAME")
    assert not service.managed("daemon")


def test_talk_keys_are_valid_only_when_each_one_can_be_listened_for():
    assert hotkey.valid({"talk": "ctrl+alt+a", "talk-fresh": "ctrl+alt+shift+a"})
    assert not hotkey.valid({"talk": "ctrl+nothing", "talk-fresh": "ctrl+alt+shift+a"})


class _RestartingDaemon:
    """The parts of Daemon that restart it for a permission, with Auren idle or not as a test says."""
    from auren.daemon import Daemon
    _grants = Daemon._grants
    _plan_restart = Daemon._plan_restart
    _restart_when_idle = Daemon._restart_when_idle
    settings = {"keys.talk": "ctrl+alt+a", "keys.talk_fresh": "ctrl+alt+shift+a"}
    _hotkeys = None

    def __init__(self, grants, quiet):
        self._grants_now, self.quiets, self.stopped = grants, iter(quiet), False
        self.loop = self

    def _quiet(self):
        return next(self.quiets, True)

    def stop(self):                      # loop.stop()
        self.stopped = True


def _restart(d, waiting):
    import asyncio

    async def go():
        out = d._plan_restart(waiting)
        task = getattr(d, "_restart_task", None)
        if task:
            await asyncio.wait_for(task, 5)
        return out
    return asyncio.run(go())


def test_the_daemon_restarts_once_it_is_idle_for_a_new_grant(monkeypatch, tmp_path):
    from auren import daemon
    monkeypatch.setattr(service, "managed", lambda name: True)
    monkeypatch.setattr(daemon.Daemon._restart_when_idle, "__defaults__", (0.0, 3))
    g, _ = _grants(tmp_path, {}, {})
    d = _RestartingDaemon(g, quiet=[True, False, True, True, True])     # busy once: counts from zero again
    restarting, stuck = _restart(d, ["screen"])
    assert restarting == ["screen"] and stuck == []
    assert d.stopped and d.exit_code == daemon.RESTART_EXIT and g.tried() == ["screen"]
    assert next(d.quiets, "none") == "none"                               # all five checks were needed


def test_the_keys_waiter_restarts_for_accessibility_the_daemon_still_sees_as_off(monkeypatch, tmp_path):
    """After a reset, the daemon can keep seeing Accessibility as off once it's allowed again (in macOS's own
    prompt, with the setup window closed): the waiter asks a new process every 10 s and restarts for it,
    also when it restarted for an earlier grant that was taken away since."""
    import asyncio

    from auren import daemon
    from auren.mac import permissions
    monkeypatch.setattr(permissions, "accessibility", lambda: False)
    live = {"microphone": "granted", "accessibility": False, "screen": True}
    answers = iter([{"accessibility": False}, {"accessibility": True}])
    g, asked = _grants(tmp_path, live, {})
    g._ask = lambda: (asked.append(1), next(answers))[1]
    g.mark(["accessibility"])                  # the restart for the first grant
    planned = []

    class D:
        _keys_once_allowed = daemon.Daemon._keys_once_allowed
        _hotkeys = None

        def _grants(self):
            return g

        def _plan_restart(self, waiting):
            planned.append((list(waiting), g.tried()))
            self._hotkeys = "restarting"       # stands in for the restart: the waiter is done

    async def go():
        await asyncio.wait_for(D()._keys_once_allowed(every=0.0), 5)
    asyncio.run(go())
    # the 5th round: taken away (the old restart is forgotten); the 10th: allowed again, a new grant
    assert len(asked) == 2 and planned == [(["accessibility"], [])]


def test_a_restart_that_didnt_help_is_not_tried_again(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "managed", lambda name: True)
    g, _ = _grants(tmp_path, {}, {})
    g.mark(["screen"])
    d = _RestartingDaemon(g, quiet=[])
    restarting, stuck = _restart(d, ["screen"])
    assert restarting == [] and stuck == ["screen"] and not d.stopped and not getattr(d, "_restart_task", None)


def test_a_daemon_outside_its_launch_agent_leaves_the_restart_to_the_user(monkeypatch, tmp_path):
    monkeypatch.setattr(service, "managed", lambda name: False)
    g, _ = _grants(tmp_path, {}, {})
    d = _RestartingDaemon(g, quiet=[])
    assert _restart(d, ["microphone"]) == ([], ["microphone"]) and not d.stopped and g.tried() == []


def test_talk_keys_that_failed_after_accessibility_get_one_restart(monkeypatch, tmp_path):
    from auren import daemon
    monkeypatch.setattr(service, "managed", lambda name: True)
    monkeypatch.setattr(daemon.Daemon._restart_when_idle, "__defaults__", (0.0, 1))
    g, _ = _grants(tmp_path, {}, {})
    d = _RestartingDaemon(g, quiet=[])
    d._keys_failed = True
    assert _restart(d, [])[0] == ["keys"] and d.stopped and g.tried() == ["keys"]
    d2 = _RestartingDaemon(g, quiet=[])
    d2._keys_failed = True
    assert _restart(d2, []) == ([], ["keys"]) and not d2.stopped


def test_the_daemon_exits_with_the_restart_code_for_launchd():
    src = (ROOT / "src/auren/daemon.py").read_text()
    assert "return d.exit_code" in src and "RESTART_EXIT = 75" in src
    assert service.plist("daemon")["KeepAlive"] == {"SuccessfulExit": False}


def test_the_talk_keys_waiter_wakes_as_soon_as_accessibility_is_seen(monkeypatch):
    import asyncio

    from auren.daemon import Daemon
    from auren.mac import permissions
    monkeypatch.setattr(permissions, "accessibility", lambda: True)

    class D:
        _hotkeys = None
    d = D()
    d._listen_for_keys = lambda: setattr(d, "_hotkeys", "tap")

    async def go():
        task = asyncio.create_task(Daemon._keys_once_allowed(d, every=30))
        await asyncio.sleep(0.05)
        d._keys_wake.set()                       # the setup window's check saw it allowed
        await asyncio.wait_for(task, 2)
    asyncio.run(go())
    assert d._hotkeys == "tap"


def test_talk_keys_that_keep_failing_ask_for_a_restart(monkeypatch):
    import asyncio

    from auren.daemon import Daemon
    from auren.mac import permissions
    monkeypatch.setattr(permissions, "accessibility", lambda: True)

    class D:
        _hotkeys = None
    d = D()
    d._listen_for_keys = lambda: None            # the tap never starts

    async def go():
        task = asyncio.create_task(Daemon._keys_once_allowed(d, every=0.01))
        for _ in range(3):
            await asyncio.sleep(0.02)
            d._keys_wake.set()
        await asyncio.wait_for(task, 2)
    asyncio.run(go())
    assert d._keys_failed and d._hotkeys is None


def _doctor(monkeypatch, st, cfg=None):
    import json

    from auren import ctl, settings
    from auren.mac import doctor
    monkeypatch.setattr(ctl, "send", lambda cmd, timeout=5: json.dumps(st))
    monkeypatch.setattr(settings, "current", lambda: cfg or {"keys.talk": "ctrl+alt+a", "keys.talk_fresh": "ctrl+alt+shift+a"})
    return {c.name: c for c in doctor._permissions()}


def test_doctor_agrees_with_the_setup_window(monkeypatch):
    from auren.mac.doctor import OK, WARN
    st = {**ALLOWED, "program": "/Applications/Auren.app"}
    turning = _doctor(monkeypatch, {**st, "screen": True, "waiting": ["screen"], "restarting": ["screen"]})
    assert turning["Screen Recording"].status == OK and "restarts by itself" in turning["Screen Recording"].detail
    stuck = _doctor(monkeypatch, {**st, "screen": True, "waiting": ["screen"], "restart": ["screen"]})
    assert stuck["Screen Recording"].status == WARN and stuck["Screen Recording"].fix == "auren service restart"
    keys = _doctor(monkeypatch, {**st, "talk_keys": False, "keys_waiting": True})
    assert keys["talk keys"].status == OK
    off = _doctor(monkeypatch, {**st, "talk_keys": False})
    assert off["talk keys"].status == WARN and off["talk keys"].fix == "auren service restart"


def test_check_mark_and_done_on_many_threads_never_lose_a_marker(tmp_path):
    """The daemon's executor threads check while a restart marks: each read-modify-write goes whole."""
    import threading

    from auren.mac import permissions
    live = {"microphone": "granted", "accessibility": True, "screen": False}
    g, _ = _grants(tmp_path, live, {"screen": True})
    stop = threading.Event()

    def checking():
        while not stop.is_set():
            g.check()
            g.done("nothing")
    threads = [threading.Thread(target=checking) for _ in range(4)]
    for t in threads:
        t.start()
    try:
        for _ in range(200):
            g.mark(["screen", "keys"])
            assert set(g.tried()) == {"screen", "keys"}        # still waiting: nothing may drop them
    finally:
        stop.set()
        for t in threads:
            t.join()
    assert permissions.restarted(g.path)["for"] == ["screen", "keys"]
    assert [p.name for p in tmp_path.iterdir()] == ["restart.json"]       # no temp file left behind


def _quiet_daemon(state, listening=False, live=(), locked=False, batch=()):
    import asyncio
    from types import SimpleNamespace

    from auren.daemon import Daemon
    lock = asyncio.Lock()
    if locked:
        lock._locked = True
    return SimpleNamespace(state=state, listening=listening, _live=lambda: tuple(live), busy=lock,
                           tools=SimpleNamespace(batch=lambda sid: list(batch)), quiet=Daemon._quiet)


def test_a_failed_listen_is_quiet_enough_to_restart_but_an_open_question_isnt():
    from auren.daemon import Daemon
    assert Daemon._quiet(_quiet_daemon("idle"))
    assert Daemon._quiet(_quiet_daemon("error"))           # stays "error" until the next turn
    for busy in (_quiet_daemon("thinking"), _quiet_daemon("error", listening=True),
                 _quiet_daemon("idle", live=["turn"]), _quiet_daemon("idle", locked=True),
                 _quiet_daemon("idle", batch=["a yes/no question"])):
        assert not Daemon._quiet(busy)


def _fake_quartz(monkeypatch):
    enabled = []
    q = types.SimpleNamespace(kCGEventTapDisabledByTimeout=-2, kCGEventTapDisabledByUserInput=-1,
                              CGEventTapEnable=lambda tap, on: enabled.append(on))
    monkeypatch.setitem(sys.modules, "Quartz", q)
    return q, enabled


def test_a_tap_slowed_down_after_accessibility_was_taken_away_stays_off(monkeypatch):
    """Turned back on without Accessibility, the tap would hold up every key press (the keyboard lags):
    it stays off, and the daemon is told once."""
    from auren.mac import hotkey
    q, enabled = _fake_quartz(monkeypatch)
    lost = []
    allowed = [True]
    keys = hotkey.Hotkeys({"talk": "ctrl+alt+a"}, lambda c: None, on_lost=lost.append,
                          trusted=lambda: allowed[0], clock=lambda: 0.0)
    monkeypatch.setattr(hotkey.threading, "Thread",
                        lambda target, args, daemon: types.SimpleNamespace(start=lambda: target(*args)))
    assert keys._callback(None, q.kCGEventTapDisabledByUserInput, "e", None) == "e" and enabled == [True]
    assert keys._callback(None, q.kCGEventTapDisabledByTimeout, "e", None) == "e" and enabled == [True, True]
    allowed[0] = False
    keys._callback(None, q.kCGEventTapDisabledByTimeout, "e", None)
    keys._callback(None, q.kCGEventTapDisabledByTimeout, "e", None)
    assert enabled == [True, True] and lost == [keys] and keys.lost


def test_a_tap_macos_keeps_stopping_stays_off(monkeypatch):
    from auren.mac import hotkey
    q, enabled = _fake_quartz(monkeypatch)
    now = [0.0]
    keys = hotkey.Hotkeys({"talk": "ctrl+alt+a"}, lambda c: None, trusted=lambda: True, clock=lambda: now[0])
    for t in (0.0, 40.0, 80.0):                # now and then: turned back on each time
        now[0] = t
        keys._callback(None, q.kCGEventTapDisabledByTimeout, "e", None)
    assert enabled == [True, True, True]
    for t in (150.0, 151.0, 152.0):            # three times in half a minute: left off on the third
        now[0] = t
        keys._callback(None, q.kCGEventTapDisabledByTimeout, "e", None)
    assert enabled == [True, True, True, True, True] and keys.lost


class _KeysDaemon:
    from auren.daemon import Daemon
    _keys_lost = Daemon._keys_lost
    _drop_keys = Daemon._drop_keys
    _keys_watchdog = Daemon._keys_watchdog

    def __init__(self, keys):
        import threading
        self._hotkeys, self._keys_lock, self.calls = keys, threading.Lock(), []

    async def _keys_once_allowed(self):
        self.calls.append("wait")


def test_the_daemon_drops_lost_talk_keys_and_waits_for_accessibility_again():
    import asyncio
    stopped = []

    async def go():
        old = types.SimpleNamespace(stop=lambda: stopped.append("old"))
        new = types.SimpleNamespace(stop=lambda: stopped.append("new"))
        d = _KeysDaemon(new)
        await d._drop_keys(old)                # a late report about a tap a reload already replaced
        assert d._hotkeys is new and stopped == [] and d.calls == []
        d._keys_lost(new)
        for _ in range(200):
            if d._hotkeys is None and d.calls:
                break
            await asyncio.sleep(0.01)
        return d
    d = asyncio.run(go())
    assert stopped == ["new"] and d._hotkeys is None and d.calls == ["wait"]


def test_the_watchdog_drops_the_keys_only_when_a_new_process_agrees_accessibility_is_gone(monkeypatch):
    import asyncio

    from auren.mac import permissions
    live, fresh = [True, False, False], [{"accessibility": True}, {"accessibility": False}]
    monkeypatch.setattr(permissions, "accessibility", lambda: live.pop(0) if live else True)
    monkeypatch.setattr(permissions, "fresh", lambda: fresh.pop(0))
    stopped = []

    async def go():
        d = _KeysDaemon(types.SimpleNamespace(stop=lambda: stopped.append(1)))
        task = asyncio.create_task(d._keys_watchdog(every=0.0))
        for _ in range(200):                   # the checks run on worker threads
            if d._hotkeys is None:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        return d
    d = asyncio.run(go())
    assert stopped == [1] and d._hotkeys is None and not fresh     # a stale no alone (the first) kept them
