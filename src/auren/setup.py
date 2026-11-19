"""auren setup: get Auren ready for you, the part of installing that's per user.

    auren setup                        ask what's needed, then set everything up (safe to run again)
        --keys=super | --keys=alt      add these talk keys to bindings.lua without asking
        --no-keys                      don't add talk keys: print the lines instead
        --browser | --no-browser       set up the browser tools (Open Browser Use), or turn them off
        --brain=auto|claude|codex      who answers
        --yes                          ask nothing: what isn't given by a flag is left for later
    auren setup --remove [--purge]     undo it: talk keys, bar plugin, services, browser host
                                       (--purge also deletes settings, the API key, history and logs)

The Arch package installs the program (/usr); this does what a package mustn't do, in your home
folder: the talk keys, the bar plugin, the AssemblyAI key, the voice model, the browser tools, and
starting the services. Running `auren` for the first time after `yay -S auren` runs it. install.sh runs
it too, after installing the program into ~/.local/share/auren.

Every question comes first, so the rest runs on its own. In a terminal the questions use gum, as
Omarchy's own scripts do; without one (or with --yes) nothing is asked.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from . import config

PLUGIN = "kvm404.auren"
OLD_PLUGIN = "krvm.auren"             # the bar plugin's id up to 0.1.3
UNITS = ("auren", "auren-mascot")
DROP_IN = "auren-xdg.conf"            # custom XDG folders, for the package's units


class Stop(Exception):
    """The user stopped setup (Ctrl+C or Esc in a question)."""


# --- where things are ------------------------------------------------------------------------------
# Read on every call, so tests can point config's folders elsewhere.

def config_home() -> Path:
    return config.CONFIG_DIR.parent


def bindings_file() -> Path:
    return config_home() / "hypr" / "bindings.lua"


def plugins_dir() -> Path:
    return config_home() / "omarchy" / "plugins"


def user_units() -> Path:
    return config_home() / "systemd" / "user"


def user_bin() -> Path:
    return Path.home() / ".local" / "bin"


def ctl_path() -> Path:
    """What the talk keys run: the same path for both kinds of install."""
    return config.DATA_HOME / "bin" / "auren-ctl"


def info_file() -> Path:
    """What's installed (install.sh writes it, or setup for the package): auren update and the bar read it."""
    return config.DATA_HOME / "install.json"


def marker() -> Path:
    """Setup finished once, for this kind of install."""
    return config.DATA_HOME / "setup.json"


def _json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed_info() -> dict:
    return _json(info_file())


def done() -> bool:
    """Setup has finished for this kind of install (what `auren` with nothing after it checks). An
    install.sh install that the package now replaces doesn't count."""
    mode = _json(marker()).get("mode")
    return mode == ("package" if config.PACKAGED else "install")


def mark_done() -> None:
    _write_atomic(marker(), json.dumps({"mode": "package" if config.PACKAGED else "install",
                                        "version": _version(), "at": int(time.time())}))


def _version() -> str:
    try:
        return version("auren")
    except PackageNotFoundError:
        return ""


# --- the screen ------------------------------------------------------------------------------------

GREEN, YELLOW, DIM, BOLD, END = "\033[32m", "\033[33m", "\033[2m", "\033[1m", "\033[0m"


