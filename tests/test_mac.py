"""The parts of the macOS port that run anywhere: parsing, labels, plists, the installer's lists."""

import re
import subprocess
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


def _setup_check(monkeypatch, status, found=(), brain="auto", key=True):
    from auren import config, settings
    from auren.mac import setup
    monkeypatch.setattr(config, "secret", lambda name: "k" if key else None)
    monkeypatch.setattr(settings, "current", lambda: {"brain": brain})
    return setup.check(status, which=lambda name: f"/opt/homebrew/bin/{name}" if name in found else None)


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
    off = _setup_check(monkeypatch, None, found=("codex",))
    assert not off["running"] and not off["ready"] and not off["restart"]
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


def test_restart_reloads_an_agent_whose_program_changed(monkeypatch, tmp_path):
    import plistlib
    monkeypatch.setattr(service, "AGENTS_DIR", tmp_path)
    with open(service.plist_path("daemon"), "wb") as f:
        plistlib.dump({"ProgramArguments": ["/Users/a/Applications/Auren.app/Contents/MacOS/Auren", "daemon"]}, f)
    loaded_as = ["/Users/a/.local/share/auren/venv/bin/auren"]
    calls = []
    monkeypatch.setattr(service, "loaded", lambda n: True)
    monkeypatch.setattr(service, "stop", lambda n: calls.append("stop") or True)
    monkeypatch.setattr(service, "start", lambda n: calls.append("start") or True)

    def launchctl(*args):
        calls.append(args[0])
        out = f"com.auren.daemon = {{\n\tstate = running\n\tprogram = {loaded_as[0]}\n}}\n"
        return subprocess.CompletedProcess(args, 0, out, "")
    monkeypatch.setattr(service, "_launchctl", launchctl)
    assert service.restart("daemon") and calls == ["print", "stop", "start"]    # kickstart would keep the old one
    calls.clear()
    loaded_as[0] = "/Users/a/Applications/Auren.app/Contents/MacOS/Auren"
    assert service.restart("daemon") and calls == ["print", "kickstart"]


def test_setup_offers_no_restart_for_a_key_no_restart_fixes(monkeypatch):
    from auren import config, settings
    from auren.mac import setup
    monkeypatch.setattr(config, "secret", lambda name: "k")
    monkeypatch.setattr(settings, "current", lambda: {"brain": "auto", "keys.talk": "ctrl+nothing"})
    s = setup.check({**ALLOWED, "talk_keys": False}, which=lambda name: "/bin/codex" if name == "codex" else None)
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


# --- auren selftest -------------------------------------------------------------------------------

def test_selftest_reads_an_answer_from_the_history(monkeypatch, tmp_path):
    from auren.mac import selftest
    hist = tmp_path / "history.jsonl"
    hist.write_text('{"source": "voice", "transcript": "old"}\n')
    monkeypatch.setattr(selftest, "HISTORY", hist)
    before = selftest._history_len()
    with hist.open("a") as f:
        f.write('{"source": "forget"}\nnot json\n{"source": "voice", "transcript": "what time is it", "reply": "Ten.",'
                ' "tools": [], "heard_s": 0.4, "think_s": 1.2, "brain": "codex"}\n')
    rec = selftest._wait_history(before, "voice", 1)
    assert rec["transcript"] == "what time is it"
    assert selftest._wait_history(selftest._history_len(), "voice", 0.1) is None


@pytest.mark.parametrize("rec,wanted,status", [
    (None, "", "fail"),
    ({"transcript": "x", "reply": "Sorry, my brain hit an error: no", "tools": []}, "", "fail"),
    ({"transcript": "x", "reply": "", "tools": []}, "", "fail"),
    ({"transcript": "x", "reply": "Safari and Notes.", "tools": []}, "system_state", "warn"),
    ({"transcript": "x", "reply": "Safari and Notes.", "tools": ["system_state"]}, "system_state", "ok"),
])
def test_selftest_judges_a_turn_by_its_reply_and_tools(rec, wanted, status):
    from auren.mac import selftest
    assert selftest._turn(rec, "turn", wanted)["status"] == status


