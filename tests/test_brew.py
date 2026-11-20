"""The Homebrew formula's parts that run anywhere: the auren and auren-ctl wrappers (POSIX sh), the
formula's placeholders and version, and install-macos.sh's --brew. Everything runs in throwaway folders,
with HOME and every XDG folder pointed at them."""

import json
import os
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from auren import config

ROOT = Path(__file__).parents[1]
FORMULA = ROOT / "packaging/homebrew/auren.rb"
WRAPPER = ROOT / "packaging/homebrew/wrapper.sh"
INSTALL = ROOT / "install-macos.sh"
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def _env(home: Path, **more) -> dict:
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}
    return {**env, **more}                      # no XDG_* from this session: only what a test sets


@pytest.fixture
def wrapped(tmp_path):
    """The wrappers as the formula writes them, for version 0.2.0, with a fake libexec and home."""
    libexec = tmp_path / "libexec"
    libexec.mkdir()
    (libexec / "install-macos.sh").write_text('#!/usr/bin/env bash\necho "installer $*"\n')
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    text = WRAPPER.read_text()
    for cmd in ("auren", "auren-ctl"):
        out = (text.replace("@COMMAND@", cmd).replace("@VERSION@", "0.2.0").replace("@LIBEXEC@", str(libexec))
               .replace("@BREW_BIN@", str(tmp_path / "homebrew/bin")))
        (bin_ / cmd).write_text(out)
        (bin_ / cmd).chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()

    def venv(share: Path, version: str | None = None, mode: str = "brew") -> None:
        (share / "venv/bin").mkdir(parents=True)
        for cmd in ("auren", "auren-ctl"):
            (share / "venv/bin" / cmd).write_text(f'#!/bin/sh\necho "{cmd} from the venv: $*"\n')
            (share / "venv/bin" / cmd).chmod(0o755)
        if version:
            (share / "install.json").write_text(json.dumps({"version": version, "mode": mode}))

    def run(cmd, *args, **env):
        return subprocess.run(["sh", str(bin_ / cmd), *args], env=_env(home, **env), capture_output=True, text=True)

    return run, venv, home


def test_before_setup_the_wrapper_says_to_run_setup(wrapped):
    run, _, _ = wrapped
    for cmd in ("auren", "auren-ctl"):
        r = run(cmd, "status")
        assert r.returncode == 1 and r.stderr == "Auren isn't set up yet: run auren setup\n"


def test_setup_runs_the_installer_with_brew(wrapped):
    run, venv, home = wrapped
    r = run("auren", "setup", "--no-browser")
    assert r.returncode == 0 and r.stdout == "installer --brew --no-browser\n"
    venv(home / ".local/share/auren", "0.2.0")
    assert run("auren", "setup").stdout == "installer --brew\n"        # also once it's set up
    assert run("auren-ctl", "setup").stdout == "auren-ctl from the venv: setup\n"


def test_once_set_up_the_wrapper_runs_the_venv(wrapped):
    run, venv, home = wrapped
    venv(home / ".local/share/auren", "0.2.0")
    r = run("auren", "update", "check", "--json")
    assert r.returncode == 0 and r.stdout == "auren from the venv: update check --json\n" and r.stderr == ""
    assert run("auren-ctl", "status").stdout == "auren-ctl from the venv: status\n"


def test_after_brew_upgrade_the_old_venv_runs_and_says_to_finish(wrapped):
    run, venv, home = wrapped
    venv(home / ".local/share/auren", "0.1.0")
    r = run("auren", "doctor")
    assert r.returncode == 0 and r.stdout == "auren from the venv: doctor\n"
    assert r.stderr == "Homebrew updated Auren to 0.2.0: run auren setup to finish\n"


def test_a_venv_from_a_clone_isnt_told_about_homebrew(wrapped):
    run, venv, home = wrapped
    venv(home / ".local/share/auren", "0.1.0", mode="install")
    assert run("auren", "doctor").stderr == ""


def test_the_wrapper_follows_xdg_data_home_and_ignores_a_relative_one(wrapped, tmp_path):
    run, venv, home = wrapped
    venv(tmp_path / "data/auren", "0.2.0")
    assert run("auren", "x", XDG_DATA_HOME=str(tmp_path / "data")).returncode == 0
    assert run("auren", "x").returncode == 1                       # not in ~/.local/share
    assert run("auren", "x", XDG_DATA_HOME="relative").returncode == 1


def test_the_formula_fills_in_every_placeholder_of_the_wrapper():
    holes = set(re.findall(r"@[A-Z_]+@", WRAPPER.read_text()))
    assert holes == {"@COMMAND@", "@VERSION@", "@LIBEXEC@", "@BREW_BIN@"}
    text = FORMULA.read_text()
    for hole in holes:
        assert f'gsub("{hole}"' in text, hole


def test_the_formula_matches_the_project_version():
    text = FORMULA.read_text()
    assert f'url "https://github.com/kvm404/Auren/archive/refs/tags/v{VERSION}.tar.gz"\n' in text
    assert re.search(r'^  sha256 "[0-9a-f]{64}"$', text, re.M)        # the workflow replaces this line


def test_the_formula_s_stanzas_come_in_homebrew_s_order():
    text = FORMULA.read_text()
    order = ["  desc ", "  homepage ", "  url ", "  sha256 ", "  license ", "  depends_on arch:",
             "  depends_on macos:", '  depends_on "uv"', "  def install", "  def caveats", "  test do"]
    at = [text.index(s) for s in order]
    assert at == sorted(at)


@pytest.mark.skipif(not shutil.which("ruby"), reason="no ruby here")
def test_the_formula_is_valid_ruby():
    subprocess.run(["ruby", "-c", str(FORMULA)], check=True, capture_output=True)


