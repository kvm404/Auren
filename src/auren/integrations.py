"""Optional integrations, and the defaults Auren detects.

Each integration is in one of three states:
- enabled: config.toml's `integrations.<name>` isn't false ("auto" or true);
- installed: what it needs is on this machine. Only then are its tools and its rules offered to the brain;
- ready: checked when a tool runs (the browser may be closed at login and opened later), and the tool says
  what's wrong.

`auren doctor` reports all three.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .settings import current as settings


@dataclass(frozen=True)
class Integration:
    name: str
    what: str
    tools: frozenset[str]


INTEGRATIONS = {
    "herdr": Integration("herdr", "coding agents in herdr",
                         frozenset({"list_agents", "read_agent", "prompt_agent", "start_agent", "show_agent"})),
    "browser": Integration("browser", "browser tools (Open Browser Use)",
                           frozenset({"browser_open", "browser_read", "browser_click", "browser_type", "browser_scroll",
                                      "browser_close", "browser_bookmarks", "recent_downloads"})),
    "screen": Integration("screen", "mouse control", frozenset({"click_at", "drag_at", "scroll_at"})),
}


CACHE_FOR = 10          # seconds: installing something shows up this soon, without a check per tool call
_cache: dict[str, tuple[float, tuple[bool, str]]] = {}


def installed(name: str) -> tuple[bool, str]:
    """Whether what the integration needs is here, and if not, what's missing."""
    hit = _cache.get(name)
    if hit and time.monotonic() - hit[0] < CACHE_FOR:
        return hit[1]
    result = _installed(name)
    _cache[name] = (time.monotonic(), result)
    return result


def _installed(name: str) -> tuple[bool, str]:
    if name == "herdr":
        return (True, "") if shutil.which("herdr") else (False, "herdr isn't installed")
    if name == "browser":
        if not shutil.which("open-browser-use"):
            return False, "Open Browser Use isn't installed"
        from .hands import browsers
        try:
            kind = browsers.resolve()
        except browsers.UnknownBrowser as e:
            return False, str(e)
        return (True, "") if browsers.installed(kind) else (False, f"{kind.name} isn't installed")
    if name == "screen":
        return (True, "") if os.access("/dev/uinput", os.W_OK) else (False, "/dev/uinput isn't writable")
    raise KeyError(name)


def state(name: str) -> tuple[str, str]:
    """("on" | "off" | "missing", why): off when config turns it off; missing when it's wanted but not here."""
    if settings()[f"integrations.{name}"] is False:
        return "off", "turned off in config.toml"
    ok, why = installed(name)
    return ("on", "") if ok else ("missing", why)


def active(name: str) -> bool:
    return state(name)[0] == "on"


def hidden_tools() -> set[str]:
    """Tools of integrations that are off or missing: the brain isn't offered them."""
    return {t for n, i in INTEGRATIONS.items() if not active(n) for t in i.tools}


# --- coding agents ------------------------------------------------------------------------------

AGENT_ORDER = ["claude", "codex", "opencode", "gemini", "agy", "grok", "cursor", "copilot", "kimi", "amp", "qwen",
               "pi", "muse"]
AGENT_COMMANDS = {"cursor": "cursor-agent"}         # where the command isn't the kind's name
OMARCHY_AGENT = Path.home() / ".config/omarchy/defaults/agent"


def agent_installed(kind: str) -> bool:
    return shutil.which(AGENT_COMMANDS.get(kind, kind)) is not None


def default_agent() -> str:
    """The coding agent for big jobs: agents.default, else Omarchy's default agent, else the first installed."""
    configured = str(settings()["agents.default"]).lower()
    if configured != "auto":
        return configured
    try:
        omarchy = OMARCHY_AGENT.read_text().strip().lower()
    except OSError:
        omarchy = ""
    if omarchy and agent_installed(omarchy):
        return omarchy
    return next((k for k in AGENT_ORDER if agent_installed(k)), "claude")