class Ui:
    """Questions and progress: gum in a terminal (Omarchy's look), plain text otherwise."""

    def __init__(self, interactive: bool):
        self.interactive = interactive
        self.gum = shutil.which("gum") if interactive else None
        self.color = sys.stdout.isatty()

    def _c(self, code: str, text: str) -> str:
        return f"{code}{text}{END}" if self.color else text

    def title(self, text: str) -> None:
        if self.gum and subprocess.run([self.gum, "style", "--border", "rounded", "--padding", "0 2",
                                        "--margin", "1 0", "--bold", text]).returncode == 0:
            return
        print("\n" + self._c(BOLD, text) + "\n")

    def step(self, text: str) -> None:
        print(self._c(BOLD, text), flush=True)

    def ok(self, text: str) -> None:
        print(self._c(GREEN, "✓ ") + text, flush=True)

    def warn(self, text: str) -> None:
        print(self._c(YELLOW, "! " + text), flush=True)

    def note(self, text: str) -> None:
        print(self._c(DIM, text), flush=True)

    def answer(self, text: str) -> None:
        """gum clears a question once it's answered: leave the answer on the screen."""
        print(self._c(DIM, "  › " + text), flush=True)

    def _gum(self, *args: str) -> tuple[int, str]:
        r = subprocess.run([self.gum, *args], stdout=subprocess.PIPE, text=True)
        return r.returncode, r.stdout.strip()

    def choose(self, header: str, options: list[tuple[str, str]], default: str) -> str:
        """options: (value, label). Returns a value."""
        if self.gum:
            labels = {v: label for v, label in options}
            code, out = self._gum("choose", "--header", header, "--label-delimiter", "\x1f",
                                  "--selected", labels[default], *(f"{label}\x1f{v}" for v, label in options))
            if code or out not in labels:
                raise Stop
            self.answer(labels[out])
            return out
        print(self._c(BOLD, header))
        for i, (_, label) in enumerate(options, 1):
            print(f"  {i}) {label}")
        n = next(i for i, (v, _) in enumerate(options, 1) if v == default)
        try:
            answer = input(f"[{n}] ").strip()
        except EOFError:
            raise Stop from None
        try:
            return options[int(answer or n) - 1][0]
        except (ValueError, IndexError):
            return default

    def confirm(self, question: str, default: bool = True) -> bool:
        if self.gum:
            code = subprocess.run([self.gum, "confirm", f"--default={'true' if default else 'false'}",
                                   question]).returncode
            if code not in (0, 1):
                raise Stop
            self.answer(f"{question} {'Yes' if code == 0 else 'No'}")
            return code == 0
        try:
            answer = input(f"{question} [{'Y/n' if default else 'y/N'}] ").strip().lower()
        except EOFError:
            raise Stop from None
        return default if not answer else answer in ("y", "yes")

    def secret(self, header: str, placeholder: str) -> str:
        if self.gum:
            code, out = self._gum("input", "--password", "--header", header, "--placeholder", placeholder,
                                  "--width", "60")
            if code:
                raise Stop
            self.answer(f"{header}: {'saved' if out.strip() else 'skipped'}")
            return out
        import getpass
        print(self._c(BOLD, header))
        try:
            return getpass.getpass(placeholder + ": ").strip()
        except EOFError:
            raise Stop from None

    def spin(self, title: str, cmd: list[str]) -> int:
        """Run a command with a spinner (gum) or with its output (plain)."""
        if self.gum:
            return subprocess.run([self.gum, "spin", "--show-error", "--title", title, "--", *cmd]).returncode
        self.step(title)
        return subprocess.run(cmd).returncode


# --- small parts -----------------------------------------------------------------------------------

def _run(cmd: list[str], timeout: float = 30) -> int:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def _systemctl(*args: str) -> int:
    return _run(["systemctl", "--user", *args], timeout=60)


def _remove(path: Path) -> None:
    """Remove a file, a link (never what it points to) or a folder."""
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _link(target: Path, link: Path) -> None:
    """link -> target. A link is replaced; a real file or folder is Auren's own inside DATA_HOME (an
    install.sh copy) and goes, anywhere else (a plugin checkout, say) it's kept as <name>.bak.<time>."""
    if link.is_symlink() and Path(os.readlink(link)) == target:
        return
    if link.is_symlink() or (link.exists() and link.is_relative_to(config.DATA_HOME)):
        _remove(link)
    elif link.exists():
        link.rename(_aside(link))
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(target)


def _aside(path: Path) -> Path:
    """A free <name>.bak.<time> next to path."""
    stamp = int(time.time())
    backup, n = path.with_name(f"{path.name}.bak.{stamp}"), 1
    while backup.exists() or backup.is_symlink():
        backup, n = path.with_name(f"{path.name}.bak.{stamp}.{n}"), n + 1
    return backup


def _write_atomic(path: Path, text: str, mode: int = 0o644) -> None:
    """Write a new file next to path, created with its mode (so a secret is never readable by others,
    not even for a moment), then put it in place. A linked path (dotfiles) is written where it points."""
    path = path.resolve() if path.is_symlink() else path
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "w") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# --- the talk keys ---------------------------------------------------------------------------------

KEYS_COMMENT = "-- Auren voice assistant: press to start talking, press again to send."


def keys_lines(kind: str, ctl: Path | None = None) -> list[str]:
    """Auren's lines for bindings.lua. Super+A frees Omarchy's own uses of the keys first: Hyprland runs
    every binding on a key, so without the unbinds both would fire."""
    ctl = ctl or ctl_path()
    if kind == "super":
        return [KEYS_COMMENT,
                'hl.unbind("SUPER + A")          -- was: Select all (Omarchy 4.5; Ctrl+A still selects all)',
                'hl.unbind("SUPER + SHIFT + A")  -- was: ChatGPT web app',
                f'o.bind("SUPER + A", "Auren: talk (press again to send)", "{_sh(ctl)} talk")',
                f'o.bind("SUPER + SHIFT + A", "Auren: talk in a fresh conversation", "{_sh(ctl)} talk-fresh")']
    return [KEYS_COMMENT,
            f'o.bind("SUPER + ALT + A", "Auren: talk (press again to send)", "{_sh(ctl)} talk")',
            f'o.bind("SUPER + CTRL + ALT + A", "Auren: talk in a fresh conversation", "{_sh(ctl)} talk-fresh")']


