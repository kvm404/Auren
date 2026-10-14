"""~/.config/auren/config.toml: the user's settings.

The daemon keeps them as a flat dict (voice, voiceName, speed, ..., brain, effort); the file
groups them in sections. The bar panel and auren-ctl change some of them through the daemon, which
rewrites only those values: comments and anything else in the file are kept.

    auren config                    # every setting, and whether it's the default
    auren config set voice.speed 1.1
    auren config get brain.mode
    auren config edit               # in $EDITOR, checked afterwards
    auren config path

A running daemon picks up changes made here (auren config sends it `reload`). The old settings.json
is migrated once, then kept as settings.json.bak.
"""

from __future__ import annotations

import contextlib
import copy
import fcntl
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import tomlkit

from .config import CONFIG_FILE
from .config import SETTINGS as LEGACY

log = logging.getLogger("auren.settings")

MODES = ("auto", "claude", "codex")                       # Brain.MODES
EFFORTS = ("low", "medium", "high", "xhigh", "max")       # Brain.EFFORTS
CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")   # Brain.CODEX_EFFORTS
SENSITIVITIES = ("low", "medium", "high")                 # wake.SENSITIVITY
FOLLOW_UPS = ("off", "questions", "always")


def _boolean(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    word = str(v).strip().lower()
    if word in ("true", "on", "yes", "1"):
        return True
    if word in ("false", "off", "no", "0"):
        return False
    raise ValueError("true or false")


def _number(low: float, high: float, whole: bool = False) -> Callable[[Any], float | int]:
    def check(v: Any) -> float | int:
        if isinstance(v, bool):
            raise ValueError(f"a number from {low} to {high}")
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"a number from {low} to {high}") from None
        if not low <= n <= high:
            raise ValueError(f"from {low} to {high}")
        return int(round(n)) if whole else round(n, 2)
    return check


def _choice(*options: str) -> Callable[[Any], str]:
    def check(v: Any) -> str:
        if str(v) not in options:
            raise ValueError(" or ".join(options))
        return str(v)
    return check


def _switch(v: Any) -> str | bool:
    """auto (use it if it's installed), true or false."""
    if str(v).strip().lower() == "auto":
        return "auto"
    try:
        return _boolean(v)
    except ValueError:
        raise ValueError("auto, true or false") from None


def _name(v: Any) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][\w .+-]{0,40}", str(v)):
        raise ValueError("auto or a name")
    return str(v)


def _model(v: Any) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][\w.:\[\]/-]{0,80}", str(v)):
        raise ValueError("a model name like haiku or gpt-6.1-sol")
    return str(v)


def _folders(v: Any) -> list[str]:
    items = [v] if isinstance(v, str) else v
    if not isinstance(items, list) or not all(isinstance(x, str) and x.strip() for x in items):
        raise ValueError("a list of folders")
    return [x.strip() for x in items]


def _spellings(v: Any) -> list[str]:
    items = [v] if isinstance(v, str) else v
    if not isinstance(items, list) or not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z' ]{1,30}", x.strip())
                                              for x in items):
        raise ValueError('a list of spellings like ["or in", "orin"]')
    return [" ".join(x.lower().split()) for x in items][:12]


def _paths_by_name(v: Any) -> dict[str, str]:
    if not isinstance(v, dict) or not all(isinstance(k, str) and isinstance(x, str) for k, x in v.items()):
        raise ValueError('a table like { chrome = "/tmp/open-browser-use-chrome" }')
    return {k.lower(): x for k, x in v.items()}


def _voice_name(v: Any) -> str:
    if not re.fullmatch(r"[FM]\d", str(v)):
        raise ValueError("a voice like F2 or M3")
    return str(v)


@dataclass(frozen=True)
class Key:
    path: str           # where it lives in config.toml: "section.name", or "name" at the top
    flat: str           # its name in the daemon's dict (and the old settings.json)
    default: Any
    check: Callable[[Any], Any]
    help: str


