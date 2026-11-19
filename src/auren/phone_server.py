"""The phone remote's server, inside the daemon: HTTPS and a secure WebSocket on the PC's Tailscale address
and its private Wi-Fi/LAN addresses.

API version 1 (docs/phone.md has the whole protocol). Every call but /v1/hello and /v1/pair carries
`Authorization: Bearer <device token>`; a revoked token is refused at once, and open sockets of a revoked
phone are closed (code 4401).

    GET  /v1/hello                 who is this, where else it listens (and, with a token, whether it is still paired)
    POST /v1/pair                  {code, name} -> {token, device}: the one-time code from `auren phone pair`
    GET  /v1/inbox?after=N         what Auren sent to the phone after item N (the cursor)
    GET  /v1/inbox/{id}/file       an inbox item's image or file
    POST /v1/share                 from the phone's share sheet: ask Auren, the PC's clipboard, or open on the PC
    WS   /v1/ws                    the live link: state, replies, the inbox; requests, the microphone, Stop

There is no cleartext: the certificate is self-signed (phone.cert) and the app trusts only its fingerprint,
which it got from the pairing QR code. The server is never on 0.0.0.0: `supervise` binds it to `tailscale ip -4`
and to each private address of a real Wi-Fi or Ethernet interface (phone.addresses), follows them when they
change, says where it listens and why a path is missing in $XDG_RUNTIME_DIR/auren/phone.json (for
`auren doctor` and `auren phone pair`), and stops when there is nowhere to listen.
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
import uuid
from collections import OrderedDict, deque
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.websockets import WebSocket, WebSocketDisconnect

from . import phone
from .config import STATE_FILE

log = logging.getLogger("auren")
WATCH = 5.0                 # seconds between looks at the addresses (Wi-Fi changed, Tailscale came up)
RETRY = 30                  # seconds before a port that was taken is tried again
SWEEP = 1.0                 # seconds between checks for revoked phones and new inbox items
MAX_SHARE = 20 * 1024 * 1024
MAX_PAIR = 4096             # a pairing request is a code and a name
MAX_TEXT = 4000
PAIR_RATE = 10              # pairing attempts a minute, from anyone, before the rest get 429
SEEN = 500                  # request ids remembered, so a resent request doesn't run twice
REPLY_TTL = 600             # seconds a reply is kept for a phone that lost its connection before it came
REPLIES = 200
STATE_FIELDS = ("state", "detail", "since", "brain", "safety", "voice", "origin", "awaiting", "question", "until",
                "ask", "transcript", "reply", "silent", "tool", "remind", "fresh")


class TooBig(Exception):
    pass


async def _read_body(request: Request, limit: int) -> bytes:
    """The request's body, refused once it passes `limit` (whatever Content-Length says)."""
    if int(request.headers.get("content-length") or 0) > limit:
        raise TooBig
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise TooBig
    return bytes(body)


def _bearer(headers) -> str:
    auth = headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


class Conn:
    """One phone's open WebSocket."""

    def __init__(self, ws: WebSocket, device: dict):
        self.ws, self.device = ws, device
        self.out: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=1000)
        self.audio: asyncio.Queue | None = None      # the listen this phone feeds, if any
        self.close_code = 1000

    def send(self, msg: dict) -> None:
        try:
            self.out.put_nowait(msg)
        except asyncio.QueueFull:                       # a phone that stopped reading: drop it, it reconnects
            log.warning("phone %s isn't reading: closing", self.device["id"])
            with contextlib.suppress(asyncio.QueueFull):
                self.out.get_nowait()
                self.out.put_nowait(None)


class Requests:
    """What the phones asked, by device/request id. It outlives a server restart (a settings reload makes a new
    Hub), so a request resent after a lost ACK never runs twice and a reply waits for its phone across it."""

    def __init__(self):
        self.seen: OrderedDict[str, str] = OrderedDict()
        self.inflight: set[str] = set()                     # requests running now: a resend gets "in progress"
        self.replies: OrderedDict[str, tuple[float, dict]] = OrderedDict()   # device/rid -> the reply, for a resume


