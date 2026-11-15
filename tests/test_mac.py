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
