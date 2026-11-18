"""The phone remote's server, inside the daemon: HTTP and a WebSocket on the Tailscale address only.

API version 1 (docs/phone.md has the whole protocol). Every call but /v1/hello and /v1/pair carries
`Authorization: Bearer <device token>`; a revoked token is refused at once, and open sockets of a revoked
phone are closed (code 4401).

    GET  /v1/hello                 who is this (and, with a token, whether it is still paired)
    POST /v1/pair                  {code, name} -> {token, device}: the one-time code from `auren phone pair`
    GET  /v1/inbox?after=N         what Auren sent to the phone after item N (the cursor)
    GET  /v1/inbox/{id}/file       an inbox item's image or file
    POST /v1/share                 from the phone's share sheet: ask Auren, the PC's clipboard, or open on the PC
    WS   /v1/ws                    the live link: state, replies, the inbox; requests, the microphone, Stop

The server is never on 0.0.0.0: `supervise` binds it to `tailscale ip -4`, and when Tailscale is down it
stays off, says why in $XDG_RUNTIME_DIR/auren/phone.json (for `auren doctor` and `auren phone pair`), and
tries again every RETRY seconds.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import mimetypes
import os
import socket
import subprocess
import time
from collections import OrderedDict
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import phone
from .config import STATE_FILE

log = logging.getLogger("auren")
RETRY = 30                  # seconds between looks at Tailscale
SWEEP = 1.0                 # seconds between checks for revoked phones and new inbox items
MAX_SHARE = 20 * 1024 * 1024
MAX_TEXT = 4000
SEEN = 500                  # request ids remembered, so a resent request doesn't run twice
STATE_FIELDS = ("state", "detail", "since", "brain", "safety", "voice", "origin", "awaiting", "question", "until",
                "transcript", "reply", "silent", "tool", "remind", "fresh")


def _bearer(headers) -> str:
    auth = headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


class Conn:
    """One phone's open WebSocket."""

    def __init__(self, ws: WebSocket, device: dict):
        self.ws, self.device = ws, device
        self.out: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
        self.audio: asyncio.Queue | None = None      # the listen this phone feeds, if any

    def send(self, msg: dict) -> None:
        try:
            self.out.put_nowait(msg)
        except asyncio.QueueFull:                       # a phone that stopped reading: drop it, it reconnects
            log.warning("phone %s isn't reading: closing", self.device["id"])
            with contextlib.suppress(asyncio.QueueFull):
                self.out.get_nowait()
                self.out.put_nowait(None)


