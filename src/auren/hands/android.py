"""Phone control: Auren uses the paired Android phone the way a person does, through the app's
accessibility service (docs/phone.md#phone-control).

The phone reads its screen as text plus a numbered list of what can be tapped, typed into or scrolled,
like the browser tools read a page. Every action returns the screen after it. The phone does the work;
this side only sends one action at a time over the phone server's WebSocket and keeps the last list,
so a tap can be checked (its label) before it runs and named by words as well as by number.

`attach` is called by the daemon with a function that sends one action to the phone and waits for its
answer; without it (auren chat, tests) the tools say the phone isn't reachable.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

TIMEOUT = 20.0          # seconds an action may take on the phone (opening an app, a slow screen)
KEYS = ("back", "home", "recents", "notifications", "quick_settings", "lock")


class PhoneError(Exception):
    pass


_link: Callable[[dict, float], dict] | None = None
_items: dict[str, dict] = {}         # the last screen's numbered items: number -> {label, field, search}
_lock = threading.Lock()


def attach(link: Callable[[dict, float], dict] | None) -> None:
    global _link
    _link = link


def _call(action: str, **args) -> str:
    if _link is None:
        raise PhoneError("phone control isn't available here (it runs in the Auren daemon)")
    msg = {"action": action, **{k: v for k, v in args.items() if v not in (None, "")}}
    out = _link(msg, TIMEOUT)
    if not out.get("ok"):
        raise PhoneError(str(out.get("error") or "the phone couldn't do that"))
    items = out.get("items")
    if isinstance(items, list):
        with _lock:
            _items.clear()
            for it in items:
                if isinstance(it, dict) and it.get("n") is not None:
                    _items[str(it["n"])] = {"label": str(it.get("label") or ""), "field": bool(it.get("field")),
                                            "search": bool(it.get("search"))}
    return str(out.get("screen") or "")


def item(target: str) -> tuple[str, dict] | None:
    """The numbered item `target` names, from the last screen: its number, or words of its label (the first,
    best match: whole label, then words in it). None if the last screen has none."""
    t = target.strip()
    with _lock:
        if t.lstrip("[").rstrip("]") in _items:
            n = t.lstrip("[").rstrip("]")
            return n, _items[n]
        low = t.lower()
        for match in (lambda lab: lab == low, lambda lab: low in lab):
            for n, it in _items.items():
                if it["label"] and match(it["label"].lower()):
                    return n, it
    return None


def resolve(target: str) -> tuple[str, dict]:
    found = item(target)
    if not found:
        raise PhoneError(f"nothing called {target!r} on the last phone screen: read it again with phone_screen")
    return found


def screen() -> str:
    return _call("screen")


def tap(target: str, long: bool = False, expect: str | None = None) -> str:
    n, it = resolve(target)
    return _call("tap", n=int(n), long=long, expect=it["label"] if expect is None else expect)


def type_text(target: str, text: str, submit: bool = False, expect: str | None = None) -> str:
    n, it = resolve(target)
    if not it["field"]:
        raise PhoneError(f"item {n} ({it['label'] or 'no label'}) isn't a text field")
    return _call("type", n=int(n), text=text, submit=submit, expect=it["label"] if expect is None else expect)


def scroll(direction: str = "down", target: str = "") -> str:
    if direction not in ("down", "up", "left", "right"):
        raise PhoneError("direction is down, up, left or right")
    n = int(resolve(target)[0]) if target else None
    return _call("scroll", direction=direction, n=n)


def key(name: str) -> str:
    name = name.strip().lower().replace(" ", "_")
    if name not in KEYS:
        raise PhoneError(f"no key {name!r}: use {', '.join(KEYS)}")
    return _call("key", key=name)


def open_app(app: str) -> str:
    if not app.strip():
        raise PhoneError("say which app")
    return _call("open", app=app.strip())


def apps(query: str = "") -> str:
    return _call("apps", query=query.strip())
