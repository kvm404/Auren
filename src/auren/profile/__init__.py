"""The system profile: what auren knows about this laptop.

    from auren.profile import Profile
    p = Profile()
    p.context()                         # compact text for the brain's prompt
    p.lookup("keys", "obsidian")        # search the full lists on demand
    p.full()                            # everything, as a dict

Static facts are cached in ~/.cache/auren/profile-static.json and rebuilt when
a source they come from changes (launchers, Hyprland config, bashrc, projects,
Obsidian config, Omarchy version) or after MAX_AGE. Live facts are read on every
call. All output is passed through `redact` first.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from ..config import CACHE_DIR, CONFIG_FILE
from . import collect
from .redact import redact, redact_all

CACHE = CACHE_DIR / "profile-static.json"
MAX_AGE = 6 * 3600

WATCHED = [  # a change to any of these rebuilds the static part
    *collect.DESKTOP_DIRS,
    Path.home() / ".config/hypr",
    Path.home() / ".bashrc",
    Path.home() / ".config/omarchy/defaults",
    Path.home() / ".local/state/omarchy/defaults",
    Path.home() / ".config/obsidian/obsidian.json",
    Path("/usr/share/omarchy/bin"),
]
RECHECK = 30          # seconds between checks whether the static part went stale


def _watched() -> list[Path]:
    """WATCHED, plus what depends on the user's setup: project folders, the browser's bookmarks, config."""
    found = collect.bookmark_file()
    return [*WATCHED, *collect.project_folders(), *([found[1]] if found else []), CONFIG_FILE]


def _fingerprint() -> dict[str, float]:
    out = {}
    for p in _watched():
        if p.exists():
            mtimes = [p.stat().st_mtime]
            if p.is_dir():
                mtimes += [c.stat().st_mtime for c in p.iterdir() if not c.name.startswith(".")]
            out[str(p)] = max(mtimes)
    return out


class Profile:
    def __init__(self, cache: Path = CACHE):
        self.cache = cache
        self._static: dict | None = None
        self._checked = 0.0

    # --- data ----------------------------------------------------------------

    def static(self, refresh: bool = False) -> dict:
        if self._static is not None and not refresh and time.monotonic() - self._checked < RECHECK:
            return self._static
        self._checked = time.monotonic()
        fp = _fingerprint()
        if self._static is not None and not refresh and self._static.get("_fingerprint") == fp \
                and time.time() - self._static.get("_built_at", 0) < MAX_AGE:
            return self._static
        if not refresh and self.cache.exists():
            cached = json.loads(self.cache.read_text())
            if cached.get("_fingerprint") == fp and time.time() - cached.get("_built_at", 0) < MAX_AGE:
                self._static = cached
                return cached
        data = redact_all(collect.collect(collect.STATIC))
        data["_fingerprint"], data["_built_at"] = fp, time.time()
        self.cache.parent.mkdir(parents=True, exist_ok=True)
        self.cache.write_text(json.dumps(data, ensure_ascii=False))
        self._static = data
        return data

    def live(self) -> dict:
        return redact_all(collect.collect(collect.LIVE))

    def full(self, refresh: bool = False) -> dict:
        return {**self.static(refresh), **self.live()}

    # --- views ----------------------------------------------------------------

    def context(self) -> str:
        from .render import render_context
        return render_context(self.static(), self.live())

    def static_context(self) -> str:
        from .render import render_static
        return render_static(self.static())

    def live_context(self) -> str:
        from .render import render_live
        return render_live(self.live())

    def lookup(self, topic: str, query: str = "", limit: int = 20) -> str:
        """Search one of the long lists the context only summarizes."""
        s = self.static()
        rows: list[str] = {
            "commands": lambda: [f"{c['route']} {c['args']}".strip() + f"  # {c['summary']}" + (" (sudo)" if c["sudo"] else "")
                                 for c in s["omarchy_commands"]],
            "keys": lambda: [f"{b['keys']} → {b['action']}" for b in s["keybindings"]],
            "aliases": lambda: [f"{a['name']} = {a['value']}" for a in s["shell"]["aliases"]],
            "functions": lambda: s["shell"]["functions"],
            "apps": lambda: [f"{a['name']} (id {a['id']})" + (f" — {a['about']}" if a.get("about") else "")
                             for a in s["apps"]["apps"]],
            "webapps": lambda: [f"{a['name']} → {a['url']} (id {a['id']})" for a in s["apps"]["webapps"]],
            "tuis": lambda: [f"{a['name']}: {a['command']}" for a in s["apps"]["tuis"]],
            "packages": lambda: s["packages"],
            "projects": lambda: [f"{p['name']}: {p['path']}" + (f" — {p['about']}" if p.get("about") else "")
                                 for p in s["projects"]],
        }.get(topic, lambda: [])()
        if isinstance(rows, dict):   # a collector failed
            return f"{topic}: unavailable ({rows.get('error')})"
        words = query.lower().split()
        hits = [r for r in rows if all(w in r.lower() for w in words)]
        more = f"\n… {len(hits) - limit} more" if len(hits) > limit else ""
        return redact("\n".join(hits[:limit]) + more) if hits else f"No {topic} match '{query}'."

    TOPICS = ("commands", "keys", "aliases", "functions", "apps", "webapps", "tuis", "packages", "projects")