def test_selftest_in_the_daemon_reports_a_broken_check_and_goes_on(monkeypatch):
    import asyncio

    from auren.mac import selftest

    def broken(*a):
        raise RuntimeError("no Quartz here")

    for name in ("_permissions", "_displays", "_mascot_window", "_screen", "_windows", "_clipboard"):
        monkeypatch.setattr(selftest, name, broken)

    async def fine(*a):
        return selftest.result("async", "ok")

    for name in ("_microphone", "_speech_service", "_voice"):
        monkeypatch.setattr(selftest, name, fine)
    rows = asyncio.run(selftest.inside(object()))
    assert len(rows) == 9
    assert [r["status"] for r in rows].count("fail") == 6
    assert all("no Quartz here" in r["detail"] for r in rows if r["status"] == "fail")


def test_selftest_writes_a_report_with_every_result_and_the_logs(monkeypatch, tmp_path):
    from auren.mac import selftest
    ok, bad = selftest.result("fine", "ok", "yes"), selftest.result("mascot window", "fail", "off every screen")
    monkeypatch.setattr(selftest, "_about", lambda: ["macOS: 27.2"])
    monkeypatch.setattr(selftest, "_doctor", lambda: [ok])
    monkeypatch.setattr(selftest, "_daemon", lambda: ([bad], {"state": "idle"}))
    monkeypatch.setattr(selftest, "_brain", lambda: ok)
    monkeypatch.setattr(selftest, "_browser", lambda: selftest.result("browser tools", "skip", "turned off"))
    monkeypatch.setattr(selftest, "_settings", lambda: ["brain = 'auto'"])
    monkeypatch.setattr(selftest, "_by_hand", lambda *a: pytest.fail("--quick asks nothing"))
    monkeypatch.setattr(selftest, "STATE_DIR", tmp_path)
    (tmp_path / "daemon.out.log").write_text("17:22 ready in 3.0s\n")
    out = tmp_path / "report.txt"
    assert selftest.main(["--quick", "--out", str(out)]) == 1
    text = out.read_text()
    assert "result: 1 failed, 0 warnings, 2 ok, 1 skipped" in text
    assert "FAIL mascot window: off every screen" in text
    assert "17:22 ready in 3.0s" in text and "brain = 'auto'" in text
    assert out.stat().st_mode & 0o077 == 0


def test_selftest_waits_for_the_words_asked_for_and_notes_the_states(monkeypatch, tmp_path):
    from auren.mac import selftest
    hist = tmp_path / "history.jsonl"
    hist.write_text('{"source": "voice", "transcript": "पर ऑप्शन क्या था?", "reply": "Which option?"}\n')
    monkeypatch.setattr(selftest, "HISTORY", hist)
    monkeypatch.setattr("auren.ctl.send", lambda line, timeout=0: '{"state": "listening"}')
    states: list[str] = []
    # Someone else's words first: they're kept only if the asked-for ones never come
    assert selftest._wait_history(0, "voice", 0.3, wanted="time", states=states)["reply"] == "Which option?"
    assert states == ["listening"]
    with hist.open("a") as f:
        f.write('{"source": "voice", "transcript": "What time is it?", "reply": "Twelve."}\n')
    assert selftest._wait_history(0, "voice", 5, wanted="time")["reply"] == "Twelve."


def test_selftest_waits_past_a_cancelled_try(monkeypatch, tmp_path):
    from auren.mac import selftest
    hist = tmp_path / "history.jsonl"
    hist.write_text('{"source": "voice", "transcript": "what did each time", "reply": "(cancelled)"}\n'
                    '{"source": "voice", "transcript": "What time is this?", "reply": "Half past three."}\n')
    monkeypatch.setattr(selftest, "HISTORY", hist)
    assert selftest._wait_history(0, "voice", 1, wanted="time")["reply"] == "Half past three."
    hist.write_text('{"source": "voice", "transcript": "what did each time", "reply": "(cancelled)"}\n')
    assert selftest._wait_history(0, "voice", 0.3, wanted="time")["reply"] == "(cancelled)"