def _sh(path: Path) -> str:
    """A path in the binding's shell command, inside a Lua string: single quotes if it needs any."""
    text = str(path)
    if re.fullmatch(r"[\w@%+=:,./-]+", text):
        return text
    return "'" + text.replace("'", "'\\''").replace("\\", "\\\\").replace('"', '\\"') + "'"


BOUND = re.compile(r"^\s*(o\.bind|hl\.bind|bind[a-z]*\s*=).*auren-ctl'? talk", re.M)
OWN_SUPER_A = re.compile(r'^\s*(o|hl)\.bind\(\s*"SUPER\s*\+\s*(SHIFT\s*\+\s*)?A"', re.M)


def keys_bound(hypr: Path | None = None) -> bool:
    """A binding (not a comment) in some Hyprland config file already runs Auren's talk command. Linked
    files count: dotfile managers link them in."""
    hypr = hypr or bindings_file().parent
    for root, _, files in os.walk(hypr, followlinks=True):
        for name in files:
            if name.endswith((".lua", ".conf")):
                try:
                    if BOUND.search((Path(root) / name).read_text(errors="replace")):
                        return True
                except OSError:
                    continue
    return False


def own_super_a(bindings: Path | None = None) -> bool:
    """The user bound Super+A or Super+Shift+A to something of their own (Omarchy's defaults live
    elsewhere, so they don't count)."""
    try:
        return bool(OWN_SUPER_A.search((bindings or bindings_file()).read_text(errors="replace")))
    except OSError:
        return False


def _backup(path: Path) -> Path:
    """A copy the way Omarchy makes them: <file>.bak.<time>."""
    backup = _aside(path)
    shutil.copy2(path, backup)
    return backup


def add_keys(kind: str, bindings: Path | None = None) -> Path:
    """Back up bindings.lua, then add Auren's lines at the end. Returns the backup."""
    bindings = bindings or bindings_file()
    backup = _backup(bindings)
    with bindings.open("a") as f:
        f.write("\n" + "\n".join(keys_lines(kind)) + "\n")
    return backup


def remove_keys(bindings: Path | None = None) -> Path | None:
    """Take out exactly the lines add_keys adds (with any auren-ctl path), after a backup. Returns the
    backup, or None when there was nothing to take out."""
    bindings = bindings or bindings_file()
    try:
        lines = bindings.read_text().split("\n")
    except OSError:
        return None
    ours = set()
    for kind in ("super", "alt"):
        for line in keys_lines(kind, Path("@CTL@")):
            ours.add(line)
    pattern = re.compile("|".join(re.escape(x).replace("@CTL@", r"[^\"]*auren-ctl'?") for x in ours))
    # Only lines in a run that has one of Auren's binds: an unbind on its own is the user's
    keep, run = [], []

    def flush():
        if any('"Auren: ' in x for x in run):
            if keep and keep[-1] == "" and run[0] == KEYS_COMMENT:
                keep.pop()                          # the empty line add_keys put before them
            flush.dropped = True
        else:
            keep.extend(run)
        run.clear()
    flush.dropped = False
    for line in lines:
        if pattern.fullmatch(line):
            run.append(line)
            continue
        flush()
        keep.append(line)
    flush()
    if not flush.dropped:
        return None
    backup = _backup(bindings)
    _write_atomic(bindings, "\n".join(keep), mode=bindings.stat().st_mode & 0o777)
    return backup


def _key_names(kind: str) -> list[str]:
    return [m.group(1) + ": " + m.group(2) for line in keys_lines(kind)
            if (m := re.match(r'o\.bind\("([^"]*)", "Auren: ([^"]*)"', line))]


# --- the browser tools -----------------------------------------------------------------------------

def browser_ready() -> bool:
    """They work already, or were turned off: setup leaves them as they are."""
    from .browser_cli import _integration
    from .hands import browsers, obu
    st, _ = _integration()
    if st == "off":
        return True
    try:
        target = obu.choose()
    except browsers.UnknownBrowser:
        return False
    return not obu.not_set_up(target.kind, target.profile)


