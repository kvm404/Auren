"""auren doctor: what works on this machine, what doesn't, and how to fix it.

Checks run read-only and fast. Commands are looked up twice: in this shell's PATH and in the PATH
of the systemd user session the daemon runs in (tools installed by version managers such as mise
are often only in the first).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .config import CONFIG_DIR, DATA_HOME, secret

OK, WARN, FAIL = "ok", "warn", "fail"
HYPRLAND_MIN = (0, 56)        # Hyprland's Lua dispatchers (hl.dsp.*), which the window tools use


@dataclass
class Check:
    group: str
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def _run(cmd: list[str], timeout: float = 5) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or p.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, str(e)


def service_path() -> str | None:
    """PATH as the systemd user services see it."""
    code, out = _run(["systemctl", "--user", "show-environment"])
    if code:
        return None
    for line in out.splitlines():
        if line.startswith("PATH="):
            return line[5:]
    return None


def _command(group: str, cmd: str, why: str, svc_path: str | None, required: bool = True, fix: str = "") -> Check:
    here = shutil.which(cmd)
    bad = FAIL if required else WARN
    if not here:
        return Check(group, cmd, bad, f"not installed ({why})", fix or f"install {cmd}")
    if svc_path is not None and not shutil.which(cmd, path=svc_path):
        return Check(group, cmd, bad, f"found at {here}, but not in the PATH systemd services get",
                     "link it into ~/.local/bin, or run: systemctl --user import-environment PATH")
    return Check(group, cmd, OK, here)


def _version(text: str) -> tuple[int, ...]:
    parts = []
    for p in text.lstrip("v").split("."):
        digits = "".join(c for c in p if c.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


# Hyprland's modmask bits, in the order key names are written (Super+Ctrl+Alt+A)
MODS = ((64, "Super"), (1, "Shift"), (4, "Ctrl"), (8, "Alt"))
TALK_KEYS = {"talk": "Super+A", "talk-fresh": "Super+Shift+A"}           # what install.sh prints
SPARE_KEYS = {"talk": "Super+Alt+A", "talk-fresh": "Super+Ctrl+Alt+A"}    # its alternative, free on Omarchy 4.5
NO_TALK = "no binding named \"Auren: talk …\" (doctor finds Auren's keys by the names install.sh gives them)"
NO_TALK_FIX = "add the bindings ./install.sh prints to ~/.config/hypr/bindings.lua, keeping their names"


def _key_name(b: dict) -> str:
    key = b.get("key") or f"code:{b.get('keycode', 0)}"
    return "+".join([name for bit, name in MODS if b.get("modmask", 0) & bit] + [key.upper() if len(key) == 1 else key])


def talk_keys(binds: list[dict]) -> list[Check]:
    """The keys that run auren-ctl talk and talk-fresh, found by the descriptions install.sh gives
    them ("Auren: talk ..."): Hyprland reports Lua binds as __lua, without their command. A key that
    also runs another binding warns: Hyprland runs both (Omarchy 4.5 put Select all on Super+A)."""
    g = "Install"
    ours = [b for b in binds if (b.get("description") or "").startswith("Auren: talk")]
    if not ours:
        return [Check(g, "talk key", WARN, NO_TALK, NO_TALK_FIX)]
    out = []
    for which in ("talk", "talk-fresh"):
        mine = [b for b in ours if ("fresh" in b["description"]) == (which == "talk-fresh") and not b.get("release")]
        if not mine:
            if which == "talk":
                out.append(Check(g, "talk key", WARN, NO_TALK, NO_TALK_FIX))
            continue
        b = mine[0]
        name = _key_name(b)
        same = [o for o in binds if o not in ours and _key_name(o) == name
                and o.get("submap", "") == b.get("submap", "") and bool(o.get("release")) == bool(b.get("release"))]
        label = "talk key" if which == "talk" else "fresh-conversation key"
        if same:
            others = ", ".join(f'"{o.get("description") or o.get("dispatcher", "?")}"' for o in same)
            hypr = name.upper().replace("+", " + ")
            fix = f'add hl.unbind("{hypr}") above Auren\'s binding in ~/.config/hypr/bindings.lua'
            if name != SPARE_KEYS[which]:
                fix += f", or move Auren to {SPARE_KEYS[which]}"
            out.append(Check(g, label, WARN, f"{name} also runs {others}: both happen on each press", fix))
        else:
            known = name in (TALK_KEYS[which], SPARE_KEYS[which])
            out.append(Check(g, label, OK, name if known else f"{name} (the docs say {TALK_KEYS[which]})"))
    return out


def checks() -> list[Check]:
    out: list[Check] = []
    svc = service_path()
    if svc is None:
        out.append(Check("Session", "systemd user session", WARN, "systemctl --user didn't answer"))

    # --- desktop ---------------------------------------------------------------------------------
    g = "Desktop"
    code, ver = _run(["omarchy", "version"])
    out.append(Check(g, "Omarchy", OK, ver) if code == 0 and ver else
               Check(g, "Omarchy", FAIL, "not found: Auren is made for Omarchy", "https://omarchy.org"))
    code, raw = _run(["hyprctl", "version", "-j"])
    try:
        hv = json.loads(raw).get("version", "") if code == 0 else ""
    except json.JSONDecodeError:
        hv = ""
    if not hv:
        out.append(Check(g, "Hyprland", FAIL, "hyprctl didn't answer", "run this inside your Hyprland session"))
    elif _version(hv) < HYPRLAND_MIN:
        out.append(Check(g, "Hyprland", FAIL, f"{hv}: Auren needs {'.'.join(map(str, HYPRLAND_MIN))} or newer",
                         "update Omarchy (omarchy update)"))
    else:
        out.append(Check(g, "Hyprland", OK, hv))
    for cmd, why in (("wtype", "typing"), ("wl-copy", "pasting typed text"), ("grim", "screenshots"), ("xdg-settings", "default browser"),
                     ("qs", "the mascot")):
        out.append(_command(g, cmd, why, svc))

    # --- audio and speech ------------------------------------------------------------------------------
    g = "Audio"
    for cmd, why in (("pw-record", "the microphone"), ("pw-cat", "the voice"), ("wpctl", "volume")):
        out.append(_command(g, cmd, why, svc))
    code, mic = _run(["wpctl", "inspect", "@DEFAULT_AUDIO_SOURCE@"])
    name = next((ln.split("=", 1)[1].strip().strip('"') for ln in mic.splitlines() if "node.description" in ln), "")
    out.append(Check(g, "microphone", OK, name or "default source") if code == 0 else
               Check(g, "microphone", FAIL, "no default microphone", "pick one in Omarchy's audio settings"))
    has_key = bool(secret("ASSEMBLYAI_API_KEY"))
    out.append(Check(g, "speech-to-text key", OK, "AssemblyAI") if has_key else
               Check(g, "speech-to-text key", FAIL, "no AssemblyAI key",
                     f"add ASSEMBLYAI_API_KEY=… to {CONFIG_DIR / 'secrets.env'}"))
    try:
        from .voice import model_dir
        folder = model_dir()
        out.append(Check(g, "voice model", OK, str(folder)) if folder else
                   Check(g, "voice model", WARN, "not downloaded: replies will be text only", "auren voice-model"))
    except ImportError as e:
        out.append(Check(g, "voice model", WARN, f"voice library missing ({e})", "./install.sh"))

    # --- brain -------------------------------------------------------------------------------------
    g = "Brain"
    claude = _command(g, "claude", "Claude Code, the main brain", svc, required=False,
                      fix="install Claude Code and log in (claude)")
    codex = _command(g, "codex", "Codex, the backup brain", svc, required=False, fix="install Codex and log in (codex)")
    out += [claude, codex]
    if claude.status != OK and codex.status != OK:
        out.append(Check(g, "a brain", FAIL, "neither Claude Code nor Codex can run", "install one of them"))

    # --- optional integrations ------------------------------------------------------------------------
    from . import integrations
    from .hands import obu

    g = "Optional"
    fixes = {"herdr": "https://herdr.dev, or turn it off: auren config set integrations.herdr false",
             "browser": "auren browser setup, or turn it off: auren config set integrations.browser false",
             "screen": "add a udev rule giving your user access to /dev/uinput (see README)"}
    for name, i in integrations.INTEGRATIONS.items():
        st, why = integrations.state(name)
        if st == "off":
            out.append(Check(g, i.what, OK, "turned off"))
        elif st == "missing":
            wanted = integrations.settings()[f"integrations.{name}"] is True
            left = ("its tools offer to set it up" if i.offer_unset and integrations._can_set_up(name)
                    else "its tools are left out")
            out.append(Check(g, i.what, FAIL if wanted else WARN, f"{why}: {left}", fixes[name]))
        elif name == "herdr" and _run(["herdr", "workspace", "list"])[0] != 0:
            out.append(Check(g, i.what, WARN, "installed but not running", "open it: omarchy-launch-terminal herdr"))
        elif name == "browser":
            target = obu.choose()
            binary = obu.cli() or ""
            if binary != str(obu.own_binary()) and svc is not None and not shutil.which(Path(binary).name, path=svc):
                out.append(Check(g, i.what, WARN, f"{binary} isn't in the PATH systemd services get",
                                 "auren browser setup (installs Auren's own copy)"))
            else:
                out.append(Check(g, i.what, OK, target.label() + (" (connected)" if target.conn else
                                 " (not connected: it opens when needed)")))
        else:
            out.append(Check(g, i.what, OK, "on"))
    agent = integrations.default_agent()
    out.append(Check(g, "default coding agent", OK if integrations.agent_installed(agent) else WARN, agent,
                     "" if integrations.agent_installed(agent) else f"install {agent}, or: auren config set agents.default <agent>"))
    from .profile.collect import project_folders
    folders = project_folders()
    out.append(Check(g, "project folders", OK if folders else WARN,
                     ", ".join(str(f).replace(str(Path.home()), "~") for f in folders) or "none found",
                     "" if folders else 'auren config set projects.folders "~/where/they/are"'))

    # --- install ----------------------------------------------------------------------------------
    g = "Install"
    ctl = DATA_HOME / "bin/auren-ctl"
    out.append(Check(g, "auren-ctl", OK, str(ctl)) if os.access(ctl, os.X_OK) else
               Check(g, "auren-ctl", FAIL, f"{ctl} missing", "./install.sh"))
    code, raw = _run(["hyprctl", "binds", "-j"])
    try:
        binds = json.loads(raw) if code == 0 else None
    except json.JSONDecodeError:
        binds = None
    if isinstance(binds, list):          # no Hyprland: the Desktop group already says so
        out += talk_keys(binds)
    units = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd/user"
    for unit in ("auren", "auren-mascot"):
        f = units / f"{unit}.service"
        if not f.exists():
            out.append(Check(g, unit, FAIL, "service not installed", "./install.sh"))
            continue
        exe = next((ln.split("=", 1)[1].split()[0] for ln in f.read_text().splitlines() if ln.startswith("ExecStart=")), "")
        state = _run(["systemctl", "--user", "is-active", unit])[1] or "unknown"
        if exe and not os.access(exe, os.X_OK):
            out.append(Check(g, unit, FAIL, f"its program {exe} is missing", "./install.sh"))
        else:
            out.append(Check(g, unit, OK if state == "active" else WARN, state,
                             "" if state == "active" else f"systemctl --user start {unit}"))
    return out


SYMBOL = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m"}


def main() -> int:
    results = checks()
    group = None
    for c in results:
        if c.group != group:
            group = c.group
            print(f"\033[1m{group}\033[0m")
        print(f"  {SYMBOL[c.status]} {c.name}" + (f": {c.detail}" if c.detail else ""))
        if c.fix and c.status != OK:
            print(f"      fix: {c.fix}")
    failed = sum(c.status == FAIL for c in results)
    print(f"\n{failed} problem(s)" if failed else "\nAuren can run here.")
    return 1 if failed else 0
