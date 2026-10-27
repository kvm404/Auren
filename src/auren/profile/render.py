"""Turn the profile into the compact text the brain reads with every request.

Budget: ~2k tokens. Long lists (all 218 key bindings, 367 Omarchy commands,
aliases, packages) are summarized here and searchable with Profile.lookup.
"""

from __future__ import annotations

import re
from pathlib import Path

HOME = str(Path.home())

CORE_KEY_ACTIONS = {"Terminal", "Browser", "File manager", "Editor", "Herdr", "Tmux", "Agent", "Omarchy menu",
                    "Apps menu", "Keybindings", "Close active window", "Screenshot", "Lock system",
                    "Clipboard manager", "Full screen", "Toggle scratchpad"}


def short(path: str | None) -> str:
    return (path or "").replace(HOME, "~", 1)


def _err(section) -> str | None:
    return f"(unavailable: {section['error']})" if isinstance(section, dict) and "error" in section else None


def _windows(h: dict) -> list[str]:
    if e := _err(h):
        return [e]
    by_ws: dict = {}
    for w in h["windows"]:
        label = f"{w['app']}" + (f" “{w['title']}”" if w["title"] and w["title"].lower() != w["app"].lower() else "")
        if w["focused"]:
            label += " (focused)"
        by_ws.setdefault(w["workspace"], []).append(label)
    lines = [f"- workspace {ws}: " + "; ".join(items) for ws, items in sorted(by_ws.items(), key=lambda kv: str(kv[0]))]
    lines.append(f"- current workspace: {h['active_workspace']}; others empty")
    return lines


def _herdr(hd: dict) -> list[str]:
    if e := _err(hd):
        return [e]
    lines = []
    for w in hd["workspaces"]:
        tabs = []
        for t in w["tabs"]:
            for p in t["panes"]:
                who = f"agent `{p['agent']}` ({p['status']})" if p["agent"] else "shell"
                tabs.append(f"tab {t['label']}: {who} in {short(p['cwd'])}" + (" [focused]" if p["focused"] else ""))
        lines.append(f"- workspace “{w['label']}”" + (" (focused)" if w["focused"] else "") + ": " + "; ".join(tabs))
    return lines


def _apps(a: dict) -> list[str]:
    if e := _err(a):
        return [e]
    names = sorted({x["name"] for x in a["apps"]}, key=str.lower)
    web = [f"{x['name']} ({re.sub(r'^https?://(www\.)?', '', x['url']).rstrip('/')})" for x in a["webapps"]]
    tuis = sorted({x["name"] for x in a["tuis"]}, key=str.lower)
    lines = [f"- apps ({len(names)}): " + ", ".join(names),
             "- web apps (open in the browser's app mode): " + ", ".join(web)]
    if tuis:
        lines.append("- terminal apps: " + ", ".join(tuis))
    if a.get("not_installed"):
        lines.append("- NOT installed: " + ", ".join(a["not_installed"]))
    return lines


def _keys(binds, apps: dict) -> list[str]:
    if e := _err(binds):
        return [e]
    app_names = set()
    if not _err(apps):
        app_names = {x["name"].lower() for x in apps["apps"] + apps["webapps"]}
    picked = [b for b in binds
              if b["action"] in CORE_KEY_ACTIONS or b["action"].lower() in app_names
              or re.match(r"^SUPER SHIFT( ALT)? \+ [A-Z]$", b["keys"])]
    seen, out = set(), []
    for b in picked:
        if b["action"] not in seen:
            seen.add(b["action"])
            out.append(f"{b['action']}: {b['keys'].replace(' + ', '+').replace(' ', '+')}")
    return ["- " + "; ".join(out), f"- ({len(binds)} bindings in total; search with lookup keys)"]


