"""What Auren's setup window shows and does (the window is mascot/mac-setup.qml, in the mascot's process).

check() gathers the steps: the three permissions as the daemon has them, the AssemblyAI key, and a brain.
It's plain Python, so tests run it anywhere; the window polls it about once a second while it's open, and
again whenever it comes to the front (the user is back from System Settings).

Nothing needs a restart by hand: the daemon starts the talk keys once Accessibility is allowed, and restarts
itself (once it's idle) for a permission it can only use from its next start (mac/permissions.py). The
window says "Turning on …" meanwhile, and offers Restart only when that restart didn't turn it on.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import time
from pathlib import Path

from ..config import SECRETS, STATE_DIR

DONE = STATE_DIR / "setup.json"          # {"done": true} once the user closes a finished setup
KEY_NAME = "ASSEMBLYAI_API_KEY"
INSTALL_CODEX = "brew install --cask codex && codex"


def _daemon_status() -> dict | None:
    from ..ctl import send
    try:
        return json.loads(send("permissions status", timeout=5))
    except (OSError, ValueError):
        return None


def _brain_path() -> str | None:
    """The PATH the daemon's launch agent gives the brains, as `auren doctor` checks it (None: not installed
    as a launch agent, so this process's own PATH)."""
    from . import service
    return service.installed_path("daemon")


def check(status: dict | None = None, which=shutil.which, restarted: dict | None = None) -> dict:
    """Every step's state, for the window. status: the daemon's `permissions status` (asked when None).
    restarted: what the daemon last restarted itself for (permissions.restarted(), read when None)."""
    from ..config import secret
    from ..settings import current
    from . import permissions
    st = _daemon_status() if status is None else status
    running = st is not None
    st = st or {}
    try:
        cfg = current()
        mode = str(cfg["brain"])
    except Exception:  # noqa: BLE001 - a broken config.toml: the window still works
        cfg, mode = {}, "auto"
    path = _brain_path()
    claude, codex = bool(which("claude", path=path)), bool(which("codex", path=path))
    mic = str(st.get("microphone", "unknown"))
    # Turning on: what the daemon restarts for once it's idle, or, while it isn't answering, what it just
    # restarted for (it's on its way back).
    turning = list(st.get("restarting") or [])
    last = permissions.restarted() if restarted is None else restarted
    starting = not running and time.time() - float(last.get("at") or 0) < permissions.TURNING_ON
    if starting:
        turning = list(last.get("for") or [])
    out = {
        "running": running,
        "starting": starting,
        "microphone": mic,
        "accessibility": bool(st.get("accessibility")),
        "screen": bool(st.get("screen")),
        "talkKeys": bool(st.get("talk_keys")),
        "key": bool(secret(KEY_NAME)),
        "claude": claude,
        "codex": codex,
        "brain": mode,
        "brainReady": (mode == "claude" and claude) or (mode == "codex" and codex) or (mode == "auto" and (claude or codex)),
        "turningOn": turning,
        "turningText": permissions.turning_on(turning),
    }
    out["ready"] = (running and mic == "granted" and out["accessibility"] and out["key"] and out["brainReady"])
    # Restart by hand only when the daemon's own restart can't help: it was tried for that grant already, or
    # Auren runs outside its launch agent. The talk keys: not while they're about to start by themselves,
    # nor when a key in config.toml isn't a shortcut, which no restart fixes (auren doctor says which).
    stuck = [n for n in st.get("restart") or [] if n != "keys"]
    out["keysTurningOn"] = (running and out["accessibility"] and not out["talkKeys"]
                            and (bool(st.get("keys_waiting")) or "keys" in turning))
    keys_off = out["accessibility"] and not out["talkKeys"] and not out["keysTurningOn"]
    out["restart"] = running and (bool(stuck) or (keys_off and _keys_valid(cfg)))
    return out


def _keys_valid(cfg: dict) -> bool:
    from .hotkey import valid
    return valid({name: cfg.get(name) for name in ("keys.talk", "keys.talk_fresh")})


def needs(s: dict) -> str:
    """What the setup still needs, in a line (the Auren window's Home and the menu), or "" if nothing."""
    if s.get("turningText"):
        return s["turningText"]
    if not s.get("running"):
        return "Auren isn't running."
    missing = [name for name, ok in (("the microphone", s.get("microphone") == "granted"),
                                     ("Accessibility", s.get("accessibility")),
                                     ("a speech-to-text key", s.get("key")),
                                     ("a brain", s.get("brainReady"))) if not ok]
    if missing:
        return "Needs " + (missing[0] if len(missing) == 1 else ", ".join(missing[:-1]) + " and " + missing[-1]) + "."
    return "Restart Auren to turn on what you allowed." if s.get("restart") else ""


def brain_text(s: dict) -> tuple[str, str]:
    """The brain row's line, and what its button does ("" for no button, else a brain mode or "copy")."""
    claude, codex, mode = s["claude"], s["codex"], s["brain"]
    if not claude and not codex:
        return "Install Codex or Claude Code, then sign in once in a terminal.", "copy"
    if mode == "claude" and not claude:
        return "Auren is set to Claude Code, which isn't installed. Codex is.", "codex"
    if mode == "codex" and not codex:
        return "Auren is set to Codex, which isn't installed. Claude Code is.", "claude"
    if claude and codex:
        return {"claude": "Claude Code answers.", "codex": "Codex answers."}.get(
            mode, "Claude Code answers, and Codex when Claude can't."), ""
    return ("Claude Code answers." if claude else "Codex answers."), ""


def save_key(value: str, path: Path = SECRETS) -> str:
    """Write the AssemblyAI key into secrets.env (mode 600), replacing an old one. '' if saved, else why not."""
    value = value.strip().strip("\"'")
    if not re.fullmatch(r"[A-Za-z0-9_\-]{20,128}", value):
        return "That doesn't look like an AssemblyAI key"
    path = path.resolve()                 # a symlinked secrets.env (dotfiles) stays a symlink
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines() if path.exists() else []
    kept = [line for line in lines if line.partition("=")[0].strip() != KEY_NAME]
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)           # a leftover one would keep its own, looser, mode
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join([*kept, f"{KEY_NAME}={value}"]) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return ""


def done() -> bool:
    try:
        return json.loads(DONE.read_text()).get("done") is True
    except (OSError, ValueError, AttributeError):
        return False


def mark_done() -> None:
    DONE.parent.mkdir(parents=True, exist_ok=True)
    DONE.write_text(json.dumps({"done": True}) + "\n")