def browser_choice():
    """(the browser `auren browser setup` would use, whether it's the default one), or (None, False)."""
    from .hands import browsers, obu
    try:
        kind = obu.setup_choice(None)
    except browsers.UnknownBrowser:
        return None, False
    return kind, kind == browsers.default_kind()


# --- the brain -------------------------------------------------------------------------------------

def brains() -> tuple[bool, bool]:
    """(Claude Code is installed, Codex is installed)"""
    return bool(shutil.which("claude")), bool(shutil.which("codex"))


# --- what setup will do ----------------------------------------------------------------------------

@dataclass
class Plan:
    keys: str = "print"               # super | alt | print | keep
    browser: str = "later"            # yes | no | keep | later | none
    browser_kind: object = None
    assemblyai_key: str = ""          # to write to secrets.env
    brain: str = ""                   # to set (brain.mode), or "" to leave it
    migrate: dict = field(default_factory=dict)   # an install.sh install the package replaces


def updating() -> bool:
    """install.sh is running for `auren update`: nothing is asked, and the services are left to it."""
    return bool(os.environ.get("AUREN_UPDATE"))


def ask(a: argparse.Namespace, ui: Ui) -> Plan:
    """Every question, before anything changes."""
    plan = Plan()
    asking = ui.interactive and not updating()

    if config.PACKAGED:
        plan.migrate = old_install()
        if plan.migrate:
            ui.step("Auren is also installed with install.sh")
            ui.note(f"  {plan.migrate['where']}. Setup replaces it with the package's copy; your settings,\n"
                    "  API key, talk keys, voice model and history stay.")
            if asking and not ui.confirm("Replace the install.sh copy with the package?", True):
                raise Stop

    # Talk keys: never added twice, nor on an update; without a terminal (or bindings.lua) they're printed.
    bindings = bindings_file()
    if a.keys != "print" and keys_bound():
        plan.keys = "keep"
    elif not bindings.is_file():
        if a.keys not in ("ask", "print"):
            ui.warn(f"no {bindings} to add the talk keys to: they're printed at the end")
        plan.keys = "print"
    elif a.keys != "ask":
        plan.keys = a.keys
    elif asking:
        suggest = "alt" if own_super_a() else "super"
        plan.keys = ui.choose(
            "Talk keys: Auren starts listening when you press its key. This adds it to bindings.lua (backed up first).",
            [("super", "Super+A, and Super+Shift+A for a fresh conversation (replaces Omarchy's Select all and ChatGPT keys)"),
             ("alt", "Super+Alt+A and Super+Ctrl+Alt+A (keeps Omarchy's keys as they are)"),
             ("print", "Skip: print the lines and I'll add them myself")], suggest)

    # Browser tools: kept as they are when they already work (or were turned off), and never asked on an update.
    if a.browser == "no":
        plan.browser = "no"
    elif updating() or (a.browser == "ask" and browser_ready()):
        plan.browser = "keep"
    else:
        kind, is_default = browser_choice()
        plan.browser_kind = kind
        if kind is None:
            if a.browser == "yes":
                ui.warn("no Chromium browser is installed (Chrome, Chromium, Brave, Helium, Edge or Vivaldi): "
                        "skipping the browser tools")
            plan.browser = "none"
        elif a.browser == "yes":
            plan.browser = "yes"
        elif asking:
            where = (f"{kind.name}, your default browser" if is_default else
                     f"{kind.name} (your default browser isn't a Chromium one, which they need)")
            ui.step("Browser tools (optional)")
            ui.note(f"  Auren can work in your own logged-in browser: read pages, click through sites, fill forms.\n"
                    f"  This sets them up in {where}: it downloads Open Browser Use and opens its\n"
                    "  extension's page there, where you click Add.")
            plan.browser = "yes" if ui.confirm(f"Set up the browser tools in {kind.name}?", True) else "no"

    # The AssemblyAI key: asked only when Auren has none (environment, secrets.env or the keyring).
    if asking and not config.secret("ASSEMBLYAI_API_KEY"):
        ui.step("Speech to text")
        ui.note("  Auren hears you through AssemblyAI. Paste your API key (assemblyai.com, Dashboard → API Keys),\n"
                f"  or leave it empty and add it to {config.SECRETS} later.")
        plan.assemblyai_key = ui.secret("AssemblyAI API key", "paste the key, or press Enter to skip").strip()

    # The brain: asked only when both are installed and it's the first setup.
    claude, codex = brains()
    if a.brain:
        plan.brain = a.brain
    elif not done() and not updating():
        if codex and not claude:
            plan.brain = "codex"
        elif claude and codex and asking:
            plan.brain = ui.choose("Brain: who answers?", [
                ("auto", "Claude Code, and Codex when Claude hits its usage limit"),
                ("claude", "Claude Code only"), ("codex", "Codex only")], "auto")
    return plan


