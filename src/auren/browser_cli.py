"""auren browser: see and set up the browser tools (Open Browser Use).

    auren browser                      which browsers have it, which profiles are connected, which one Auren uses
    auren browser setup [BROWSER]      set it up in a browser (default: your default browser)
        --yes                          don't ask before downloading (for scripts and agents that asked already)
        --no-open                      don't open the extension's Web Store page
        --wait SECONDS                 how long to wait for the extension to connect (default 180; 0: don't)
    auren browser use BROWSER [PROFILE]   always use this browser (and profile); `use auto` goes back to choosing
"""

from __future__ import annotations

import argparse
import sys

from . import settings
from .hands import browsers, obu


def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if sys.stdout.isatty() else s


def status() -> int:
    binary = obu.cli()
    if binary:
        own = " (Auren's copy)" if binary == str(obu.own_binary()) else ""
        print(f"Open Browser Use: {binary} {obu.version_of(binary)}{own}")
    else:
        print("Open Browser Use: not installed")
    conns = obu.connections(fresh=True)
    default = browsers.default_kind()
    shown = False
    for kind in browsers.KINDS:
        if not browsers.installed(kind):
            continue
        shown = True
        name = kind.name + (" (default)" if kind == default else "")
        parts = []
        if browsers.flatpak(kind):
            parts.append("a Flatpak: not supported")
        elif not obu.registered(kind):
            parts.append("not set up")
        else:
            parts.append("host registered")
            ext = obu.with_extension(kind)
            parts.append("extension in " + ", ".join(p.name for p in ext) if ext else "extension not installed")
            live = [c for c in conns if c.kind == kind]
            if live:
                parts.append("connected: " + ", ".join(c.profile.name if c.profile else "a profile" for c in live))
            elif ext:
                parts.append("not connected" + ("" if browsers.running(kind) else " (not running)"))
        print(f"  {name:<24} " + " · ".join(parts))
    if not shown:
        print("  no Chromium browser is installed")
    st, why = _integration()
    if st == "off":
        print(_bold("Browser tools: turned off") + " (auren browser setup turns them on)")
        return 0
    try:
        target = obu.choose()
    except browsers.UnknownBrowser as e:
        print(_bold("Browser tools: ") + str(e))
        return 1
    missing = obu.not_set_up(target.kind, target.profile)
    if missing:
        print(_bold("Browser tools: not set up") + f" ({missing}): auren browser setup")
        return 1
    print(_bold("Browser tools use: ") + target.label() + (" (connected)" if target.conn else " (opened when needed)"))
    return 0


def _integration() -> tuple[str, str]:
    from . import integrations
    integrations._cache.clear()
    return integrations.state("browser")


def setup(name: str | None, yes: bool, open_store: bool, wait: float) -> int:
    try:
        kind = obu.setup_choice(name)
    except browsers.UnknownBrowser as e:
        print(f"auren browser setup: {e}", file=sys.stderr)
        return 1
    print(f"Setting up the browser tools in {_bold(kind.name)}.")
    if obu.cli() is None or not obu.registered(kind):
        print(f"This downloads Open Browser Use {obu.VERSION} (github.com/iFurySt/open-browser-use, checksum checked)\n"
              f"into {obu.own_binary().parent} and registers it with {kind.name}.")
        if not yes:
            if not sys.stdin.isatty():
                print("Run it with --yes to go ahead without asking.", file=sys.stderr)
                return 2
            if input("Go ahead? [Y/n] ").strip().lower() not in ("", "y", "yes"):
                print("Nothing changed.")
                return 1

    def waiting(opened: bool) -> None:
        if opened:
            print(f"\nThe Open Browser Use page is open in {kind.name}: click its Add button "
                  f"(\"Add to {kind.name.split()[-1]}\"), then confirm.")
        elif obu.with_extension(kind):
            print(f"\nThe extension is already in {kind.name}: restart {kind.name} (or turn the extension off and on) "
                  "so it connects to the newly registered host.")
        print(f"Waiting up to {wait:.0f} s for the extension to connect…", flush=True)

    print("Downloading Open Browser Use…" if obu.installed_version() != obu.VERSION else "", end="", flush=True)
    try:
        result = obu.setup(kind, open_store=open_store, wait=wait, on_wait=waiting)
    except obu.SetupError as e:
        print(f"auren browser setup: {e}", file=sys.stderr)
        return 1
    if settings.current()["integrations.browser"] is False:
        settings.save({"integrations.browser": "auto"})
        print("Turned the browser tools on (integrations.browser = auto).")
    print(result.summary())
    if result.connected:
        print(_bold("Done: ") + f"Auren's browser tools work in {result.connected.label()}. "
              "A running Auren offers them from its next fresh conversation (Super+Shift+A).")
        return 0
    if not obu.with_extension(kind):
        if not open_store:
            print(f"Add the extension in {kind.name}: {obu.STORE_URL}")
        print("The extension isn't added yet. Run `auren browser` to check later.")
        return 1 if wait > 0 else 0
    print(f"The extension is installed but hasn't connected: restart {kind.name}, then run `auren browser`.")
    return 1 if wait > 0 else 0


def use(name: str, profile: str | None) -> int:
    if name.lower() == "auto":
        settings.save({"browser.name": "auto", "browser.profile": "auto"})
        print("Auren chooses the browser again: a connected one, preferring your default browser.")
        return 0
    try:
        kind = browsers.resolve(name)
    except browsers.UnknownBrowser as e:
        print(f"auren browser use: {e}", file=sys.stderr)
        return 1
    if profile:
        p = obu.find_profile(kind, profile)
        if p is None:
            names = ", ".join(x.label() for x in obu.profiles(kind)) or "none yet"
            print(f"auren browser use: {kind.name} has no profile {profile!r} (profiles: {names})", file=sys.stderr)
            return 1
        profile = p.dir
    settings.save({"browser.name": kind.key, "browser.profile": profile or "auto"})
    print(f"The browser tools use {kind.name}" + (f", profile {profile}" if profile else "") + ".")
    why = obu.not_set_up(kind, obu.find_profile(kind, profile) if profile else None)
    if why:
        print(f"Not set up there yet ({why}): auren browser setup {kind.key}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="auren browser", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("status")
    s = sub.add_parser("setup")
    s.add_argument("browser", nargs="?")
    s.add_argument("--yes", "-y", action="store_true")
    s.add_argument("--no-open", action="store_true")
    s.add_argument("--wait", type=float, default=180)
    u = sub.add_parser("use")
    u.add_argument("browser")
    u.add_argument("profile", nargs="?")
    a = ap.parse_args(argv)
    if a.cmd in (None, "status"):
        return status()
    if a.cmd == "setup":
        return setup(a.browser, a.yes, not a.no_open, max(0.0, a.wait))
    return use(a.browser, a.profile)
