"""auren setup in a throwaway home: questions, talk keys, the package's links, moving off install.sh,
removing. systemctl, omarchy and hyprctl are recorded, never run."""

import json
import os
from pathlib import Path

import pytest

from auren import cli, config, setup


@pytest.fixture
def home(tmp_path, monkeypatch):
    """Every folder setup touches, under tmp_path; a package install unless a test says otherwise."""
    h = tmp_path / "home"
    cfg, data = h / ".config", h / ".local/share"
    for d in (cfg / "hypr", data / "auren", h / ".local/bin"):
        d.mkdir(parents=True)
    (cfg / "hypr/bindings.lua").write_text('-- my keys\no.bind("SUPER + T", "Terminal", { omarchy = "terminal" })\n')
    monkeypatch.setenv("HOME", str(h))
    for var in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "AUREN_UPDATE", "HF_HOME",
                "HF_HUB_CACHE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(config, "CONFIG_DIR", cfg / "auren")
    monkeypatch.setattr(config, "DATA_HOME", data / "auren")
    monkeypatch.setattr(config, "CACHE_DIR", h / ".cache/auren")
    monkeypatch.setattr(config, "STATE_DIR", h / ".local/state/auren")
    monkeypatch.setattr(config, "SECRETS", cfg / "auren/secrets.env")
    pkg = tmp_path / "usr"
    for d in ("bin", "share/auren/mascot", "share/auren/bar-plugin", "lib/systemd/user"):
        (pkg / d).mkdir(parents=True)
    monkeypatch.setattr(config, "PACKAGE_BIN", pkg / "bin")
    monkeypatch.setattr(config, "PACKAGE_SHARE", pkg / "share/auren")
    monkeypatch.setattr(config, "PACKAGE_UNITS", pkg / "lib/systemd/user")
    monkeypatch.setattr(config, "PACKAGED", True)

    keys = {}
    monkeypatch.setattr(config, "secret", lambda name: keys.get(name) or _file_secret(name))
    calls = []
    monkeypatch.setattr(setup, "_systemctl", lambda *a: calls.append(("systemctl", *a)) or (3 if a[0] == "is-active" else 0))
    monkeypatch.setattr(setup, "_run", lambda cmd, timeout=30: calls.append(tuple(cmd)) or 0)
    found = {"omarchy": "/usr/bin/omarchy", "claude": "/usr/bin/claude"}
    monkeypatch.setattr(setup.shutil, "which", lambda name, path=None: found.get(name))
    monkeypatch.setattr(setup, "voice_model", lambda ui: calls.append(("voice-model",)))
    monkeypatch.setattr(setup, "browser_ready", lambda: False)
    monkeypatch.setattr(setup, "browser_choice", lambda: (None, False))
    import auren.doctor
    monkeypatch.setattr(auren.doctor, "main", lambda: 0)

    class Home:
        pass
    e = Home()
    e.root, e.cfg, e.data, e.pkg, e.calls, e.found, e.keys = h, cfg, data / "auren", pkg, calls, found, keys
    e.bindings = cfg / "hypr/bindings.lua"
    return e


def _file_secret(name):
    try:
        for line in config.SECRETS.read_text().splitlines():
            k, _, v = line.partition("=")
            if k == name and v:
                return v
    except OSError:
        pass
    return None


def _setup(*args):
    return setup.main(list(args))


# --- talk keys -------------------------------------------------------------------------------------

def test_talk_keys_are_added_after_a_backup(home):
    original = home.bindings.read_text()
    assert not setup.keys_bound()
    setup.add_keys("super")
    [backup] = home.bindings.parent.glob("bindings.lua.bak.*")
    assert backup.read_text() == original
    added = home.bindings.read_text()
    ctl = home.data / "bin/auren-ctl"
    assert added.startswith(original)
    assert 'hl.unbind("SUPER + A")' in added
    assert f'o.bind("SUPER + A", "Auren: talk (press again to send)", "{ctl} talk")' in added
    assert f'o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "{ctl} talk-fresh")' in added
    assert setup.keys_bound()                                    # so a second run doesn't add them again