# --- the package's files in your home folder -----------------------------------------------------

def old_install() -> dict:
    """An install.sh install that a package install replaces: what's there, or {}."""
    data = config.DATA_HOME
    units = [user_units() / f"{u}.service" for u in UNITS]
    ours = [u for u in units if u.is_file() and "auren" in u.read_text(errors="replace")]
    venv = data / "venv"
    if not (venv.exists() or venv.is_symlink() or ours):
        return {}
    mode = installed_info().get("mode") or "install"
    return {"mode": mode, "units": ours,
            "where": f"In {data}" + (f" (a --dev install of {installed_info().get('source')})" if mode == "dev" else "")}


def migrate(found: dict, ui: Ui) -> None:
    """Take out the install.sh install: its services first (they'd override the package's, and run the old
    program), then its program files. Settings, keys, models, history and the browser host stay."""
    ui.step("Replacing the install.sh copy")
    _systemctl("stop", *UNITS)
    _systemctl("disable", *UNITS)
    for unit in found.get("units", []):
        unit.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    data = config.DATA_HOME
    for name in ("venv", "mascot", "bar-plugin", "bin"):
        _remove(data / name)
    for name in ("auren", "auren-ctl"):          # ~/.local/bin comes first in PATH: they'd hide /usr/bin's
        link = user_bin() / name
        if link.is_symlink() and (not link.exists() or Path(os.readlink(link)).is_relative_to(data)):
            link.unlink()
    _remove(config.CACHE_DIR / "source")         # the self-updater's clone
    ui.ok("install.sh copy removed (settings, keys and voice model kept)")
    found["restart_shell"] = True                # the loaded bar plugin still calls the old program


def link_package() -> None:
    """DATA_HOME's program paths, pointing into the package: what the talk keys, the mascot and the bar
    plugin use, whichever way Auren was installed."""
    data = config.DATA_HOME
    _link(config.PACKAGE_BIN / "auren", data / "bin" / "auren")
    _link(config.PACKAGE_BIN / "auren-ctl", data / "bin" / "auren-ctl")
    _link(config.PACKAGE_SHARE / "mascot", data / "mascot")
    _link(config.PACKAGE_SHARE / "bar-plugin", data / "bar-plugin")


MASCOT_START = f"{config.PACKAGE_BIN}/auren with-display /usr/bin/qs -n -p {{}}"


def _unit_quote(text: str) -> str:
    """One word in a systemd unit line, quoted if it has spaces; % is a specifier there."""
    text = text.replace("%", "%%")
    if not re.search(r'[\s"\'\\]', text):
        return text
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def xdg_drop_ins() -> None:
    """Custom XDG folders and a Hugging Face cache set in the shell (HF_HOME, HF_HUB_CACHE: where the voice
    was downloaded) reach the package's services through a drop-in (install.sh writes them into its own
    units instead): the user manager may not have them. A custom XDG_DATA_HOME also moves where the
    mascot starts from, since the bar plugin calls it by that path (qs matches the path as given)."""
    env, data = [], None
    home = Path.home()
    for var, default in (("XDG_CONFIG_HOME", home / ".config"), ("XDG_DATA_HOME", home / ".local/share"),
                         ("XDG_STATE_HOME", home / ".local/state"), ("XDG_CACHE_HOME", home / ".cache")):
        value = os.environ.get(var, "")
        if value.startswith("/") and Path(value) != default:
            env.append(f"Environment={_unit_quote(f'{var}={value}')}")
            if var == "XDG_DATA_HOME":
                data = value
    for var in ("HF_HOME", "HF_HUB_CACHE"):        # else the daemon downloads the voice again, elsewhere
        value = os.environ.get(var, "")
        if value.startswith("/"):
            env.append(f"Environment={_unit_quote(f'{var}={value}')}")
    for unit in UNITS:
        path = user_units() / f"{unit}.service.d" / DROP_IN
        lines = list(env)
        if unit == "auren-mascot" and data:
            lines += ["ExecStart=", "ExecStart=" + MASCOT_START.format(_unit_quote(data + "/auren/mascot"))]
        if lines:
            _write_atomic(path, "# Written by auren setup: your custom XDG and Hugging Face folders\n[Service]\n" + "\n".join(lines) + "\n")
        elif path.exists():
            path.unlink()
            with_parent = path.parent
            if not any(with_parent.iterdir()):
                with_parent.rmdir()


