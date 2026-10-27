"""Native messaging host for Auren Tabs. Stdout is reserved for browser protocol frames."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socketserver
import struct
import sys
import threading
import uuid
from pathlib import Path

from .hands import browsers, tabs

MAX_FRAME = 1_000_000
HOST_NAME = "com.auren.tabs"


def read_frame(stream) -> dict | None:
    def read_exact(count):
        chunks = bytearray()
        while len(chunks) < count:
            chunk = stream.read(count - len(chunks))
            if not chunk:
                if not chunks:
                    return None
                raise ValueError("truncated native message")
            chunks.extend(chunk)
        return bytes(chunks)

    header = read_exact(4)
    if header is None:
        return None
    size = struct.unpack("=I", header)[0]
    if not 0 < size <= MAX_FRAME:
        raise ValueError("invalid native message length")
    payload = read_exact(size)
    if payload is None:
        raise ValueError("truncated native message")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("native message must be an object")
    return value


class Relay:
    def __init__(self, output):
        self.output = output
        self.lock = threading.Lock()
        self.pending = {}

    def request(self, request):
        if request.get("method") not in {"list_tabs", "activate_tab"}:
            return {"error": "unsupported tab operation"}
        key = uuid.uuid4().hex
        event, result = threading.Event(), {}
        payload = json.dumps({**request, "id": key}).encode()
        with self.lock:
            self.pending[key] = (event, result)
            try:
                self.output.write(struct.pack("=I", len(payload)) + payload)
                self.output.flush()
            except OSError:
                self.pending.pop(key, None)
                return {"error": "browser disconnected"}
        ready = event.wait(6)
        with self.lock:
            self.pending.pop(key, None)
        return result if ready else {"error": "browser did not answer the tab operation"}

    def receive(self, message):
        with self.lock:
            item = self.pending.get(message.get("id"))
            if item:
                event, result = item
                result.update({k: message[k] for k in ("result", "error") if k in message})
                event.set()


def parent_browser() -> str:
    pid = os.getppid()
    for _ in range(12):
        proc = Path("/proc") / str(pid)
        name = (proc / "comm").read_text().strip()
        for kind in browsers.KINDS:
            if name in kind.processes:
                return kind.key
        pid = int(re.search(r"^PPid:\s+(\d+)", (proc / "status").read_text(), re.M).group(1))
        if pid <= 1:
            break
    raise ValueError("native host was not launched by a supported browser")


def serve(source, output, browser: str) -> None:
    hello = read_frame(source)
    if not hello or hello.get("event") != "hello":
        raise ValueError("missing extension handshake")
    relay = Relay(output)

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.connection.settimeout(8)
            try:
                request = json.loads(self.rfile.readline(MAX_FRAME + 1))
                reply = relay.request(request)
            except (ValueError, OSError, AttributeError):
                reply = {"error": "invalid tab request"}
            self.wfile.write(json.dumps(reply).encode() + b"\n")

    tabs.DIRECTORY.mkdir(parents=True, mode=0o700, exist_ok=True)
    stem = browser + "-" + uuid.uuid4().hex
    endpoint, registry = tabs.DIRECTORY / (stem + ".sock"), tabs.DIRECTORY / (stem + ".json")
    old_mask = os.umask(0o077)
    try:
        server = socketserver.ThreadingUnixStreamServer(str(endpoint), Handler)
        server.daemon_threads = True
        registry.write_text(json.dumps({"browser": browser, "pid": os.getpid()}))
    finally:
        os.umask(old_mask)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        while message := read_frame(source):
            relay.receive(message)
    finally:
        registry.unlink(missing_ok=True)
        server.shutdown()
        server.server_close()
        endpoint.unlink(missing_ok=True)


def install(extension: Path, executable: Path) -> list[Path]:
    import base64

    manifest = json.loads((extension / "manifest.json").read_text())
    digest = hashlib.sha256(base64.b64decode(manifest["key"], validate=True)).hexdigest()[:32]
    extension_id = "".join(chr(ord("a") + int(char, 16)) for char in digest)
    installed = []
    for kind in browsers.KINDS:
        base = browsers.config_dir(kind)
        if base is None:
            continue
        target = base / "NativeMessagingHosts" / (HOST_NAME + ".json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"name": HOST_NAME, "description": "Auren tab navigation",
                                      "path": str(executable), "type": "stdio",
                                      "allowed_origins": [f"chrome-extension://{extension_id}/"]}, indent=2))
        installed.append(target)
    return installed


def main() -> int:
    try:
        if len(sys.argv) == 4 and sys.argv[1] == "install":
            for path in install(Path(sys.argv[2]), Path(sys.argv[3])):
                print("Registered Auren Tabs native host:", path)
        else:
            serve(sys.stdin.buffer, sys.stdout.buffer, parent_browser())
    except (OSError, ValueError) as error:
        print(f"Auren Tabs: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
