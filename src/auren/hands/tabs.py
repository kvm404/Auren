"""Tab metadata and activation through Auren's optional, tab-only browser extension."""

from __future__ import annotations

import json
import logging
import os
import re
import socket
from urllib.parse import urlsplit

from ..config import RUNTIME_DIR
from . import browsers

DIRECTORY = RUNTIME_DIR / "tab-bridges"
log = logging.getLogger("auren.tabs")


class TabError(RuntimeError):
    pass


def connections() -> list[dict]:
    found = []
    for path in DIRECTORY.glob("*.json"):
        try:
            entry = json.loads(path.read_text())
            endpoint = DIRECTORY / (path.stem + ".sock")
            if entry["browser"] in browsers.BY_KEY and type(entry["pid"]) is int and entry["pid"] > 0 and endpoint.is_socket():
                os.kill(entry["pid"], 0)
                found.append({**entry, "socket": str(endpoint)})
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return found


def _connection(browser: str) -> dict:
    available = connections()
    if browser:
        try:
            key = browsers.resolve(browser).key
        except browsers.UnknownBrowser as err:
            raise TabError(str(err)) from None
        available = [item for item in available if item["browser"] == key]
    if not available:
        raise TabError(f"the Auren Tabs extension is not connected in {browser or 'a browser'}. "
                       "Connect it first; do not substitute screenshot typing or clicking.")
    if len(available) != 1:
        raise TabError("more than one browser or profile is connected. Name the browser; if its profiles "
                       "are ambiguous, connect Auren Tabs in only the intended profile")
    return available[0]


def _request(connection: dict, method: str, **params) -> dict:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(8)
            channel.connect(connection["socket"])
            channel.sendall(json.dumps({"method": method, **params}).encode() + b"\n")
            with channel.makefile("rb") as reader:
                raw = reader.readline(1_000_001)
            if len(raw) > 1_000_000:
                raise TabError("browser tab response is too large")
            reply = json.loads(raw)
    except (OSError, ValueError) as err:
        raise TabError(f"browser tab connection failed: {err}") from None
    if not isinstance(reply, dict) or "result" not in reply:
        raise TabError(str(reply.get("error", "invalid tab response")) if isinstance(reply, dict) else "invalid tab response")
    if not isinstance(reply["result"], dict):
        raise TabError("invalid tab response")
    return reply["result"]


def _matches(tab: dict, query: str) -> bool:
    words = re.findall(r"\w+", query.casefold())
    title = str(tab.get("title", "")).casefold()
    try:
        host = urlsplit(tab.get("url", "")).hostname or ""
    except ValueError:
        host = ""
    if "." in query.strip() and " " not in query.strip():
        try:
            wanted = urlsplit(query if "://" in query else "https://" + query).hostname
        except ValueError:
            return False
        return bool(wanted and (host.casefold() == wanted.casefold() or host.casefold().endswith("." + wanted.casefold())))
    searchable = title + " " + host.casefold()
    return all(re.search(rf"\b{re.escape(word)}\b", searchable) for word in words)


def list_tabs(query: str = "", browser: str = "") -> dict:
    connection = _connection(browser)
    result = _request(connection, "list_tabs")
    return {"browser": connection["browser"], "tabs": [tab for tab in result["tabs"] if _matches(tab, query)]}


def switch_tab(query: str, browser: str = "", tab_id: int = 0) -> dict:
    if not re.search(r"\w", query):
        raise TabError("give the tab's site or title")
    connection = _connection(browser)
    matches = [tab for tab in _request(connection, "list_tabs")["tabs"] if _matches(tab, query)]
    if tab_id:
        matches = [tab for tab in matches if tab["id"] == tab_id]
    if len(matches) != 1:
        return {"status": "ambiguous" if matches else "not_found", "browser": connection["browser"],
                "matches": matches}
    tab = matches[0]
    activated = _request(connection, "activate_tab", tabId=tab["id"], expectedUrl=tab["url"],
                         expectedWindowId=tab["windowId"])
    if activated.get("id") != tab["id"] or activated.get("active") is not True:
        raise TabError("the browser did not confirm that the selected tab is active")
    log.info("browser tab switched browser=%s tab=%s window=%s", connection["browser"], tab["id"], tab["windowId"])
    return {"status": "switched", "browser": connection["browser"], "tab": activated}