def write_info() -> None:
    _write_atomic(info_file(), json.dumps({"mode": "package", "version": _version(), "package": config.PACKAGE_NAME,
                                           "installed_at": int(time.time())}))


# --- the rest --------------------------------------------------------------------------------------

def secrets(key: str) -> bool:
    """secrets.env, mode 600, with the key if one was given. Returns whether a key was written."""
    path = config.SECRETS
    lines = path.read_text().splitlines() if path.exists() else []
    if not key and lines:
        return False
    if key:
        lines = [ln for ln in lines if ln.partition("=")[0].strip() != "ASSEMBLYAI_API_KEY"]
    lines.append(f"ASSEMBLYAI_API_KEY={key}")
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_atomic(path, "\n".join(lines) + "\n", mode=0o600)
    return bool(key)


def voice_model(ui: Ui) -> None:
    from .voice import missing, retire_old_model
    if os.environ.get("AUREN_SKIP_VOICE_MODEL"):     # CI: not 100 MB per run
        ui.warn("voice model skipped (AUREN_SKIP_VOICE_MODEL): auren voice-model downloads it")
        return
    if not missing():
        ui.ok("Voice model: already downloaded")
        if retire_old_model(lambda line: None):
            ui.ok("Old voice model (Supertonic, about 380 MB) deleted")
        return
    code = ui.spin("Downloading the voice model (about 100 MB)…", [sys.executable, "-P", "-m", "auren.cli", "voice-model"])
    if code == 0:
        ui.ok("Voice model downloaded")
    else:
        ui.warn("no voice model: Auren answers in text until 'auren voice-model' works")


def services(ui: Ui) -> None:
    if updating():
        return                                   # auren update stopped them, and starts them again as they were
    if _systemctl("daemon-reload"):
        ui.warn("couldn't reach your systemd user session: start Auren with systemctl --user enable --now auren auren-mascot")
        return
    stale = []
    for unit in UNITS:
        if _systemctl("is-active", "--quiet", unit) == 0:
            _systemctl("enable", unit)
            if not config.PACKAGED:
                stale.append(unit)
        elif _systemctl("enable", "--now", unit):
            ui.warn(f"{unit} didn't start: journalctl --user -u {unit}")
            continue
    for unit in stale:
        ui.warn(f"{unit} is running the old version: systemctl --user restart {unit} (when Auren is idle)")
    ui.ok("Services: auren, auren-mascot")


def bar_plugin(ui: Ui) -> None:
    if not shutil.which("omarchy"):
        return
    plugins = plugins_dir()
    plugins.mkdir(parents=True, exist_ok=True)
    _link(config.DATA_HOME / "bar-plugin", plugins / PLUGIN)
    shell_json = config_home() / "omarchy" / "shell.json"
    try:
        shell = shell_json.read_text()
    except OSError:
        shell = ""
    old = plugins / OLD_PLUGIN
    if old.is_symlink():
        if f'"{OLD_PLUGIN}"' in shell:
            _rename_plugin(shell_json, ui)
        old.unlink()
        _run(["omarchy-shell", "-q", "shell", "rescanPlugins"])
    elif f'"{PLUGIN}"' not in shell:
        if _run(["omarchy", "plugin", "enable", PLUGIN], timeout=60):
            ui.warn(f"couldn't add the bar plugin: omarchy plugin enable {PLUGIN}")
            return
    ui.ok(f"Bar plugin: {PLUGIN}")


def _rename_plugin(shell_json: Path, ui: Ui) -> None:
    """Up to 0.1.3 the plugin was krvm.auren: put the new one where it was, then remove the old one."""
    ui.step(f"Renaming the bar plugin {OLD_PLUGIN} to {PLUGIN}")
    if _run(["omarchy-shell", "shell", "ping"]) == 0:
        # The running shell owns shell.json: ask it. The new id needs a rescan, which runs in the
        # background, so retry while it says "not ready".
        _run(["omarchy-shell", "shell", "rescanPlugins"])
        result = ""
        for _ in range(50):
            r = subprocess.run(["omarchy-shell", "shell", "putBarWidget", PLUGIN, json.dumps({"after": OLD_PLUGIN})],
                               capture_output=True, text=True)
            result = (r.stdout + r.stderr).strip()
            if result != "not ready":
                break
            time.sleep(0.1)
        if result != "ok":
            ui.warn(f"couldn't place {PLUGIN} in the bar ({result}): omarchy plugin enable {PLUGIN}")
        _run(["omarchy", "plugin", "disable", OLD_PLUGIN])
        return
    # No shell running: rename the entries in place, keeping their position and settings.
    rename_in_shell_json(shell_json, OLD_PLUGIN, PLUGIN)