# Ranges match voice.TUNING (decision 0001's defaults); a test keeps them in step.
KEYS = [
    Key("voice.enabled", "voice", True, _boolean, "Speak replies (false: they only show in the mascot's bubble)"),
    Key("voice.name", "voiceName", "F2", _voice_name, "Supertonic voice: F1..F5 or M1..M5"),
    Key("voice.speed", "speed", 1.05, _number(0.7, 1.6), "How fast it speaks: 0.7 to 1.6"),
    Key("voice.steps", "steps", 5, _number(2, 12, whole=True), "Quality: 2 to 12 (higher costs more CPU)"),
    Key("voice.volume", "volume", 1.0, _number(0.1, 1.0), "Volume relative to the system's: 0.1 to 1"),
    Key("voice.pause", "pause", 0.0, _number(0.0, 1.0), "Silence between sentences in seconds: 0 to 1"),
    Key("brain.mode", "brain", "auto", _choice(*MODES), "Who answers: auto (Claude Code, Codex when Claude hits a limit), claude or codex"),
    Key("brain.effort", "effort", "low", _choice(*EFFORTS), "Claude's effort: low, medium, high, xhigh or max"),
    Key("brain.claude_model", "brain.claude_model", "haiku", _model,
        "Claude Code's --model: haiku, sonnet, opus, or a full model id"),
    Key("brain.codex_model", "brain.codex_model", "gpt-6.1-sol", _model, "Codex's model (codex exec -m)"),
    Key("brain.codex_effort", "brain.codex_effort", "low", _choice(*CODEX_EFFORTS),
        "Codex's reasoning effort: minimal, low, medium, high or xhigh"),
    Key("handsfree.enabled", "handsfree", False, _boolean,
        "Hands-free: say \"Auren\" instead of pressing Super+A. The mic stays open, but only this laptop hears it until you say the name"),
    Key("handsfree.sensitivity", "handsfree.sensitivity", "medium", _choice(*SENSITIVITIES),
        "How readily it hears its name: low (fewer false starts), medium or high (hears it more often, and other words too)"),
    Key("handsfree.heard_as", "handsfree.heard_as", [], _spellings,
        "How the wake-word model hears you say the name (Learn my voice on the bar panel fills it in)"),
    Key("handsfree.follow_up", "handsfree.follow_up", "questions", _choice(*FOLLOW_UPS),
        "Listen again after a reply, without the name: off, questions (when Auren asked you something) or always"),
    Key("handsfree.end_silence", "handsfree.end_silence", 1.2, _number(0.6, 4.0),
        "Seconds of silence that end what you say: 0.6 to 4 (a finished sentence ends sooner)"),
    Key("handsfree.chime", "handsfree.chime", True, _boolean, "A short chime when Auren hears its name"),
    Key("handsfree.pause_when_mic_busy", "handsfree.pause_when_mic_busy", True, _boolean,
        "Stop listening for the name while another app records (dictation, a call)"),
    Key("integrations.herdr", "integrations.herdr", "auto", _switch,
        "Coding agents and commands in herdr tabs: auto (if herdr is installed), true or false. Without it, commands run in the background"),
    Key("integrations.browser", "integrations.browser", "auto", _switch,
        "Browser tools through Open Browser Use: auto (if it's installed), true or false"),
    Key("integrations.screen", "integrations.screen", "auto", _switch,
        "Mouse control (click, drag, scroll on the screen): auto (if /dev/uinput is writable), true or false"),
    Key("browser.name", "browser.name", "auto", _name,
        "Browser for the browser tools: auto (your default browser), or chrome, chromium, brave, helium, edge, vivaldi"),
    Key("browser.profile", "browser.profile", "auto", _name,
        "Browser profile folder for bookmarks and downloads: auto (the last one used), or Default, Profile 1, ..."),
    Key("browser.sockets", "browser.sockets", {}, _paths_by_name,
        "Open Browser Use socket folder per browser, if you run more than one host (default /tmp/open-browser-use)"),
    Key("projects.folders", "projects.folders", [], _folders,
        "Folders that hold your projects; empty: the usual ones that exist (~/Projects, ~/Code, ~/src, ...)"),
    Key("agents.default", "agents.default", "auto", _name,
        "Coding agent for big jobs: auto (Omarchy's default agent, else the first installed), or codex, claude, opencode, ..."),
]
BY_PATH = {k.path: k for k in KEYS}
BY_FLAT = {k.flat: k for k in KEYS}

HEADER = """Auren's settings. Edit them here or with `auren config set KEY VALUE`; the bar panel and
auren-ctl change some of them too (comments and order are kept)."""