def test_the_spare_talk_keys_leave_omarchy_s_alone():
    lines = "\n".join(setup.keys_lines("alt", Path("/x/auren-ctl")))
    assert "unbind" not in lines
    assert '"SUPER + ALT + A"' in lines and '"SUPER + CTRL + ALT + A"' in lines


@pytest.mark.parametrize(("bindings", "own"), [
    ('o.bind("SUPER + A", "Notes", "obsidian")\n', True),
    ('  o.bind("SUPER + SHIFT + A", "Chat", "chat")\n', True),
    ('-- o.bind("SUPER + A", "Notes", "obsidian")\n', False),         # commented out
    ('o.bind("SUPER + ALT + A", "Notes", "obsidian")\n', False),
    ('hl.bind("SUPER+A", "obsidian")\n', True),
])
def test_the_spare_keys_are_suggested_when_super_a_is_yours(tmp_path, bindings, own):
    f = tmp_path / "bindings.lua"
    f.write_text(bindings)
    assert setup.own_super_a(f) == own


@pytest.mark.parametrize(("line", "bound"), [
    ('o.bind("SUPER + A", "Auren: talk", "/x/auren-ctl talk")\n', True),
    ('hl.bind("SUPER + ALT + A", "/x/auren-ctl talk-fresh")\n', True),
    ('bind = SUPER, A, exec, /x/auren-ctl talk\n', True),
    ('-- o.bind("SUPER + A", "Auren: talk", "/x/auren-ctl talk")\n', False),  # commented out
])
def test_it_knows_when_auren_is_bound(tmp_path, line, bound):
    (tmp_path / "bindings.lua").write_text(line)
    assert setup.keys_bound(tmp_path) == bound


def test_a_linked_bindings_file_counts(tmp_path):
    dots = tmp_path / "dots"
    dots.mkdir()
    (dots / "keys.lua").write_text('o.bind("SUPER + A", "Auren: talk", "/x/auren-ctl talk")\n')
    hypr = tmp_path / "hypr"
    hypr.mkdir()
    (hypr / "dots").symlink_to(dots)
    assert setup.keys_bound(hypr)


@pytest.mark.parametrize("kind", ["super", "alt"])
def test_removing_the_keys_gives_back_the_file_as_it_was(home, kind):
    original = home.bindings.read_text()
    setup.add_keys(kind)
    with home.bindings.open("a") as f:
        f.write('o.bind("SUPER + B", "Browser", "brave")\n')        # the user's own, added afterwards
    assert setup.remove_keys() is not None
    assert home.bindings.read_text() == original + 'o.bind("SUPER + B", "Browser", "brave")\n'
    assert setup.remove_keys() is None                           # nothing left of Auren's


# --- the API key --------------------------------------------------------------------------------------

def test_the_key_file_is_private_and_keeps_other_lines(home):
    config.SECRETS.parent.mkdir(parents=True)
    config.SECRETS.write_text("OTHER=1\nASSEMBLYAI_API_KEY=old\n")
    assert setup.secrets("new-key")
    assert config.SECRETS.read_text() == "OTHER=1\nASSEMBLYAI_API_KEY=new-key\n"
    assert config.SECRETS.stat().st_mode & 0o777 == 0o600
    assert not setup.secrets("")                                 # no key given: left as it is
    assert config.SECRETS.read_text() == "OTHER=1\nASSEMBLYAI_API_KEY=new-key\n"


# --- the package ----------------------------------------------------------------------------------------