def rename_in_shell_json(path: Path, old: str, new: str) -> None:
    with path.open() as f:
        doc = json.load(f)

    def rename(node):
        if isinstance(node, dict):
            if node.get("id") == old:
                node["id"] = new
            for value in node.values():
                rename(value)
        elif isinstance(node, list):
            for i, value in enumerate(node):
                if value == old:
                    node[i] = new
                else:
                    rename(value)

    rename(doc)
    _write_atomic(path, json.dumps(doc, indent=2) + "\n")


def browser(plan: Plan, ui: Ui) -> None:
    from .browser_cli import main as browser_main
    if plan.browser == "yes":
        ui.step("Browser tools")
        if browser_main(["setup", plan.browser_kind.key, "--yes"]):
            ui.warn("the browser tools aren't working yet: auren browser setup")
    elif plan.browser == "no":
        from . import settings
        settings.set_value("integrations.browser", "false")
        ui.ok("Browser tools: off (auren browser setup turns them on)")
    elif plan.browser == "later" and plan.browser_kind is not None:
        ui.warn(f"the browser tools aren't set up: run auren browser setup {plan.browser_kind.key}")


def talk_keys(plan: Plan, ui: Ui) -> None:
    bindings = bindings_file()
    if plan.keys in ("super", "alt"):
        backup = add_keys(plan.keys)
        ui.ok(f"Talk keys added to {bindings} (the old file is {backup.name})")
        for name in _key_names(plan.keys):
            ui.note("    " + name)
        _run(["hyprctl", "reload"])                  # in case automatic reloading is off
    elif plan.keys == "keep":
        ui.ok("Talk keys: already in your Hyprland config")
    elif not updating():
        ui.step(f"Add to {bindings}:")
        print("\n".join(keys_lines("super")))
        ui.step("To keep Omarchy's Select all and ChatGPT keys, use these instead:")
        print("\n".join(keys_lines("alt")[1:]))


def _carry_on(what: str, step, ui: Ui) -> None:
    """One part failing (a missing tool, an odd config) mustn't stop the rest of setup."""
    try:
        step()
    except (KeyboardInterrupt, Stop):
        raise
    except Exception as e:  # noqa: BLE001
        ui.warn(f"setting up {what} didn't finish ({type(e).__name__}: {e}): run auren setup again, "
                "or see auren doctor")


def run(a: argparse.Namespace) -> int:
    ui = Ui(interactive=sys.stdin.isatty() and sys.stdout.isatty() and not a.yes)
    first = not done()
    if not updating():
        ui.title("Auren setup")
    try:
        plan = ask(a, ui)
    except (Stop, KeyboardInterrupt):
        print()
        ui.warn("setup stopped: nothing was changed (run auren setup to start again)")
        return 130

    if config.PACKAGED:
        if plan.migrate:
            migrate(plan.migrate, ui)
        link_package()
        xdg_drop_ins()
    config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if secrets(plan.assemblyai_key):
        ui.ok(f"AssemblyAI key saved in {config.SECRETS}")
    elif not config.secret("ASSEMBLYAI_API_KEY"):
        ui.warn(f"add your AssemblyAI key to {config.SECRETS} (ASSEMBLYAI_API_KEY=…)")
    if plan.brain:
        from . import settings
        settings.set_value("brain.mode", plan.brain)
        ui.ok(f"Brain: {plan.brain}")
    voice_model(ui)
    if config.PACKAGED:
        write_info()                             # before the services: the bar plugin reads it
    _carry_on("the services", lambda: services(ui), ui)
    _carry_on("the bar plugin", lambda: bar_plugin(ui), ui)
    _carry_on("the talk keys", lambda: talk_keys(plan, ui), ui)
    _carry_on("the browser tools", lambda: browser(plan, ui), ui)     # last: it can wait minutes
    mark_done()
    if plan.migrate.get("restart_shell") and shutil.which("omarchy"):
        from .update import _restart_shell
        ui.ok("Restarting the Omarchy shell, so the bar plugin runs the package's Auren")
        _restart_shell()
    if updating():
        return 0
    if first:
        ui.note('\nOptional: add to ~/.config/hypr/hyprland.lua to skip mascot layer animations:\n'
                'hl.layer_rule({ match = { namespace = "^auren-mascot" }, no_anim = true, animation = "none" })\n')
    from .doctor import main as doctor_main
    print()
    healthy = doctor_main() == 0
    names = {"super": "Super+A", "alt": "Super+Alt+A"}
    key = names.get(plan.keys, "your talk key")
    if healthy:
        ui.title(f"Auren is ready: press {key} and say what you want.")
    else:
        ui.warn("Auren needs the fixes above first. Run auren setup again any time.")
    return 0