class Hub:
    """The phones connected now, and what the daemon tells them."""

    def __init__(self, daemon):
        self.daemon = daemon
        self.conns: set[Conn] = set()
        self.seen: OrderedDict[str, str] = OrderedDict()
        self.paired = bool(phone.devices())
        self.inbox_last = phone.last_id()
        self._devices_stamp = self._stamp(phone.DEVICES)
        self._inbox_stamp = self._stamp(phone.INBOX)
        self._flush = False

    # --- what the daemon calls ----------------------------------------------------------------

    def state_changed(self) -> None:
        """The state changed: every phone gets it (once per loop pass, however often it changed)."""
        if self.conns and not self._flush:
            self._flush = True
            asyncio.get_running_loop().call_soon(self._send_state)

    def _send_state(self) -> None:
        self._flush = False
        self.broadcast({"type": "state", **self.snapshot()})

    def snapshot(self) -> dict:
        try:
            st = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            st = {"state": getattr(self.daemon, "state", "starting")}
        out = {k: st[k] for k in STATE_FIELDS if k in st}
        out["origin"] = self.daemon._origin()
        return out

    def reply(self, turn, text: str) -> None:
        self.broadcast({"type": "reply", "id": turn.id, "device": turn.device, "text": text, "at": time.time()})

    def broadcast(self, msg: dict) -> None:
        for c in list(self.conns):
            c.send(msg)

    # --- requests -----------------------------------------------------------------------------

    def _turn(self, rid: str, device: dict):
        from .daemon import Turn
        return Turn("phone", rid, device["id"])

    @staticmethod
    def _key(device: dict, rid: str) -> str:
        """Request ids are each phone's own: the same id from another phone is another request."""
        return f"{device['id']}/{rid}" if rid else ""

    def _remember(self, key: str, status: str) -> None:
        if key:
            self.seen[key] = status
            while len(self.seen) > SEEN:
                self.seen.popitem(last=False)

    async def request(self, conn: Conn, msg: dict) -> dict | None:
        """One message from a phone. Returns the ack (None: nothing to answer)."""
        kind, rid, dev = msg.get("type"), str(msg.get("id") or "")[:64], conn.device
        key = self._key(dev, rid)
        if kind in ("ask", "talk.start", "answer") and key in self.seen:
            return {"type": "ack", "id": rid, "status": self.seen[key], "duplicate": True}
        d = self.daemon
        if kind == "ask":
            text = str(msg.get("text") or "").strip()[:MAX_TEXT]
            status = d.phone_ask(self._turn(rid, dev), text) if text else "error: nothing to ask"
        elif kind == "talk.start":
            audio: asyncio.Queue = asyncio.Queue()
            status = await d.phone_talk(self._turn(rid, dev), audio, bool(msg.get("handsFree")), bool(msg.get("fresh")))
            conn.audio = audio if status == "ok" else None
        elif kind == "talk.stop":
            conn.audio = None
            status = await d.phone_talk_stop(dev["id"])
        elif kind == "cancel":
            await d.cancel()
            status = "cancelled"
        elif kind == "answer":
            status = d.yes_no(bool(msg.get("yes")), self._turn(rid, dev), "phone")
        elif kind == "sync":
            after = int(msg.get("after") or 0)
            for it in phone.items(after, 200):
                conn.send({"type": "inbox", "item": phone.shown(it)})
            return None
        elif kind == "ping":
            return {"type": "pong", "id": rid}
        else:
            return {"type": "error", "id": rid, "error": f"unknown message {kind!r}"}
        if status == "ok" or status.startswith(("approved", "dropped")):
            self._remember(key, status)
        ack = {"type": "ack", "id": rid, "status": status}
        if status == "busy":
            conn.send({"type": "busy", "id": rid, "owner": d._origin() or "desk"})
        return ack

    def audio(self, conn: Conn, data: bytes) -> None:
        listening = self.daemon.listening
        if conn.audio is not None and listening is not None and listening.audio is conn.audio:
            conn.audio.put_nowait(data)

    # --- the share sheet ----------------------------------------------------------------------

    async def share(self, device: dict, body: dict) -> dict:
        rid = str(body.get("id") or "")[:64]
        key = self._key(device, rid)
        if key in self.seen:
            return {"status": self.seen[key], "duplicate": True}
        kind, action = body.get("kind"), body.get("action")
        text = str(body.get("text") or "").strip()[:MAX_TEXT]
        note = str(body.get("note") or "").strip()[:MAX_TEXT]
        if kind not in ("text", "link", "image") or action not in ("ask", "clipboard", "open"):
            return {"status": "error: kind is text, link or image; action is ask, clipboard or open"}
        data, mime = b"", str(body.get("mime") or "image/png")
        if kind == "image":
            try:
                data = base64.b64decode(str(body.get("data") or ""), validate=True)
            except ValueError:
                return {"status": "error: the image isn't base64"}
            if not data or not mime.startswith("image/"):
                return {"status": "error: no image"}
        elif not text:
            return {"status": "error: nothing was shared"}
        if kind == "link" and not text.startswith(("http://", "https://")):
            return {"status": "error: a link is an http(s) URL"}
        try:
            status = await self._share(device, rid, kind, action, text, note, data, mime)
        except (OSError, subprocess.SubprocessError, ValueError) as e:
            return {"status": f"error: {e}"[:200]}
        if status == "ok":
            self._remember(key, status)
        return {"status": status}

    async def _share(self, device, rid, kind, action, text, note, data, mime) -> str:
        from .hands import desktop
        if action == "ask":
            if kind == "image":
                path = await asyncio.to_thread(_save_download, data, mime)
                ask = f"(The user shared an image from their phone; it is saved at {path}.) {note or 'Here is an image.'}"
            else:
                ask = f"{note}\n{text}" if note else text
            return self.daemon.phone_ask(self._turn(rid, device), ask, source="phone-share")
        if action == "clipboard":
            if kind == "image":
                await asyncio.to_thread(subprocess.run, ["wl-copy", "--type", mime], input=data, check=True, timeout=5)
            else:
                await asyncio.to_thread(subprocess.run, ["wl-copy"], input=text.encode(), check=True, timeout=5)
            return "ok"
        # open on the PC
        if kind == "link":
            await asyncio.to_thread(desktop.open_url, text)
        elif kind == "image":
            path = await asyncio.to_thread(_save_download, data, mime)
            await asyncio.to_thread(desktop.open_path, str(path))
        else:
            await asyncio.to_thread(desktop.notify, f"From your phone: {text}")
        return "ok"

    # --- upkeep -------------------------------------------------------------------------------

    @staticmethod
    def _stamp(path: Path):
        try:
            st = path.stat()
        except OSError:
            return None
        return st.st_mtime_ns, st.st_size

    async def sweep(self) -> None:
        """Close revoked phones' sockets, push new inbox items, and notice a first pairing or a last revoke
        (also when `auren phone revoke` or an `auren chat` changed the files)."""
        while True:
            await asyncio.sleep(SWEEP)
            try:
                self.check()
            except Exception:  # noqa: BLE001 - keep sweeping
                log.exception("phone sweep failed")

    def check(self) -> None:
        stamp = self._stamp(phone.DEVICES)
        if stamp != self._devices_stamp:
            self._devices_stamp = stamp
            ids = {d["id"] for d in phone.devices()}
            for c in list(self.conns):
                if c.device["id"] not in ids:
                    log.info("phone %s was unpaired: closing its connection", c.device["id"])
                    c.send({"type": "revoked"})
                    c.out.put_nowait(None)
            self._paired(bool(ids))
        stamp = self._stamp(phone.INBOX)
        if stamp != self._inbox_stamp:
            self._inbox_stamp = stamp
            new = phone.items(self.inbox_last, 200)
            for it in new:
                self.broadcast({"type": "inbox", "item": phone.shown(it)})
            if new:
                self.inbox_last = new[-1]["id"]

    def _paired(self, paired: bool) -> None:
        if paired != self.paired:
            self.paired = paired
            from . import integrations
            integrations._cache.pop("phone", None)
            asyncio.get_running_loop().create_task(self.daemon.phone_paired_changed())