class Hub:
    """The phones connected now, and what the daemon tells them."""

    def __init__(self, daemon, requests: Requests | None = None):
        self.daemon = daemon
        self.conns: set[Conn] = set()
        requests = requests or Requests()
        self.seen, self.inflight, self.replies = requests.seen, requests.inflight, requests.replies
        self.paired = bool(phone.devices())
        self.inbox_last = phone.last_id()
        self.addrs: list[dict] = []                         # where the server listens now: [{"ip", "via"}]
        self.fp = ""
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
        """A phone turn's answer, to that phone only; kept a while, for a phone that reconnects without it."""
        self._deliver(turn, {"type": "reply", "id": turn.id, "device": turn.device, "text": text, "at": time.time()})

    def closed(self, turn) -> None:
        """A phone turn ended. If it got no reply (nothing heard, cancelled, an error), the phone hears that it
        ended, so it stops waiting."""
        if self._key({"id": turn.device}, turn.id) not in self.replies:
            self._deliver(turn, {"type": "ended", "id": turn.id})

    def _deliver(self, turn, msg: dict) -> None:
        key = self._key({"id": turn.device}, turn.id)
        if key:
            self.replies[key] = (time.monotonic(), msg)
            self.replies.move_to_end(key)
            self._expire()
        for c in list(self.conns):
            if c.device["id"] == turn.device:
                c.send(msg)

    def _expire(self) -> None:
        old = time.monotonic() - REPLY_TTL
        while self.replies and (len(self.replies) > REPLIES or next(iter(self.replies.values()))[0] < old):
            self.replies.popitem(last=False)

    def resume(self, conn: Conn, ids: list) -> None:
        """A reconnected phone names the requests it still waits for: each gets its reply, or "lost" when the
        PC has neither the reply nor the turn any more (so the phone never waits forever)."""
        self._expire()
        dev = conn.device["id"]
        for rid in [str(i)[:64] for i in ids if i][:100]:
            key = self._key(conn.device, rid)
            if key in self.replies:
                conn.send(self.replies[key][1])
            elif key not in self.inflight and not self.daemon.open_turn(dev, rid):
                conn.send({"type": "lost", "id": rid})

    def broadcast(self, msg: dict) -> None:
        for c in list(self.conns):
            c.send(msg)

    def close_all(self, code: int = 1012) -> None:
        """The server is stopping or moving: every phone's socket closes (it reconnects)."""
        for c in list(self.conns):
            c.close_code = code
            with contextlib.suppress(asyncio.QueueFull):
                c.out.put_nowait(None)

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

    def _again(self, key: str) -> str | None:
        """A request id seen before: how it went, or that it is still running."""
        if key in self.seen:
            return self.seen[key]
        if key and key in self.inflight:
            return "in progress"
        return None

    async def request(self, conn: Conn, msg: dict) -> dict | None:
        """One message from a phone. Returns the ack (None: nothing to answer)."""
        kind, rid, dev = msg.get("type"), str(msg.get("id") or "")[:64], conn.device
        key = self._key(dev, rid)
        if kind in ("ask", "talk.start", "answer"):
            if (before := self._again(key)) is not None:
                # A resend means the phone missed the ack, so it may have missed the reply too: send that
                # (or "lost") now rather than leave it waiting for the next reconnect.
                if kind != "answer":
                    self.resume(conn, [rid])
                return {"type": "ack", "id": rid, "status": before, "duplicate": True}
            if key:
                self.inflight.add(key)          # taken now, before anything awaits: a retry can't run it again
        try:
            return await self._request(conn, msg, kind, rid, dev, key)
        finally:
            self.inflight.discard(key)

    async def _request(self, conn: Conn, msg: dict, kind, rid: str, dev: dict, key: str) -> dict | None:
        d = self.daemon
        if kind == "ask":
            text = str(msg.get("text") or "").strip()[:MAX_TEXT]
            status = d.phone_ask(self._turn(rid, dev), text) if text else "error: nothing to ask"
        elif kind == "talk.start":
            audio: asyncio.Queue = asyncio.Queue()
            status = await d.phone_talk(self._turn(rid, dev), audio, bool(msg.get("handsFree")), bool(msg.get("fresh")))
            conn.audio = audio if status == "ok" else None
        elif kind == "talk.stop":
            status = await d.phone_talk_stop(dev["id"])
            if status != "not listening":
                conn.audio = None
        elif kind == "cancel":
            await d.cancel()
            status = "cancelled"
        elif kind == "answer":
            # Bound to the question the phone showed: a yes kept from before a reconnect approves nothing new.
            status = d.yes_no(bool(msg.get("yes")), self._turn(rid, dev), "phone", str(msg.get("ask") or ""))
        elif kind == "resume":
            ids = msg.get("ids")
            self.resume(conn, ids if isinstance(ids, list) else [])
            return None
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
        """Microphone frames: only into the listen this phone started (another phone's frames are dropped)."""
        listening, hearing = self.daemon.listening, getattr(self.daemon, "_hearing", None)
        if (conn.audio is not None and listening is not None and listening.audio is conn.audio
                and hearing is not None and hearing.device == conn.device["id"]):
            conn.audio.put_nowait(data)

    # --- the share sheet ----------------------------------------------------------------------

    async def share(self, device: dict, body: dict) -> dict:
        rid = str(body.get("id") or "")[:64]
        key = self._key(device, rid)
        if (before := self._again(key)) is not None:
            return {"status": before, "duplicate": True}
        if key:
            self.inflight.add(key)
        try:
            return await self._share_checked(device, rid, key, body)
        finally:
            self.inflight.discard(key)

    async def _share_checked(self, device: dict, rid: str, key: str, body: dict) -> dict:
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
            if kind != "image":
                ask = f"{note}\n{text}" if note else text
                return self.daemon.phone_ask(self._turn(rid, device), ask, source="phone-share")
            if self.daemon.busy_for("phone", device["id"]):
                return "busy"                                  # nothing is saved for a request that can't run
            path = await asyncio.to_thread(_save_download, data, mime)
            ask = f"(The user shared an image from their phone; it is saved at {path}.) {note or 'Here is an image.'}"
            status = self.daemon.phone_ask(self._turn(rid, device), ask, source="phone-share")
            if status != "ok":                                 # someone else got the turn meanwhile
                path.unlink(missing_ok=True)
            return status
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


