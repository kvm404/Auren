"""auren's MCP adapter: the tools, as the brain's Claude Code (or Codex) process sees them.

Run by the brain:  python -m auren.server   (with AUREN_TOOLS and AUREN_SESSION in its environment)

It holds no state and runs nothing itself: each call goes to the tool runtime (auren.tools) on its
socket, in the daemon or in `auren chat`, so every brain shares the same tabs, screenshots and
confirmations. Names, arguments and descriptions come from auren.tools.
"""

from __future__ import annotations

import base64
import json
import os
import socket

from mcp.server.mcpserver import Image, MCPServer

from . import tools
from .config import TOOLS_SOCKET

SOCKET = os.environ.get("AUREN_TOOLS") or str(TOOLS_SOCKET)
SESSION = os.environ.get("AUREN_SESSION") or "daemon"

server = MCPServer(
    "auren",
    instructions="Tools for acting on the user's Omarchy/Hyprland laptop: apps, windows, herdr coding agents, "
                 "shell commands, screenshots, sound, media, reminders and system information.",
)


def forward(name: str, args: dict) -> list:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.connect(SOCKET)
            s.sendall(json.dumps({"session": SESSION, "tool": name, "args": args}).encode() + b"\n")
            with s.makefile("rb") as f:
                reply = json.loads(f.readline())
    except (OSError, ValueError) as e:
        return [f"error: Auren's tools aren't reachable ({e}); is the auren daemon running?"]
    return [Image(data=base64.b64decode(c["data"]), format=c.get("format", "jpeg")) if c.get("type") == "image"
            else c.get("text", "") for c in reply.get("content", [])]


def _register(name: str) -> None:
    def call(**kwargs):
        out = forward(name, kwargs)
        return out[0] if len(out) == 1 and isinstance(out[0], str) else out

    call.__name__ = name
    call.__doc__ = tools.TOOLS[name].fn.__doc__
    call.__signature__ = tools.signature(name)
    server.tool()(call)


for _name in tools.TOOLS:
    _register(_name)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