def render_live(l: dict) -> str:
    """What changes from moment to moment: sent with every request."""
    pw, au, mem, now = l.get("power", {}), l.get("audio", {}), l.get("memory", {}), l.get("now", {})
    parts = [f"Now: {now.get('date')} {now.get('time')} ({now.get('timezone')})."]
    status = []
    if pw.get("battery_percent") is not None:
        status.append(f"battery {pw['battery_percent']}% ({pw.get('battery_status', '').lower()}"
                      + (", plugged in" if pw.get("plugged_in") else "") + ")")
    if au.get("volume_percent") is not None:
        status.append(f"volume {au['volume_percent']}%" + (" muted" if au.get("muted") else ""))
    md = l.get("media", {})
    if md.get("title"):
        status.append(("playing" if md.get("playing") else "paused") + f" “{md['title']}”"
                      + (f" by {md['artist']}" if md.get("artist") else "") + f" in {md.get('player')}")
    if mem.get("ram_free_gb") is not None:
        status.append(f"RAM free {mem['ram_free_gb']} of {mem['ram_total_gb']} GB")
    if status:
        parts.append("Status: " + ", ".join(status) + ".")
    parts += ["Windows (Hyprland):", *_windows(l.get("hyprland", {}))]
    if "herdr" in l:
        parts += ["herdr (terminal workspaces with coding agents):", *_herdr(l["herdr"])]
    return "\n".join(parts)


def render_static(s: dict) -> str:
    """What rarely changes: goes into the brain's instructions once."""
    m, d = s.get("machine", {}), s.get("defaults", {})
    parts = ["# This laptop", ""]
    if not _err(m):
        parts.append(f"Machine: Omarchy {m.get('omarchy')} (Arch Linux, Hyprland on Wayland), user {m.get('user')}, "
                     f"home {m.get('home')}, {m.get('cpu')}, {m.get('threads')} threads, {m.get('ram_gb')} GB RAM, shell {short(m.get('shell'))}.")
    if not _err(d):
        names = {}
        if not _err(s.get("apps", {})):
            names = {x["id"]: x["name"] for x in s["apps"]["apps"] + s["apps"]["tuis"]}
        parts.append("Defaults: " + ", ".join(f"{k.replace('_', ' ')} {names.get(v, v)}" for k, v in d.items() if v) + ".")
    parts += ["", "## Installed", *_apps(s.get("apps", {}))]
    parts += ["", "## Key bindings", *_keys(s.get("keybindings", []), s.get("apps", {}))]

    projects = s.get("projects", [])
    if projects and not _err(projects):
        folders = sorted({short(str(Path(p["path"]).parent)) for p in projects})
        parts += ["", f"## Projects (in {', '.join(folders)}, most recently changed first)",
                  "- " + ", ".join(p["name"] for p in projects[:25])]
    vaults = s.get("obsidian_vaults", [])
    if vaults and not _err(vaults):
        parts.append("- Obsidian vault(s): " + ", ".join(short(v) for v in vaults))
    marks = s.get("bookmarks", {})
    if marks and not _err(marks):
        for browser, names in marks.items():
            if names:
                parts.append(f"- {browser} bookmarks ({len(names)}): " + ", ".join(names[:40])
                             + (" …" if len(names) > 40 else ""))
    services = s.get("services", [])
    if services and not _err(services):
        parts.append("- user services running: " + ", ".join(services))

    shell = s.get("shell", {})
    n_alias = len(shell.get("aliases", [])) if not _err(shell) else 0
    n_cmds = len(s.get("omarchy_commands", [])) if not _err(s.get("omarchy_commands", [])) else 0
    parts += ["", "## How things work here",
              f"- Omarchy has one CLI, `omarchy <group> <action>` ({n_cmds} commands: launch, capture, audio, "
              "toggle, reminder, webapp, system, ...). Prefer it over raw tools; look up exact commands first.",
              "- Web apps are launchers that run `omarchy-launch-webapp <url>`; `omarchy webapp install` creates new ones.",
              "- `omarchy launch or focus <window-pattern> <command>` focuses an app if it is open, else starts it.",
              f"- The user's shell has {n_alias} aliases and some functions (e.g. agent launchers); look them up before guessing.",
              "", "More detail on request (lookup): " + ", ".join(
                  ["commands", "keys", "aliases", "functions", "apps", "webapps", "tuis", "packages", "projects"]) + "."]
    return "\n".join(parts)


def render_context(s: dict, l: dict) -> str:
    return render_static(s) + "\n\n# Right now\n" + render_live(l)
