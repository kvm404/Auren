"""The phone remote's records on the PC: paired devices, the one-time pairing code, and the inbox.

    auren phone pair            # a QR code (and its text) for the app; the code works once, for 5 minutes
    auren phone devices         # the paired phones
    auren phone revoke <id>     # unpair one: its token stops working at once

Everything lives in STATE_DIR/phone (mode 700): devices.json and pairing.json (mode 600) hold only SHA-256
hashes of the tokens and of the code, never the secrets themselves. The inbox (inbox.jsonl, files/) is what
Auren sent to the phone; its item ids only grow, so a phone that was away asks for what came after the last
one it has (the cursor). The server itself is in phone_server.py and runs inside the daemon, on the
Tailscale address only (see docs/phone.md).
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import ipaddress
import json
import mimetypes
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

from .config import RUNTIME_DIR, STATE_DIR

API = 1                             # the protocol version, in every path (/v1/...)
PHONE_DIR = STATE_DIR / "phone"
DEVICES = PHONE_DIR / "devices.json"
PAIRING = PHONE_DIR / "pairing.json"
INBOX = PHONE_DIR / "inbox.jsonl"
FILES = PHONE_DIR / "files"
STATUS = RUNTIME_DIR / "phone.json"     # the daemon's server: on or off, where, and why not
PAIR_TTL = 300                      # seconds a pairing code works
PAIR_TRIES = 5                      # wrong codes before the pairing is dropped
MAX_FILE = 50 * 1024 * 1024         # biggest file send_to_phone sends
KEEP = 200                          # inbox items kept (older ones and their files go)
KINDS = ("text", "link", "image", "file")
TAILNET = ipaddress.ip_network("100.64.0.0/10")    # Tailscale's addresses (CGNAT range)


class PhoneError(Exception):
    pass


# --- private files ------------------------------------------------------------------------------

def _dir(path: Path | None = None) -> Path:
    path = path or PHONE_DIR
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _write(path: Path, data: str | bytes) -> None:
    """Write atomically, mode 600 from the start."""
    _dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data.encode() if isinstance(data, str) else data)
    os.replace(tmp, path)


def _read(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


@contextlib.contextmanager
def _locked(name: str):
    """One writer at a time across processes (the daemon and `auren phone ...`)."""
    _dir()
    fd = os.open(PHONE_DIR / f".{name}.lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# --- devices ------------------------------------------------------------------------------------

def devices() -> list[dict]:
    data = _read(DEVICES, [])
    return [d for d in data if isinstance(d, dict) and d.get("id") and d.get("hash")] if isinstance(data, list) else []


def _save_devices(items: list[dict]) -> None:
    _write(DEVICES, json.dumps(items, indent=1))


def start_pairing(ttl: float = PAIR_TTL) -> tuple[str, float]:
    """A new one-time code (any earlier one stops working). Returns (code, expires at)."""
    code = secrets.token_urlsafe(12)
    expires = time.time() + ttl
    with _locked("devices"):
        _write(PAIRING, json.dumps({"hash": _hash(code), "expires": expires, "tries": 0}))
    return code, expires


def pairing_open() -> bool:
    p = _read(PAIRING, {})
    return isinstance(p, dict) and p.get("expires", 0) > time.time()


def pair(code: str, name: str) -> tuple[dict, str]:
    """Trade a pairing code for a device token. The code works once; PAIR_TRIES wrong ones drop it."""
    name = re.sub(r"[^\w .,'()+-]", "", str(name or ""))[:40].strip() or "Phone"
    with _locked("devices"):
        p = _read(PAIRING, {})
        if not isinstance(p, dict) or not p.get("hash"):
            raise PhoneError("no pairing is open: run auren phone pair on the PC")
        if p.get("expires", 0) <= time.time():
            PAIRING.unlink(missing_ok=True)
            raise PhoneError("the pairing code expired: run auren phone pair again")
        if not hmac.compare_digest(p["hash"], _hash(str(code))):
            p["tries"] = p.get("tries", 0) + 1
            if p["tries"] >= PAIR_TRIES:
                PAIRING.unlink(missing_ok=True)
            else:
                _write(PAIRING, json.dumps(p))
            raise PhoneError("wrong pairing code")
        PAIRING.unlink(missing_ok=True)
        token = secrets.token_urlsafe(32)
        items = devices()
        ids = {d["id"] for d in items}
        while (dev_id := secrets.token_hex(3)) in ids:
            pass
        device = {"id": dev_id, "name": name, "hash": _hash(token), "created": time.time(), "lastSeen": 0}
        _save_devices([*items, device])
    return public(device), token


def authenticate(token: str) -> dict | None:
    """The device this token belongs to, or None (unknown or revoked)."""
    if not token:
        return None
    h = _hash(token)
    found = None
    for d in devices():
        if hmac.compare_digest(d["hash"], h):
            found = d
    return public(found) if found else None


def seen(dev_id: str) -> None:
    with _locked("devices"):
        items = devices()
        for d in items:
            if d["id"] == dev_id:
                d["lastSeen"] = time.time()
        _save_devices(items)


def revoke(dev_id: str) -> dict | None:
    with _locked("devices"):
        items = devices()
        gone = next((d for d in items if d["id"] == dev_id), None)
        if gone:
            _save_devices([d for d in items if d["id"] != dev_id])
    return public(gone) if gone else None


def public(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "hash"}


# --- the inbox ----------------------------------------------------------------------------------

def items(after: int = 0, limit: int = 100) -> list[dict]:
    """Inbox items with an id above the cursor, oldest first."""
    out = []
    try:
        lines = INBOX.read_text().splitlines()
    except OSError:
        return []
    for line in lines:
        try:
            it = json.loads(line)
        except ValueError:
            continue
        if isinstance(it, dict) and isinstance(it.get("id"), int) and it["id"] > after:
            out.append(it)
    return out[:limit]


def last_id() -> int:
    return max((it["id"] for it in items(limit=10**9)), default=0)


def item(item_id: int) -> dict | None:
    return next((it for it in items(item_id - 1, 1) if it["id"] == item_id), None)


def file_of(it: dict) -> Path | None:
    name = (it.get("file") or {}).get("stored")
    path = FILES / name if name else None
    return path if path and path.is_file() else None


def add(kind: str, title: str = "", content: str = "", path: str = "") -> dict:
    """Put something in the phone's inbox: text, a link, or a copy of an image or file."""
    if kind not in KINDS:
        raise ValueError("kind is text, link, image or file")
    entry: dict = {"kind": kind, "title": str(title or "")[:200], "at": time.time()}
    src = None
    if kind == "text":
        if not content.strip():
            raise ValueError("nothing to send: content is empty")
        entry["text"] = content[:20000]
    elif kind == "link":
        url = content.strip()
        if not re.match(r"^https?://\S+$", url):
            raise ValueError("a link is an http(s) URL in content")
        entry["url"] = url
    else:
        src = Path(os.path.expanduser(path or content)).resolve()
        if not src.is_file():
            raise ValueError(f"no such file: {path or content}")
        size = src.stat().st_size
        if size > MAX_FILE:
            raise ValueError(f"the file is too big for the phone ({size // 2**20} MB, at most {MAX_FILE // 2**20} MB)")
        mime = mimetypes.guess_type(src.name)[0] or "application/octet-stream"
        if kind == "image" and not mime.startswith("image/"):
            raise ValueError(f"{src.name} isn't an image: send it as a file")
        entry["file"] = {"name": src.name, "size": size, "mime": mime}
    with _locked("inbox"):
        entry["id"] = last_id() + 1
        if src:
            stored = f"{entry['id']}-{re.sub(r'[^\w.-]', '_', src.name)[-80:]}"
            _dir(FILES)
            tmp = FILES / f".{stored}.tmp"
            shutil.copyfile(src, tmp)
            os.chmod(tmp, 0o600)
            os.replace(tmp, FILES / stored)
            entry["file"]["stored"] = stored
        _dir()
        fd = os.open(INBOX, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        _trim()
    return entry


def _trim() -> None:
    kept = items(limit=10**9)
    if len(kept) <= 2 * KEEP:
        return
    old, kept = kept[:-KEEP], kept[-KEEP:]
    _write(INBOX, "".join(json.dumps(it, ensure_ascii=False) + "\n" for it in kept))
    for it in old:
        if p := file_of(it):
            p.unlink(missing_ok=True)


def shown(it: dict) -> dict:
    """An item as the phone gets it (without where the PC keeps its file)."""
    out = {k: v for k, v in it.items() if k != "file"}
    if it.get("file"):
        out["file"] = {k: v for k, v in it["file"].items() if k != "stored"}
    return out


# --- the network --------------------------------------------------------------------------------

def tailscale_ip() -> tuple[str | None, str]:
    """This machine's Tailscale IPv4 address, or (None, why not)."""
    if not shutil.which("tailscale"):
        return None, "Tailscale isn't installed"
    try:
        out = subprocess.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"tailscale didn't answer ({type(e).__name__})"
    for line in out.stdout.split():
        try:
            ip = ipaddress.ip_address(line.strip())
        except ValueError:
            continue
        if ip.version == 4 and ip in TAILNET:
            return str(ip), ""
    why = (out.stderr or out.stdout).strip().splitlines()
    return None, "Tailscale is off or logged out" + (f" ({why[0][:80]})" if why else "") + ": tailscale up"


def pair_uri(host: str, port: int, code: str) -> str:
    from urllib.parse import urlencode
    return "auren://pair?" + urlencode({"v": API, "host": host, "port": port, "code": code, "pc": socket.gethostname()})


def status() -> dict:
    """What the daemon wrote about its server: {"on": bool, "host", "port", "why"}."""
    data = _read(STATUS, {})
    return data if isinstance(data, dict) else {}


# --- the CLI ------------------------------------------------------------------------------------

def _ago(t: float) -> str:
    if not t:
        return "never"
    s = time.time() - t
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else \
        f"{int(s // 3600)} h ago" if s < 86400 else time.strftime("%Y-%m-%d", time.localtime(t))


def _qr(text: str) -> str:
    import io

    import segno
    buf = io.StringIO()
    segno.make(text, error="m").terminal(out=buf, compact=True, border=2)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="auren phone", description="Pair the Auren phone app with this PC.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="show a one-time QR code for the app (works for 5 minutes)")
    p.add_argument("--no-wait", action="store_true", help="print the code and return, don't wait for the phone")
    p.add_argument("--no-qr", action="store_true", help="only the text to paste into the app")
    sub.add_parser("devices", help="list paired phones")
    r = sub.add_parser("revoke", help="unpair a phone (its id from auren phone devices)")
    r.add_argument("id")
    a = ap.parse_args(argv)

    if a.cmd == "devices":
        found = devices()
        if not found:
            print("No phone is paired. Pair one: auren phone pair")
        for d in found:
            print(f"{d['id']}  {d['name']:<24} paired {_ago(d.get('created', 0))}, last seen {_ago(d.get('lastSeen', 0))}")
        return 0
    if a.cmd == "revoke":
        gone = revoke(a.id)
        if not gone:
            print(f"No paired phone has the id {a.id} (see auren phone devices)", file=sys.stderr)
            return 1
        print(f"Unpaired {gone['name']} ({gone['id']}). It can't connect any more.")
        return 0

    st = status()
    if not st.get("on"):
        why = st.get("why") or "the Auren daemon isn't running (systemctl --user start auren)"
        print(f"The phone server is off: {why}", file=sys.stderr)
        print("auren doctor says more.", file=sys.stderr)
        return 1
    code, expires = start_pairing()
    uri = pair_uri(st["host"], int(st["port"]), code)
    before = {d["id"] for d in devices()}
    if not a.no_qr:
        print(_qr(uri))
    print("Scan this with the Auren app (Pair), or paste the line below into it:\n")
    print(uri + "\n")
    print(f"It works once, for {PAIR_TTL // 60} minutes, from a phone on your tailnet ({st['host']}:{st['port']}).")
    if a.no_wait:
        return 0
    try:
        while time.time() < expires:
            new = [d for d in devices() if d["id"] not in before]
            if new:
                print(f"Paired {new[0]['name']} ({new[0]['id']}).")
                return 0
            time.sleep(0.5)
    except KeyboardInterrupt:
        PAIRING.unlink(missing_ok=True)
        print("\nCancelled: the code no longer works.")
        return 130
    print("The code expired. Run auren phone pair again.")
    return 1