def _downloads_dir() -> Path:
    try:
        out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        out = ""
    return Path(out) if out.startswith("/") and out != str(Path.home()) else Path.home() / "Downloads"


def _save_download(data: bytes, mime: str) -> Path:
    """Keep an image from the phone in the user's Downloads folder, under a name nothing else has."""
    folder = _downloads_dir()
    folder.mkdir(parents=True, exist_ok=True)
    ext = mimetypes.guess_extension(mime) or ".img"
    path = folder / f"phone-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}{ext}"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)     # never over an existing file
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def app(hub: Hub) -> Starlette:
    pairing_tries: deque[float] = deque()

    def device_of(request) -> dict | None:
        return phone.authenticate(_bearer(request.headers))

    def unauthorized() -> JSONResponse:
        return JSONResponse({"error": "not paired (or unpaired): pair again with auren phone pair"}, 401)

    def peer(request) -> str:
        return request.client.host if request.client else "?"

    def where() -> dict:
        return {"app": "auren", "api": phone.API, "pc": socket.gethostname(), "addrs": hub.addrs, "fp": hub.fp}

    async def hello(request: Request) -> Response:
        out = where()
        if _bearer(request.headers):
            dev = device_of(request)
            if not dev:
                return unauthorized()
            out["device"] = dev
        return JSONResponse(out)

    async def pair(request: Request) -> Response:
        now = time.monotonic()
        while pairing_tries and pairing_tries[0] < now - 60:
            pairing_tries.popleft()
        if len(pairing_tries) >= PAIR_RATE:
            log.warning("phone pairing refused from %s: too many attempts", peer(request))
            return JSONResponse({"error": "too many pairing attempts: wait a minute"}, 429)
        pairing_tries.append(now)
        if not phone.pairing_open():                 # nothing to pair with: the body isn't even read
            log.warning("phone pairing refused from %s: no pairing is open", peer(request))
            return JSONResponse({"error": "no pairing is open: run auren phone pair on the PC"}, 403)
        try:
            body = json.loads(await _read_body(request, MAX_PAIR))
            dev, token = await asyncio.to_thread(phone.pair, str(body.get("code") or ""), str(body.get("name") or ""))
        except TooBig:
            return JSONResponse({"error": "too big"}, 413)
        except phone.PhoneError as e:
            log.warning("phone pairing refused from %s: %s", peer(request), e)
            return JSONResponse({"error": str(e)}, 403)
        except (ValueError, AttributeError, ClientDisconnect):
            return JSONResponse({"error": "bad request"}, 400)
        log.info("phone paired from %s: %s (%s)", peer(request), dev["name"], dev["id"])
        hub._devices_stamp = hub._stamp(phone.DEVICES)
        hub._paired(True)
        return JSONResponse({"token": token, "device": dev, "pc": socket.gethostname(), "api": phone.API,
                             "addrs": hub.addrs, "fp": hub.fp})

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
        if not device_of(request):
            return unauthorized()
        try:
            raw = await _read_body(request, MAX_SHARE)
        except TooBig:
            return JSONResponse({"error": "too big"}, 413)
        except ClientDisconnect:
            return JSONResponse({"error": "bad request"}, 400)
        dev = device_of(request)                      # again: it may have been unpaired while the upload came
        if not dev:
            return unauthorized()
        try:
            body = json.loads(raw)
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
        conn.send({"type": "hello", **where(), "device": dev, "inboxLast": phone.last_id(), "state": hub.snapshot()})

        async def writer():
            while (msg := await conn.out.get()) is not None:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            await ws.close(4401 if dev["id"] not in {d["id"] for d in phone.devices()} else conn.close_code)

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


def _write_status(on: bool, port: int = 0, addrs: list[dict] | None = None, fp: str = "", why: str = "",
                  missing: dict | None = None) -> None:
    phone.STATUS.parent.mkdir(parents=True, exist_ok=True)
    tmp = phone.STATUS.with_name(f".{phone.STATUS.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"on": on, "port": port, "addrs": addrs or [], "fp": fp, "why": why,
                               "missing": missing or {}, "pid": os.getpid(), "at": time.time()}))
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