def _save_download(data: bytes, mime: str) -> Path:
    """Keep an image from the phone in the user's Downloads folder."""
    try:
        out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    folder = Path(out) if out.startswith("/") and out != str(Path.home()) else Path.home() / "Downloads"
    folder.mkdir(parents=True, exist_ok=True)
    ext = mimetypes.guess_extension(mime) or ".img"
    path = folder / f"phone-{time.strftime('%Y%m%d-%H%M%S')}{ext}"
    path.write_bytes(data)
    return path


def app(hub: Hub) -> Starlette:
    def device_of(request) -> dict | None:
        return phone.authenticate(_bearer(request.headers))

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": "not paired (or unpaired): pair again with auren phone pair"}, 401)

    async def hello(request: Request) -> Response:
        out = {"app": "auren", "api": phone.API, "pc": socket.gethostname()}
        if _bearer(request.headers):
            dev = device_of(request)
            if not dev:
                return unauthorized()
            out["device"] = dev
        return JSONResponse(out)

    async def pair(request: Request) -> Response:
        try:
            body = await request.json()
            dev, token = await asyncio.to_thread(phone.pair, str(body.get("code") or ""), str(body.get("name") or ""))
        except phone.PhoneError as e:
            return JSONResponse({"error": str(e)}, 403)
        except (ValueError, AttributeError):
            return JSONResponse({"error": "bad request"}, 400)
        log.info("phone paired: %s (%s)", dev["name"], dev["id"])
        hub._devices_stamp = hub._stamp(phone.DEVICES)
        hub._paired(True)
        return JSONResponse({"token": token, "device": dev, "pc": socket.gethostname(), "api": phone.API})

    async def inbox(request: Request) -> Response:
        if not device_of(request):
            return unauthorized()
        try:
            after = int(request.query_params.get("after", 0))
            limit = max(1, min(200, int(request.query_params.get("limit", 100))))
        except ValueError:
            return JSONResponse({"error": "after and limit are numbers"}, 400)
        found = phone.items(after, limit)
        return JSONResponse({"items": [phone.shown(it) for it in found], "last": phone.last_id(),
                             "next": found[-1]["id"] if found else after})

    async def inbox_file(request: Request) -> Response:
        if not device_of(request):
            return unauthorized()
        it = phone.item(int(request.path_params["item"]))
        path = phone.file_of(it) if it else None
        if not path:
            return JSONResponse({"error": "no such file"}, 404)
        return FileResponse(path, media_type=it["file"]["mime"], filename=it["file"]["name"])

    async def share(request: Request) -> Response:
        dev = device_of(request)
        if not dev:
            return unauthorized()
        if int(request.headers.get("content-length") or 0) > MAX_SHARE:
            return JSONResponse({"error": "too big"}, 413)
        try:
            body = json.loads(await request.body())
        except ValueError:
            return JSONResponse({"error": "bad request"}, 400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "bad request"}, 400)
        out = await hub.share(dev, body)
        return JSONResponse(out, 409 if out["status"] == "busy" else 200)

    async def ws_endpoint(ws: WebSocket) -> None:
        dev = phone.authenticate(_bearer(ws.headers))
        if not dev:
            await ws.send_denial_response(unauthorized())
            return
        await ws.accept()
        conn = Conn(ws, dev)
        hub.conns.add(conn)
        await asyncio.to_thread(phone.seen, dev["id"])
        log.info("phone connected: %s (%s)", dev["name"], dev["id"])
        conn.send({"type": "hello", "api": phone.API, "pc": socket.gethostname(), "device": dev,
                   "inboxLast": phone.last_id(), "state": hub.snapshot()})

        async def writer():
            while (msg := await conn.out.get()) is not None:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            await ws.close(4401 if dev["id"] not in {d["id"] for d in phone.devices()} else 1000)

        out = asyncio.create_task(writer())
        pending: set[asyncio.Task] = set()
        try:
            while True:
                event = await ws.receive()
                if event["type"] == "websocket.disconnect":
                    break
                if event.get("bytes") is not None:
                    hub.audio(conn, event["bytes"])
                    continue
                if not phone.authenticate(_bearer(ws.headers)):        # revoked since it connected
                    conn.send({"type": "revoked"})
                    conn.out.put_nowait(None)
                    break
                try:
                    msg = json.loads(event.get("text") or "")
                except ValueError:
                    conn.send({"type": "error", "error": "not JSON"})
                    continue
                if not isinstance(msg, dict):
                    continue

                async def run(m=msg):
                    try:
                        ack = await hub.request(conn, m)
                    except Exception as e:  # noqa: BLE001 - one bad request doesn't drop the phone
                        log.exception("phone request failed")
                        ack = {"type": "ack", "id": str(m.get("id") or ""), "status": f"error: {e}"[:200]}
                    if ack:
                        conn.send(ack)

                # Stopping a listen waits for the transcript: other messages (Stop) shouldn't wait behind it.
                if msg.get("type") == "talk.stop":
                    t = asyncio.create_task(run())
                    pending.add(t)
                    t.add_done_callback(pending.discard)
                else:
                    await run()
        except WebSocketDisconnect:
            pass
        finally:
            hub.conns.discard(conn)
            log.info("phone disconnected: %s", dev["id"])
            with contextlib.suppress(asyncio.QueueFull):
                conn.out.put_nowait(None)
            # Let the writer flush what's queued (a "revoked") and close; a gone phone fails it at once.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(out, 2)
            out.cancel()
            await hub.daemon.phone_gone(dev["id"])

    return Starlette(routes=[
        Route("/v1/hello", hello),
        Route("/v1/pair", pair, methods=["POST"]),
        Route("/v1/inbox", inbox),
        Route("/v1/inbox/{item:int}/file", inbox_file),
        Route("/v1/share", share, methods=["POST"]),
        WebSocketRoute("/v1/ws", ws_endpoint),
    ])


