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
