"""Paths, auren-ctl, the daemon lock, the voice model download and doctor's helpers."""

import socket
import threading
from pathlib import Path

import pytest

from auren import config, ctl, daemon, doctor, voice


def test_xdg_ignores_relative_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative/dir")
    assert config._xdg("XDG_CONFIG_HOME", tmp_path) == tmp_path
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    assert config._xdg("XDG_CONFIG_HOME", tmp_path) == tmp_path / "cfg"


def test_runtime_falls_back_to_a_private_tmp_folder(monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    base = config._runtime_base()
    assert base.name.startswith("auren-runtime-")
    assert base.stat().st_mode & 0o077 == 0


def test_ctl_sends_a_line_and_prints_the_reply(monkeypatch, tmp_path, capsys):
    path = tmp_path / "s.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(path))
    server.listen(1)
    got = []

    def answer():
        conn, _ = server.accept()
        with conn:
            got.append(conn.recv(1024))
            conn.sendall(b"ok\n")

    t = threading.Thread(target=answer)
    t.start()
    monkeypatch.setattr(ctl, "SOCKET", path)
    monkeypatch.setattr("sys.argv", ["auren-ctl", "voice", "off"])
    assert ctl.main() == 0
    t.join()
    server.close()
    assert got == [b"voice off\n"]
    assert capsys.readouterr().out == "ok\n"


def test_ctl_says_when_the_daemon_is_off(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ctl, "SOCKET", tmp_path / "missing.sock")
    monkeypatch.setattr("sys.argv", ["auren-ctl", "status"])
    assert ctl.main() == 1
    assert "isn't running" in capsys.readouterr().err


def test_only_one_daemon_holds_the_lock(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(daemon, "LOCK", tmp_path / "daemon.lock")
    first = daemon.take_lock()
    assert first is not None
    pid = (tmp_path / "daemon.lock").read_text()
    assert daemon.take_lock() is None
    assert (tmp_path / "daemon.lock").read_text() == pid      # the loser didn't wipe it
    first.close()
    second = daemon.take_lock()
    assert second is not None
    second.close()


@pytest.fixture
def models(monkeypatch, tmp_path):
    import supertonic.config
    import supertonic.loader

    monkeypatch.setattr(voice, "MODELS_DIR", tmp_path / "models")
    monkeypatch.setattr(supertonic.config, "get_model_cache_dir", lambda model: tmp_path / "sdk-cache")
    monkeypatch.setattr(supertonic.loader, "has_all_onnx_modules", lambda d: (d / "onnx/model.onnx").exists())
    return tmp_path / "models"


def _fake_model(folder):
    (folder / "onnx").mkdir(parents=True)
    (folder / "onnx/model.onnx").write_text("x")
    (folder / "voice_styles").mkdir()
    (folder / "voice_styles/F2.json").write_text("{}")


def test_model_dir_needs_a_complete_model(models):
    assert voice.model_dir() is None
    (models / voice.MODEL / "onnx").mkdir(parents=True)
    (models / voice.MODEL / "onnx/model.onnx").write_text("x")
    assert voice.model_dir() is None                          # no voices yet
    (models / voice.MODEL / "voice_styles").mkdir()
    (models / voice.MODEL / "voice_styles/F2.json").write_text("{}")
    assert voice.model_dir() == models / voice.MODEL


def test_download_falls_back_to_the_archive(models, monkeypatch):
    import huggingface_hub

    tried = []

    def snapshot(repo_id, revision, local_dir, allow_patterns):
        tried.append(repo_id)
        if repo_id != voice.ARCHIVE_REPO:
            raise OSError("gone")
        _fake_model(Path(local_dir))

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    assert voice.download_model(log=lambda _: None) == models / voice.MODEL
    assert tried[-1] == voice.ARCHIVE_REPO and len(tried) == 2
    assert not (models / f".{voice.MODEL}.tmp").exists()


def test_download_reports_when_every_source_fails(models, monkeypatch):
    import huggingface_hub

    def snapshot(**kw):
        raise OSError("offline")

    monkeypatch.setattr(huggingface_hub, "snapshot_download", snapshot)
    with pytest.raises(voice.VoiceModelMissing, match="offline"):
        voice.download_model(log=lambda _: None)


def test_voice_refuses_to_start_without_a_model(models):
    with pytest.raises(voice.VoiceModelMissing):
        voice.Voice()


@pytest.mark.parametrize("text, want", [
    ("0.56.2", (0, 56, 2)), ("v0.55.0", (0, 55, 0)), ("0.56.0-dirty", (0, 56, 0)), ("", ()),
])
def test_doctor_reads_versions(text, want):
    assert doctor._version(text) == want


def test_doctor_notices_commands_systemd_cant_see(monkeypatch):
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd, path=None: None if path else f"/opt/{cmd}")
    c = doctor._command("Brain", "claude", "the brain", svc_path="/usr/bin")
    assert c.status == doctor.FAIL and "systemd" in c.detail
    assert doctor._command("Brain", "claude", "the brain", svc_path=None).status == doctor.OK
    monkeypatch.setattr(doctor.shutil, "which", lambda cmd, path=None: None)
    assert doctor._command("Optional", "herdr", "agents", svc_path=None, required=False).status == doctor.WARN


# --- the installer's browser question ---------------------------------------------------------------

import re  # noqa: E402
import subprocess  # noqa: E402

INSTALL = Path(__file__).parents[1] / "install.sh"


def _browsers_block() -> str:
    text = INSTALL.read_text()
    return text[text.index("# >>> browsers"):text.index("# <<< browsers")]


def test_the_installers_browser_list_matches_auren_s():
    from auren.hands import browsers
    rows = re.findall(r'^  "([^"]+)"$', _browsers_block(), re.M)
    table = [tuple(r.split("|")) for r in rows]
    assert table == [(k.key, k.name, " ".join(k.desktop_ids), " ".join(k.commands)) for k in browsers.KINDS]


def _pick(tmp_path, commands: dict[str, str]) -> tuple[str, str, str]:
    """Run install.sh's pick_browser with only these commands on PATH (name -> what it prints)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for name, out in commands.items():
        (bin_dir / name).write_text(f"#!/bin/sh\necho {out}\n")
        (bin_dir / name).chmod(0o755)
    script = _browsers_block() + '\npick_browser\nprintf "%s|%s|%s" "$browser_key" "$browser_name" "$browser_is_default"\n'
    out = subprocess.run(["/usr/bin/bash", "-c", script], capture_output=True, text=True,
                         env={"PATH": str(bin_dir)}, check=True).stdout
    return tuple(out.split("|"))


def test_the_installer_names_the_browser_it_would_set_up(tmp_path):
    # Omarchy: xdg-settings names Omarchy's launcher, omarchy-cmd-default-browser the real one
    assert _pick(tmp_path, {"omarchy-cmd-default-browser": "helium.desktop", "xdg-settings": "omarchy-browser.desktop",
                            "helium-browser": "", "chromium": ""}) == ("helium", "Helium", "yes")


def test_the_installer_falls_back_to_an_installed_chromium_browser(tmp_path):
    assert _pick(tmp_path, {"xdg-settings": "firefox.desktop", "brave": ""}) == ("brave", "Brave", "")


def test_the_installer_finds_no_browser_without_a_chromium_one(tmp_path):
    assert _pick(tmp_path, {"xdg-settings": "firefox.desktop", "firefox": ""}) == ("", "", "")


def _keys(tmp_path, script: str, bindings: str | None = None) -> subprocess.CompletedProcess:
    """Run install.sh's talk-key helpers against a bindings.lua in a temporary config folder."""
    text = INSTALL.read_text()
    block = text[text.index("# >>> keys"):text.index("# <<< keys")]
    hypr = tmp_path / "config/hypr"
    hypr.mkdir(parents=True, exist_ok=True)
    if bindings is not None:
        (hypr / "bindings.lua").write_text(bindings)
    env = {"PATH": "/usr/bin", "config": str(tmp_path / "config"), "share": "/opt/auren"}
    head = 'config="$config" share="$share"\n'
    return subprocess.run(["/usr/bin/bash", "-c", head + block + script], capture_output=True, text=True, env=env)


def test_the_installer_adds_the_talk_keys_after_a_backup(tmp_path):
    original = '-- my keys\no.bind("SUPER + T", "Terminal", { omarchy = "terminal" })\n'
    assert _keys(tmp_path, "keys_bound", original).returncode == 1
    assert _keys(tmp_path, "add_keys super").returncode == 0
    hypr = tmp_path / "config/hypr"
    [backup] = hypr.glob("bindings.lua.bak.*")
    assert backup.read_text() == original
    added = (hypr / "bindings.lua").read_text()
    assert added.startswith(original)
    assert 'hl.unbind("SUPER + A")' in added
    assert 'o.bind("SUPER + A", "Auren: talk (press again to send)", "/opt/auren/bin/auren-ctl talk")' in added
    assert 'o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "/opt/auren/bin/auren-ctl talk-fresh")' in added
    assert _keys(tmp_path, "keys_bound").returncode == 0      # so a second run doesn't add them again


def test_the_spare_talk_keys_leave_omarchy_s_alone(tmp_path):
    out = _keys(tmp_path, "keys_lines alt").stdout
    assert "unbind" not in out
    assert '"SUPER + ALT + A"' in out and '"SUPER + CTRL + ALT + A"' in out


@pytest.mark.parametrize(("bindings", "own"), [
    ('o.bind("SUPER + A", "Notes", "obsidian")\n', True),
    ('  o.bind("SUPER + SHIFT + A", "Chat", "chat")\n', True),
    ('-- o.bind("SUPER + A", "Notes", "obsidian")\n', False),         # commented out
    ('o.bind("SUPER + ALT + A", "Notes", "obsidian")\n', False),
])
def test_the_installer_suggests_the_spare_keys_when_super_a_is_yours(tmp_path, bindings, own):
    assert (_keys(tmp_path, "own_super_a", bindings).returncode == 0) == own
