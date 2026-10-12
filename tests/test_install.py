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
