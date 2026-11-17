"""What Auren's setup window shows and does (the window is mascot/mac-setup.qml, in the mascot's process).

check() gathers the steps: the three permissions as the daemon has them, the AssemblyAI key, and a brain.
It's plain Python, so tests run it anywhere; the window polls it about once a second while it's open.
"""

from __future__ import annotations

import json
import os
import re
import shutil
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


def check(status: dict | None = None, which=shutil.which) -> dict:
    """Every step's state, for the window. status: the daemon's `permissions status` (asked when None)."""
    from ..config import secret
    from ..settings import current
    st = _daemon_status() if status is None else status
    running = st is not None
    st = st or {}
    try:
        cfg = current()
        mode = str(cfg["brain"])
    except Exception:  # noqa: BLE001 - a broken config.toml: the window still works
        cfg, mode = {}, "auto"
    claude, codex = bool(which("claude")), bool(which("codex"))
    mic = str(st.get("microphone", "unknown"))
    out = {
        "running": running,
        "microphone": mic,
        "accessibility": bool(st.get("accessibility")),
        "screen": bool(st.get("screen")),
        "talkKeys": bool(st.get("talk_keys")),
        "key": bool(secret(KEY_NAME)),
        "claude": claude,
        "codex": codex,
        "brain": mode,
        "brainReady": (mode == "claude" and claude) or (mode == "codex" and codex) or (mode == "auto" and (claude or codex)),
    }
    out["ready"] = (running and mic == "granted" and out["accessibility"] and out["key"] and out["brainReady"])
    # The keys are off after Accessibility was allowed: a restart turns them on. Not when a key in
    # config.toml isn't a shortcut, which no restart fixes (auren doctor says which).
    out["restart"] = running and out["accessibility"] and not out["talkKeys"] and _keys_valid(cfg)
    return out


def _keys_valid(cfg: dict) -> bool:
    from .hotkey import parse
    try:
        for name in ("keys.talk", "keys.talk_fresh"):
            if cfg.get(name):
                parse(str(cfg[name]))
    except ValueError:
        return False
    return True


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