def test_the_installer_takes_brew_but_not_with_dev(tmp_path):
    def install(*args):
        return subprocess.run(["bash", str(INSTALL), *args], env=_env(tmp_path), capture_output=True, text=True)
    r = install("--brew", "--dev")
    assert r.returncode == 2 and "not both" in r.stderr
    assert install("--brew", "--nope").returncode == 2
    if os.uname().sysname != "Darwin":                  # parsed, then it stops: this isn't a Mac
        r = install("--brew", "--no-browser", "--keys=default")
        assert r.returncode == 1 and "macOS installer" in r.stdout


def test_the_installer_finds_the_brew_that_installed_it(tmp_path):
    script = INSTALL.read_text()
    block = script[script.index("  # >>> brew\n"):script.index("  # <<< brew\n")]
    prefix = tmp_path / "homebrew"
    (prefix / "bin").mkdir(parents=True)
    (prefix / "bin/brew").write_text("#!/bin/sh\n")
    (prefix / "bin/brew").chmod(0o755)

    def brew_for(source, path=os.environ["PATH"]):
        r = subprocess.run([shutil.which("bash"), "-c", f'source={source}\n{block}printf %s "$brew_bin"'],
                           env={"HOME": str(tmp_path), "PATH": path}, capture_output=True, text=True, check=True)
        return r.stdout

    assert brew_for(f"{prefix}/Cellar/auren/0.1.7/libexec") == f"{prefix}/bin/brew"
    assert brew_for("/elsewhere/auren", path="/nowhere") == "/opt/homebrew/bin/brew"


def test_a_brew_install_is_told_apart_by_install_json(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA_HOME", tmp_path)
    assert not config._brew()
    (tmp_path / "install.json").write_text('{"mode": "install"}')
    assert not config._brew()
    (tmp_path / "install.json").write_text('{"mode": "brew", "version": "0.1.7"}')
    assert config._brew()
    (tmp_path / "install.json").write_text("[]")
    assert not config._brew()


def test_every_command_gets_homebrew_s_bin_first_on_path(wrapped, tmp_path):
    run, venv, home = wrapped
    share = home / ".local/share/auren"
    venv(share, "0.2.0")
    (share / "venv/bin/auren").write_text('#!/bin/sh\necho "${PATH%%:*}"\n')
    assert run("auren", "status").stdout == f"{tmp_path / 'homebrew/bin'}\n"


def test_setup_moves_a_clone_install_s_links_to_homebrew_s_and_only_those(tmp_path):
    script = INSTALL.read_text()
    block = script[script.index("  # >>> links\n"):script.index("  # <<< links\n")]
    share, userbin, prefix = tmp_path / "share/auren", tmp_path / "bin", tmp_path / "homebrew"
    (prefix / "bin").mkdir(parents=True)
    userbin.mkdir()
    for cmd in ("auren", "auren-ctl"):
        (prefix / "bin" / cmd).write_text("#!/bin/sh\n")
        (prefix / "bin" / cmd).chmod(0o755)
    (userbin / "auren").symlink_to(share / "venv/bin/auren")        # from ./install-macos.sh
    (userbin / "auren-ctl").symlink_to("/somewhere/else/auren-ctl")   # not Auren's: left alone
    subprocess.run([shutil.which("bash"), "-c", f'share={share}; userbin={userbin}; brew_prefix={prefix}\n{block}'],
                   env=_env(tmp_path), check=True)
    assert os.readlink(userbin / "auren") == str(prefix / "bin/auren")
    assert os.readlink(userbin / "auren-ctl") == "/somewhere/else/auren-ctl"


def test_the_installer_writes_the_link_auren_app_looks_for():
    launcher = (ROOT / "src/auren/mac/app/launcher.c").read_text()
    assert '"%s/Library/Application Support/Auren/venv-python"' in launcher
    assert 'locator="$HOME/Library/Application Support/Auren/venv-python"' in INSTALL.read_text()
    assert 'ln -sfn "$share/venv/bin/python" "$locator"' in INSTALL.read_text()


@pytest.mark.skipif(not shutil.which("cc"), reason="no C compiler here")
def test_auren_app_finds_python_through_the_link_wherever_the_venv_is(tmp_path):
    """The launcher, built here (with a stand-in for macOS's _NSGetExecutablePath off a Mac), opened
    without AUREN_PYTHON: it runs the venv's python by the link's target path, not where that leads."""
    src = ROOT / "src/auren/mac/app/launcher.c"
    cmd = ["cc", "-o", str(tmp_path / "Auren"), str(src)]
    if os.uname().sysname != "Darwin":
        (tmp_path / "mach-o").mkdir()
        (tmp_path / "mach-o/dyld.h").write_text("#include <stdint.h>\nint _NSGetExecutablePath(char *, uint32_t *);\n")
        (tmp_path / "stub.c").write_text("#include <stdint.h>\nint _NSGetExecutablePath(char *b, uint32_t *n) { return -1; }\n")
        cmd += ["-I", str(tmp_path), str(tmp_path / "stub.c")]
    subprocess.run(cmd, check=True, capture_output=True)
    home = tmp_path / "home"
    venv = tmp_path / "custom-data/auren/venv/bin"
    venv.mkdir(parents=True)
    (venv / "python").write_text('#!/bin/sh\necho "$0 $*"\n')
    (venv / "python").chmod(0o755)
    (home / "Library/Application Support/Auren").mkdir(parents=True)
    (home / "Library/Application Support/Auren/venv-python").symlink_to(venv / "python")
    r = subprocess.run([str(tmp_path / "Auren")], env=_env(home), capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout == f"{venv / 'python'} -P -m auren.cli open\n"