def test_first_setup_of_the_package_links_it_into_your_home(home, capsys):
    assert _setup("--yes") == 0
    d = home.data
    assert os.readlink(d / "bin/auren-ctl") == str(home.pkg / "bin/auren-ctl")
    assert os.readlink(d / "bin/auren") == str(home.pkg / "bin/auren")
    assert os.readlink(d / "mascot") == str(home.pkg / "share/auren/mascot")
    assert os.readlink(d / "bar-plugin") == str(home.pkg / "share/auren/bar-plugin")
    assert os.readlink(home.cfg / "omarchy/plugins/kvm404.auren") == str(d / "bar-plugin")
    assert json.loads((d / "install.json").read_text())["mode"] == "package"
    assert setup.done()
    assert ("systemctl", "enable", "--now", "auren") in home.calls
    assert ("omarchy", "plugin", "enable", "kvm404.auren") in home.calls
    assert ("voice-model",) in home.calls
    out = capsys.readouterr().out
    assert "Add to" in out and "Super+A" not in home.bindings.read_text()   # --yes: keys printed, not added
    assert config.SECRETS.read_text() == "ASSEMBLYAI_API_KEY=\n" and "add your AssemblyAI key" in out


def test_flags_answer_the_questions(home):
    home.found["codex"] = "/usr/bin/codex"
    assert _setup("--yes", "--keys=alt", "--no-browser", "--brain=codex") == 0
    assert '"SUPER + ALT + A"' in home.bindings.read_text()
    from auren import settings
    assert settings.load()["brain"] == "codex"
    assert settings.load()["integrations.browser"] is False


def test_with_only_codex_it_becomes_the_brain(home):
    home.found.pop("claude")
    home.found["codex"] = "/usr/bin/codex"
    assert _setup("--yes") == 0
    from auren import settings
    assert settings.load()["brain"] == "codex"


def test_auren_alone_sets_up_once_then_shows_help(home, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["auren"])
    ran = []
    real = setup.main
    monkeypatch.setattr(setup, "main", lambda args: ran.append(args) or real(["--yes"]))
    assert cli.main() == 0
    assert ran == [[]]
    assert cli.main() == 0                                       # set up: now it's the help
    assert ran == [[]] and "auren setup" in capsys.readouterr().out


def test_an_install_sh_install_isnt_set_up_by_running_auren_alone(home, monkeypatch):
    monkeypatch.setattr(config, "PACKAGED", False)
    monkeypatch.setattr("sys.argv", ["auren"])
    monkeypatch.setattr(setup, "main", lambda args: pytest.fail("setup ran"))
    assert cli.main() == 0


def test_running_it_again_changes_nothing_twice(home):
    assert _setup("--yes", "--keys=super") == 0
    once = home.bindings.read_text()
    assert _setup("--yes", "--keys=super") == 0
    assert home.bindings.read_text() == once                    # the keys were already there