def _free(rows: list[dict], port: int) -> bool:
    """Has an address whose port was taken come free? (Then the server starts again to listen there too.)"""
    for r in rows:
        if not r["ok"]:
            with contextlib.suppress(OSError):
                _bind(r["ip"], port).close()
                return True
    return False


class _Running:
    """One uvicorn server over the sockets of the addresses it was started for."""

    def __init__(self, hub: Hub, socks: list[socket.socket], certfile: Path, keyfile: Path):
        config = uvicorn.Config(app(hub), log_level="warning", lifespan="off", ws_max_size=1 << 20,
                                ws_ping_interval=20, ws_ping_timeout=20, access_log=False,
                                ssl_certfile=str(certfile), ssl_keyfile=str(keyfile), timeout_graceful_shutdown=2)
        self.hub, self.socks, self.server = hub, socks, _Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=socks))

    async def up(self, timeout: float = 10) -> str:
        """Wait for uvicorn to start serving: "" once it does, else why not (the status says on only then)."""
        end = time.monotonic() + timeout
        while not self.server.started and not self.task.done() and time.monotonic() < end:
            await asyncio.sleep(0.02)
        if self.server.started:
            return ""
        if self.task.done() and not self.task.cancelled() and self.task.exception():
            return f"{type(self.task.exception()).__name__}: {self.task.exception()}"[:200]
        return "it didn't start in time" if not self.task.done() else "it stopped"

    async def stop(self) -> None:
        """Close the phones' sockets and the listening ones; wait (briefly) for uvicorn to wind down."""
        self.hub.close_all()
        self.server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 5)
        except (TimeoutError, Exception):  # noqa: BLE001 - stopping either way
            self.task.cancel()
            with contextlib.suppress(BaseException):
                await self.task
        for s in self.socks:
            s.close()


async def supervise(daemon) -> None:
    """Run the server on the Tailscale and private LAN addresses while there are any; follow them when they
    change. Cancelling it stops the server: the phones' sockets and the listening sockets close."""
    if getattr(daemon, "phone_requests", None) is None:
        daemon.phone_requests = Requests()
    hub = Hub(daemon, daemon.phone_requests)
    daemon.phone = hub
    sweeper = asyncio.create_task(hub.sweep())
    port = int(daemon.settings.get("phone.port", 8735))
    lan = daemon.settings.get("phone.lan", True) is not False
    running: _Running | None = None
    started_for, seen, tried, rows = None, None, 0.0, []
    try:
        certfile, keyfile, hub.fp = await asyncio.to_thread(phone.cert)
        while True:
            addrs, missing = await asyncio.to_thread(phone.addresses, lan)
            down = running is None or running.task.done()
            # A change counts once it is seen twice in a row: one failed Tailscale probe doesn't drop the LAN.
            moved = addrs != started_for and (down or addrs == seen)
            seen = addrs
            retry = time.monotonic() - tried >= RETRY and (down or _free(rows, port))
            if moved or retry:
                if running:
                    log.info("phone server: restarting on %s", ", ".join(ip for ip, _ in addrs) or "nothing")
                    await running.stop()
                    running = None
                    addrs, missing = await asyncio.to_thread(phone.addresses, lan)   # as they are now
                started_for, seen, tried = addrs, addrs, time.monotonic()
                socks, rows = [], []
                for ip, via in addrs:
                    try:
                        socks.append(_bind(ip, port))
                        rows.append({"ip": ip, "via": via, "ok": True})
                    except OSError as e:
                        rows.append({"ip": ip, "via": via, "ok": False,
                                     "why": f"can't listen on {ip}:{port} ({e.strerror}): set phone.port"})
                hub.addrs = [{"ip": r["ip"], "via": r["via"]} for r in rows if r["ok"]]
                why = "; ".join([r["why"] for r in rows if not r["ok"]] + list(missing.values()))
                if socks:
                    running = _Running(hub, socks, certfile, keyfile)
                    if err := await running.up():
                        log.warning("phone server didn't start: %s", err)
                        await running.stop()
                        running, hub.addrs = None, []
                        why, rows = f"the server didn't start: {err}", [{**r, "ok": False} for r in rows]
                    else:
                        log.info("phone server on %s, port %s (TLS)", ", ".join(a["ip"] for a in hub.addrs), port)
                _write_status(bool(hub.addrs), port, rows, hub.fp, "" if hub.addrs else why or "nowhere to listen",
                              missing)
            await asyncio.sleep(WATCH)
    finally:
        sweeper.cancel()
        if running:
            await running.stop()
        hub.close_all()
        if daemon.phone is hub:
            daemon.phone = None
        with contextlib.suppress(OSError):
            _write_status(False, port=port, why="the phone server stopped")