class _Server(uvicorn.Server):
    """uvicorn without its signal handlers: the daemon keeps its own (Ctrl+C, systemd's stop)."""

    @contextlib.contextmanager
    def capture_signals(self):
        yield


def _write_status(on: bool, host: str = "", port: int = 0, why: str = "") -> None:
    phone.STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = phone.STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"on": on, "host": host, "port": port, "why": why, "pid": os.getpid(), "at": time.time()}))
    tmp.replace(phone.STATUS)


def _bind(host: str, port: int) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError:
        sock.close()
        raise
    sock.listen(16)
    sock.setblocking(False)
    return sock


async def supervise(daemon) -> None:
    """Run the server on the Tailscale address while there is one; follow it when it changes."""
    hub = Hub(daemon)
    daemon.phone = hub
    sweeper = asyncio.create_task(hub.sweep())
    port = int(daemon.settings.get("phone.port", 8735))
    try:
        while True:
            host, why = await asyncio.to_thread(phone.tailscale_ip)
            if not host:
                _write_status(False, port=port, why=why)
                await asyncio.sleep(RETRY)
                continue
            try:
                sock = _bind(host, port)
            except OSError as e:
                _write_status(False, host, port, f"can't listen on {host}:{port} ({e.strerror}): set phone.port")
                await asyncio.sleep(RETRY)
                continue
            config = uvicorn.Config(app(hub), log_level="warning", lifespan="off", ws_max_size=1 << 20,
                                    ws_ping_interval=20, ws_ping_timeout=20, access_log=False)
            server = _Server(config)
            task = asyncio.create_task(server.serve(sockets=[sock]))
            _write_status(True, host, port)
            log.info("phone server on %s:%s", host, port)
            while not task.done():
                await asyncio.sleep(RETRY)
                now, _ = await asyncio.to_thread(phone.tailscale_ip)
                if now != host:
                    log.info("Tailscale address changed (%s -> %s): restarting the phone server", host, now)
                    server.should_exit = True
                    break
            with contextlib.suppress(Exception):
                await task
            sock.close()
            _write_status(False, port=port, why="restarting")
    finally:
        sweeper.cancel()
        with contextlib.suppress(OSError):
            _write_status(False, port=port, why="the daemon stopped")