def test_custom_xdg_folders_reach_the_package_s_services(home, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", "/elsewhere/state")
    setup.xdg_drop_ins()
    drop = home.cfg / "systemd/user/auren.service.d/auren-xdg.conf"
    assert "Environment=XDG_STATE_HOME=/elsewhere/state" in drop.read_text()
    monkeypatch.delenv("XDG_STATE_HOME")
    setup.xdg_drop_ins()
    assert not drop.parent.exists()


def test_a_custom_data_folder_moves_where_the_mascot_starts(home, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "/elsewhere/data")
    setup.xdg_drop_ins()
    drop = (home.cfg / "systemd/user/auren-mascot.service.d/auren-xdg.conf").read_text()
    assert "ExecStart=\nExecStart=/usr/bin/auren with-display /usr/bin/qs -n -p /elsewhere/data/auren/mascot" in drop
    assert "ExecStart" not in (home.cfg / "systemd/user/auren.service.d/auren-xdg.conf").read_text()


def test_the_package_s_mascot_starts_from_the_path_the_bar_plugin_calls():
    from pathlib import Path
    pkgbuild = (Path(__file__).parents[1] / "packaging/arch/PKGBUILD").read_text()
    panel = (Path(__file__).parents[1] / "omarchy-plugin/Panel.qml").read_text()
    assert "s#@MASCOT@#%h/.local/share/auren/mascot#g" in pkgbuild
    assert 'root.dataDir + "/mascot", "ipc"' in panel


def _old_install(home, mode="install"):
    d, units = home.data, home.cfg / "systemd/user"
    (d / "venv/bin").mkdir(parents=True)
    (d / "venv/bin/auren-ctl").write_text("#!/bin/sh\n")
    (d / "mascot").mkdir()
    (d / "bin").mkdir()
    (d / "bin/auren-ctl").symlink_to(d / "venv/bin/auren-ctl")
    (home.root / ".local/bin/auren-ctl").symlink_to(d / "venv/bin/auren-ctl")
    (home.root / ".local/bin/mine").symlink_to("/usr/bin/true")          # not Auren's: stays
    units.mkdir(parents=True)
    for u in ("auren", "auren-mascot"):
        (units / f"{u}.service").write_text(f"[Service]\nExecStart={d}/venv/bin/auren daemon\n")
    (home.root / ".cache/auren/source").mkdir(parents=True)
    (d / "models").mkdir()
    (d / "models/keep").write_text("x")
    (d / "install.json").write_text(json.dumps({"mode": mode, "commit": "abc"}))
    setup.add_keys("alt")


def test_the_package_replaces_an_install_sh_install(home):
    _old_install(home)
    keys = home.bindings.read_text()
    assert _setup("--yes") == 0
    d, units = home.data, home.cfg / "systemd/user"
    assert not (d / "venv").exists() and not (units / "auren.service").exists()
    assert ("systemctl", "stop", "auren", "auren-mascot") in home.calls
    assert not (home.root / ".local/bin/auren-ctl").is_symlink()          # it would hide /usr/bin's
    assert (home.root / ".local/bin/mine").is_symlink()
    assert not (home.root / ".cache/auren/source").exists()
    assert (d / "models/keep").exists()                                    # the voice model stays
    assert home.bindings.read_text() == keys                               # and the talk keys
    assert os.readlink(d / "bin/auren-ctl") == str(home.pkg / "bin/auren-ctl")
    assert json.loads((d / "install.json").read_text())["mode"] == "package"


def test_saying_no_to_replacing_it_changes_nothing(home, monkeypatch):
    _old_install(home)
    monkeypatch.setattr(setup.Ui, "confirm", lambda self, q, default=True: False)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    assert _setup() == 130
    assert (home.data / "venv").exists() and (home.cfg / "systemd/user/auren.service").exists()


def test_questions_without_gum(home, monkeypatch):
    """A terminal without gum: numbered choices and plain prompts."""
    home.found["codex"] = "/usr/bin/codex"
    answers = iter(["2", "3"])                                   # keys: Super+Alt+A; brain: codex only
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "typed-key")
    ui = setup.Ui(interactive=True)
    plan = setup.ask(setup.parser().parse_args([]), ui)
    assert (plan.keys, plan.assemblyai_key, plan.brain) == ("alt", "typed-key", "codex")


def test_an_update_asks_nothing_and_leaves_the_services_to_it(home, monkeypatch):
    monkeypatch.setattr(config, "PACKAGED", False)
    monkeypatch.setenv("AUREN_UPDATE", "1")
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(setup.Ui, "choose", lambda *a, **k: pytest.fail("asked"))
    assert _setup() == 0
    assert not any(c[:2] == ("systemctl", "enable") for c in home.calls)
    assert "Auren" not in home.bindings.read_text()


def test_the_old_bar_plugin_name_is_renamed_in_place(tmp_path):
    f = tmp_path / "shell.json"
    f.write_text(json.dumps({"bar": {"right": ["clock", "krvm.auren"]}, "plugins": [{"id": "krvm.auren", "x": 1}]}))
    setup.rename_in_shell_json(f, "krvm.auren", "kvm404.auren")
    assert json.loads(f.read_text()) == {"bar": {"right": ["clock", "kvm404.auren"]},
                                         "plugins": [{"id": "kvm404.auren", "x": 1}]}