# --- auren setup --remove -----------------------------------------------------------------------------

def remove(a: argparse.Namespace) -> int:
    ui = Ui(interactive=sys.stdin.isatty() and sys.stdout.isatty() and not a.yes)
    data = config.DATA_HOME
    what = ("your talk keys, the bar plugin, the services, the browser host" +
            ("" if config.PACKAGED else ", and the program in " + str(data)))
    try:
        if ui.interactive and not ui.confirm(f"Remove Auren's setup: {what}?", True):
            return 1
        purge = a.purge and (not ui.interactive or ui.confirm(
            "Also delete your settings, API key, history, logs and the voice model?", False))
    except (Stop, KeyboardInterrupt):
        print()
        return 130
    _systemctl("disable", "--now", *UNITS)
    for unit in UNITS:
        _remove(user_units() / f"{unit}.service.d" / DROP_IN)
        d = user_units() / f"{unit}.service.d"
        if d.is_dir() and not any(d.iterdir()):
            d.rmdir()
        if not config.PACKAGED:
            (user_units() / f"{unit}.service").unlink(missing_ok=True)
    _systemctl("daemon-reload")
    ui.ok("Services stopped and turned off")

    if shutil.which("omarchy"):
        _run(["omarchy", "plugin", "disable", PLUGIN], timeout=60)
    for name in (PLUGIN, OLD_PLUGIN):
        link = plugins_dir() / name
        if link.is_symlink():
            link.unlink()
    ui.ok("Bar plugin removed")

    backup = remove_keys()
    if backup:
        _run(["hyprctl", "reload"])
        ui.ok(f"Talk keys taken out of {bindings_file()} (the old file is {backup.name})")

    from .hands import browsers, obu
    for kind in browsers.KINDS:
        host = obu.host_of(kind)
        if host is not None and host.is_relative_to(data):
            obu.manifest_path(kind).unlink(missing_ok=True)
    _remove(data / "obu")
    ui.ok("Browser host removed (remove the Open Browser Use extension in your browser if you don't use it)")

    for name in ("bin", "mascot", "bar-plugin", "venv", "install.json", "setup.json"):
        _remove(data / name)
    for name in ("auren", "auren-ctl"):
        link = user_bin() / name
        if link.is_symlink() and (not link.exists() or Path(os.readlink(link)).is_relative_to(data)):
            link.unlink()
    _remove(config.CACHE_DIR / "source")
    from .voice import cache_dirs
    if purge:
        for d in (config.CONFIG_DIR, config.STATE_DIR, config.CACHE_DIR, data, *cache_dirs()):
            _remove(d)
        if shutil.which("secret-tool"):         # where config.secret also looks for the key
            _run(["secret-tool", "clear", "auren", "assemblyai_api_key"])
        ui.ok("Settings, API key, history, logs and voice model deleted")
    else:
        ui.note(f"Kept: settings and API key ({config.CONFIG_DIR}), history ({config.STATE_DIR}), "
                f"voice model ({cache_dirs()[0].parent}). --purge deletes them.")
    if config.PACKAGED:
        ui.step(f"Now remove the package: sudo pacman -R {config.PACKAGE_NAME}")
    return 0


# --- the command -----------------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="auren setup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--keys", choices=("super", "alt"), default="ask")
    p.add_argument("--no-keys", dest="keys", action="store_const", const="print")
    p.add_argument("--browser", dest="browser", action="store_const", const="yes", default="ask")
    p.add_argument("--no-browser", dest="browser", action="store_const", const="no")
    p.add_argument("--brain", choices=("auto", "claude", "codex"))
    p.add_argument("--yes", action="store_true", help="ask nothing")
    p.add_argument("--remove", action="store_true")
    p.add_argument("--purge", action="store_true")
    return p


def main(args: list[str]) -> int:
    a = parser().parse_args(args)
    if os.geteuid() == 0:
        print("auren setup sets Auren up for one user: run it as yourself, not as root", file=sys.stderr)
        return 1
    if a.purge and not a.remove:
        print("--purge goes with --remove", file=sys.stderr)
        return 2
    return remove(a) if a.remove else run(a)