def defaults() -> dict:
    return {k.flat: copy.deepcopy(k.default) for k in KEYS}


def _lookup(doc: dict, path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            raise KeyError(path)
        node = node[part]
    return node


def _plain(value: Any) -> Any:
    return value.unwrap() if hasattr(value, "unwrap") else value


def _unknown(doc: dict, prefix: str = "") -> list[str]:
    out = []
    for name, value in doc.items():
        path = prefix + name
        if path == "language":        # retired setting: older installs may still have it
            continue
        if isinstance(value, dict) and not any(k.path == path for k in KEYS):
            out += _unknown(value, path + ".")
        elif path not in BY_PATH:
            out.append(path)
    return out


def read(text: str) -> tuple[dict, list[str]]:
    """The settings in a config.toml (defaults for what's missing or wrong) and what was wrong."""
    try:
        doc = tomlkit.parse(text).unwrap()
    except tomlkit.exceptions.TOMLKitError as e:
        return defaults(), [f"config.toml can't be read ({e}): using the defaults"]
    values, problems = defaults(), []
    for k in KEYS:
        try:
            raw = _lookup(doc, k.path)
        except KeyError:
            continue
        try:
            values[k.flat] = k.check(raw)
        except ValueError as e:
            problems.append(f"{k.path} = {raw!r} should be {e}: using {k.default!r}")
    problems += [f"unknown setting {p}" for p in _unknown(doc)]
    return values, problems


def _item(value: Any) -> Any:
    """Tables are written inline (`sockets = { chrome = "..." }`), so they stay next to their comment."""
    if isinstance(value, dict):
        t = tomlkit.inline_table()
        t.update(value)
        return t
    return value


def _template(values: dict) -> tomlkit.TOMLDocument:
    doc = tomlkit.document()
    for line in HEADER.splitlines():
        doc.add(tomlkit.comment(line))
    doc.add(tomlkit.nl())
    tables: dict[str, Any] = {}
    for k in KEYS:
        section, _, name = k.path.rpartition(".")
        target = doc
        if section:
            if section not in tables:
                tables[section] = tomlkit.table()
                doc.add(section, tables[section])
            target = tables[section]
        target.add(tomlkit.comment(k.help))
        target.add(name, _item(values.get(k.flat, k.default)))
        if not section:
            target.add(tomlkit.nl())
    return doc


@contextlib.contextmanager
def _locked():
    """One writer at a time across processes (the daemon, auren config): each reads, changes and writes
    the file whole, so two at once would lose one's change."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE.with_name(".config.lock"), "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _write(doc: tomlkit.TOMLDocument) -> None:
    fd, tmp = tempfile.mkstemp(dir=CONFIG_FILE.parent, prefix=".config.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(tomlkit.dumps(doc))
        os.replace(tmp, CONFIG_FILE)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def ensure() -> None:
    """Create config.toml if missing and remove the retired language setting from older installs."""
    with _locked():
        _ensure()


def _ensure() -> None:
    if CONFIG_FILE.exists():
        try:
            doc = tomlkit.parse(CONFIG_FILE.read_text())
        except (OSError, tomlkit.exceptions.TOMLKitError):
            return                    # leave unreadable files alone, as save() does
        if "language" in doc:
            del doc["language"]
            _write(doc)
        return
    values = defaults()
    if LEGACY.exists():
        try:
            old = json.loads(LEGACY.read_text())
        except (OSError, json.JSONDecodeError):
            old = {}
        for flat, raw in (old.items() if isinstance(old, dict) else ()):
            if (k := BY_FLAT.get(flat)) is not None:
                try:
                    values[flat] = k.check(raw)
                except ValueError:
                    pass
    _write(_template(values))
    if LEGACY.exists():
        LEGACY.replace(LEGACY.with_name(LEGACY.name + ".bak"))
        log.info("moved %s into %s", LEGACY, CONFIG_FILE)


def load() -> dict:
    """The daemon's settings (flat), creating config.toml if needed. Problems are logged, never fatal."""
    ensure()
    try:
        text = CONFIG_FILE.read_text()
    except OSError as e:
        log.warning("can't read %s: %s", CONFIG_FILE, e)
        return defaults()
    values, problems = read(text)
    for p in problems:
        log.warning("%s: %s", CONFIG_FILE, p)
    return values


_current: tuple[tuple, dict] | None = None


def current() -> dict:
    """The settings now, re-read only when config.toml changes (for code that asks on every call)."""
    global _current
    try:
        st = CONFIG_FILE.stat()
    except OSError:
        return defaults()
    key = (str(CONFIG_FILE), st.st_mtime_ns, st.st_size)
    if _current is None or _current[0] != key:
        _current = (key, read(CONFIG_FILE.read_text())[0])
    return _current[1]


def save(values: dict) -> bool:
    """Write these settings (flat) into config.toml, keeping everything else in the file. A file with a
    mistake in it is left alone (the user may be editing it): returns False, and nothing is saved."""
    with _locked():
        _ensure()
        try:
            doc = tomlkit.parse(CONFIG_FILE.read_text())
        except (OSError, tomlkit.exceptions.TOMLKitError) as e:
            log.warning("%s can't be read (%s): not saving %s until it's fixed", CONFIG_FILE, e, ", ".join(values))
            return False
        _update(doc, values)
        _write(doc)
    return True


def _update(doc: tomlkit.TOMLDocument, values: dict) -> None:
    for flat, value in values.items():
        k = BY_FLAT.get(flat)
        if k is None:
            continue
        section, _, name = k.path.rpartition(".")
        target = doc
        if section:
            if section not in doc:
                doc.add(section, tomlkit.table())
            target = doc[section]
        if _plain(target.get(name)) != value:
            target[name] = _item(value)


def _parse(raw: str) -> Any:
    """A table or list written as in config.toml: { chrome = "/tmp/x" }, ["~/work", "~/src"]. Plain text
    (one folder) is kept as it is."""
    if not raw.strip().startswith(("{", "[")):
        return raw
    try:
        return tomlkit.parse(f"value = {raw}")["value"].unwrap()
    except tomlkit.exceptions.TOMLKitError as e:
        raise ValueError(f"written as in config.toml ({e})") from None


def set_value(path: str, raw: str) -> Any:
    """Check and save one setting given as text; returns the value saved. Raises ValueError."""
    k = BY_PATH.get(path)
    if k is None:
        raise ValueError(f"unknown setting {path} (see: auren config)")
    try:
        value = k.check(_parse(raw) if isinstance(k.default, (dict, list)) else raw)
    except ValueError as e:
        raise ValueError(f"{path} should be {e}") from None
    if not save({k.flat: value}):
        raise ValueError(f"{CONFIG_FILE} has a mistake in it: fix it first (auren config edit)")
    return value


# --- auren config ----------------------------------------------------------------------------------

def _tell_daemon() -> str:
    from .ctl import send

    try:
        return send("reload", timeout=5)
    except OSError:
        return ""


def _show() -> int:
    ensure()
    values, problems = read(CONFIG_FILE.read_text())
    print(f"\033[2m{CONFIG_FILE}\033[0m")
    for k in KEYS:
        v = values[k.flat]
        shown = v if isinstance(v, (int, float)) and not isinstance(v, bool) else json.dumps(v)
        print(f"  {k.path} = {shown}" + ("" if v != k.default else "  \033[2m(default)\033[0m"))
    for p in problems:
        print(f"\033[33m! {p}\033[0m")
    return 1 if problems else 0


def main(args: list[str]) -> int:
    cmd = args[0] if args else "show"
    if cmd == "show" and len(args) <= 1:
        return _show()
    if cmd == "path" and len(args) == 1:
        print(CONFIG_FILE)
        return 0
    if cmd == "get" and len(args) == 2:
        if args[1] not in BY_PATH:
            print(f"unknown setting {args[1]}", file=sys.stderr)
            return 1
        print(load()[BY_PATH[args[1]].flat])
        return 0
    if cmd == "set" and len(args) >= 3:
        try:
            value = set_value(args[1], " ".join(args[2:]))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        reply = _tell_daemon()
        print(f"{args[1]} = {json.dumps(value)}" + (f" ({reply})" if reply else " (used when Auren starts)"))
        return 0
    if cmd == "edit" and len(args) == 1:
        ensure()
        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "nano"
        subprocess.run([*editor.split(), str(CONFIG_FILE)])
        code = _show()
        if reply := _tell_daemon():
            print(reply)
        return code
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2