# --- removing ---------------------------------------------------------------------------------------------

def test_remove_undoes_setup_and_keeps_your_settings(home):
    original = home.bindings.read_text()
    assert _setup("--yes", "--keys=super") == 0
    config.SECRETS.write_text("ASSEMBLYAI_API_KEY=k\n")
    home.calls.clear()
    assert _setup("--remove", "--yes") == 0
    assert home.bindings.read_text() == original
    assert ("systemctl", "disable", "--now", "auren", "auren-mascot") in home.calls
    assert not (home.cfg / "omarchy/plugins/kvm404.auren").is_symlink()
    assert not (home.data / "bin").exists() and not (home.data / "install.json").exists()
    assert not setup.done()
    assert config.SECRETS.exists()


def test_purge_deletes_everything_of_auren_s(home):
    assert _setup("--yes") == 0
    assert _setup("--remove", "--purge", "--yes") == 0
    assert not config.CONFIG_DIR.exists() and not home.data.exists()


def test_purge_needs_remove(home):
    assert _setup("--purge") == 2


def test_a_failing_step_doesnt_stop_the_rest(home, monkeypatch, capsys):
    monkeypatch.setattr(setup, "bar_plugin", lambda ui: 1 / 0)
    assert _setup("--yes", "--keys=alt") == 0
    assert '"SUPER + ALT + A"' in home.bindings.read_text() and setup.done()
    assert "ZeroDivisionError" in capsys.readouterr().out


def test_a_real_plugin_folder_is_kept_aside_not_deleted(home):
    plugin = home.cfg / "omarchy/plugins" / setup.PLUGIN
    plugin.mkdir(parents=True)
    (plugin / "Panel.qml").write_text("my changes")
    assert _setup("--yes", "--no-keys") == 0
    assert plugin.is_symlink()
    kept = [p for p in plugin.parent.iterdir() if p.name.startswith(setup.PLUGIN + ".bak.")]
    assert len(kept) == 1 and (kept[0] / "Panel.qml").read_text() == "my changes"


def test_remove_leaves_an_unbind_of_your_own(home):
    own = 'hl.unbind("SUPER + A")          -- was: Select all (Omarchy 4.5; Ctrl+A still selects all)'
    home.bindings.write_text(home.bindings.read_text() + own + "\n")
    assert setup.remove_keys() is None
    assert own in home.bindings.read_text()


def test_remove_keys_keeps_a_linked_bindings_file_linked(home, tmp_path):
    real = tmp_path / "dotfiles/bindings.lua"
    real.parent.mkdir()
    real.write_text(home.bindings.read_text())
    home.bindings.unlink()
    home.bindings.symlink_to(real)
    setup.add_keys("alt")
    assert setup.remove_keys()
    assert home.bindings.is_symlink() and "Auren" not in real.read_text()


def test_the_key_file_is_never_readable_by_others(home, monkeypatch):
    modes = []
    real_fchmod = setup.os.fchmod
    monkeypatch.setattr(setup.os, "fchmod", lambda fd, mode: modes.append(mode) or real_fchmod(fd, mode))
    setup.secrets("k")
    assert modes == [0o600] and (config.SECRETS.stat().st_mode & 0o777) == 0o600


def test_paths_with_spaces_are_quoted(home, monkeypatch):
    line = setup.keys_lines("alt", setup.Path("/home/me/My Data/auren/bin/auren-ctl"))[1]
    assert "\"'/home/me/My Data/auren/bin/auren-ctl' talk\"" in line
    assert setup.BOUND.search(line)
    monkeypatch.setenv("XDG_DATA_HOME", "/home/me/My Data")
    setup.xdg_drop_ins()
    drop = (home.cfg / "systemd/user/auren-mascot.service.d/auren-xdg.conf").read_text()
    assert 'Environment="XDG_DATA_HOME=/home/me/My Data"' in drop
    assert '-p "/home/me/My Data/auren/mascot"' in drop
