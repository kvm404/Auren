"""Paths, auren-ctl, the daemon lock, the voice model download and doctor's helpers."""

import contextlib
import os
import socket
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

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


class _Hub:
    """A fake ptts over a fake Hugging Face cache: online it downloads, offline it only finds what's cached."""

    def __init__(self):
        self.cached, self.reachable, self.offline_seen = set(), True, []

    def _fetch(self, name):
        offline = os.environ.get("HF_HUB_OFFLINE") == "1"
        self.offline_seen.append(offline)
        if name not in self.cached:
            if offline:
                raise LookupError(f"{name} is not in the cache")
            if not self.reachable:
                raise OSError("offline")
            self.cached.add(name)

    def module(self):
        hub = self

        class TTS:
            sample_rate = 24000

            def __init__(self, lang):
                hub._fetch("model")

            @contextlib.contextmanager
            def stream(self, text, voice):
                hub._fetch(voice)
                yield iter([])

        return SimpleNamespace(TTS=TTS, DEFAULT_MODEL="hf://gradium/phonon-31m-en@abc")


@pytest.fixture
def hub(monkeypatch):
    hub = _Hub()
    monkeypatch.setitem(sys.modules, "ptts", hub.module())
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    return hub


def test_missing_lists_what_isnt_cached_without_going_online(hub):
    assert voice.missing() == ["model", *voice.VOICES]
    hub.cached.update({"model", "Everly", "Hugh"})
    assert voice.missing() == ["Juniper", "Trevor"]
    hub.cached.update(voice.VOICES)
    assert voice.missing() == []
    assert all(hub.offline_seen) and "HF_HUB_OFFLINE" not in os.environ


def test_download_fetches_the_model_and_every_voice(hub):
    assert voice.download_model(log=lambda _: None) == "Phonon 31m with Everly, Juniper, Hugh, Trevor"
    assert hub.cached == {"model", *voice.VOICES} and not any(hub.offline_seen)
    assert voice.missing() == []


def test_download_reports_when_the_hub_is_unreachable(hub):
    hub.reachable = False
    with pytest.raises(voice.VoiceModelMissing, match="offline"):
        voice.download_model(log=lambda _: None)


def test_voice_refuses_to_start_without_a_model_and_never_downloads(hub):
    with pytest.raises(voice.VoiceModelMissing, match="auren voice-model"):
        voice.Voice()
    assert hub.cached == set() and all(hub.offline_seen)


def test_voice_starts_from_the_cache(hub):
    hub.cached.update({"model", *voice.VOICES})
    assert voice.Voice("Juniper").name == "Juniper"
    assert all(hub.offline_seen) and "HF_HUB_OFFLINE" not in os.environ


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


# --- the service units and the package ---------------------------------------------------------------

ROOT = Path(__file__).parents[1]


def test_units_wait_for_the_display_instead_of_skipping():
    for unit in ("auren.service", "auren-mascot.service"):
        text = (ROOT / "systemd" / unit).read_text()
        assert "\nConditionEnvironment" not in text          # checked once: a late display would keep it off
        assert "RestartPreventExitStatus=78" in text and "@AUREN@" in text


def test_install_sh_and_the_pkgbuild_fill_in_every_placeholder():
    import re
    holes = set()
    for unit in ("auren.service", "auren-mascot.service"):
        holes |= set(re.findall(r"@[A-Z]+@", (ROOT / "systemd" / unit).read_text()))
    install = (ROOT / "install.sh").read_text()
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    for hole in holes:
        assert hole in install, hole
        assert hole in pkgbuild, hole


def test_the_pkgbuild_matches_the_project_version():
    import tomllib
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    pkgbuild = (ROOT / "packaging/arch/PKGBUILD").read_text()
    assert f"pkgver={version}\n" in pkgbuild



def test_install_sh_gives_its_services_the_shell_s_hugging_face_cache(tmp_path):
    """The voice is downloaded with the shell's variables: the units must look in the same cache."""
    import subprocess
    script = (ROOT / "install.sh").read_text()
    block = script[script.index('env=""\n'):script.index("for unit in auren.service")]    # just the env lines
    home = str(tmp_path)

    def env_for(**extra):
        run = subprocess.run(["bash", "-c", f'config="$HOME/.config"; data="$HOME/.local/share"\n{block}printf "$env"'],
                             env={"HOME": home, "PATH": os.environ["PATH"], **extra}, capture_output=True, text=True, check=True)
        return run.stdout

    assert env_for() == ""
    assert env_for(XDG_CACHE_HOME=home + "/.cache") == ""
    assert env_for(XDG_CACHE_HOME="/big/cache", HF_HOME="/big/hf", HF_HUB_CACHE="/big/hub").splitlines() == [
        "Environment=XDG_CACHE_HOME=/big/cache", "Environment=HF_HOME=/big/hf", "Environment=HF_HUB_CACHE=/big/hub"]
    assert env_for(HF_HOME="relative") == ""
