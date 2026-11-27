"""The phone remote's records on the PC: paired devices, the one-time pairing code, and the inbox.

    auren phone pair            # a QR code (and its text) for the app; the code works once, for 5 minutes
    auren phone devices         # the paired phones
    auren phone revoke <id>     # unpair one: its token stops working at once
    (--json on pair and devices: for the bar panel, which shows the QR code from the PNG pair writes)

Everything lives in STATE_DIR/phone (mode 700): devices.json and pairing.json (mode 600) hold only SHA-256
hashes of the tokens and of the code, never the secrets themselves. The inbox (inbox.jsonl, files/) is what
Auren sent to the phone; its item ids only grow, so a phone that was away asks for what came after the last
one it has (the cursor). The server itself is in phone_server.py and runs inside the daemon, over HTTPS on
the Tailscale address and the PC's private Wi-Fi/LAN addresses (see docs/phone.md). Its certificate is
self-signed (cert.pem, key.pem mode 600); the pairing code carries its SHA-256 fingerprint and the app trusts
only that.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import hmac
import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

from .config import CONFIG_DIR, RUNTIME_DIR, STATE_DIR

log = logging.getLogger("auren.phone")

API = 1                             # the protocol version, in every path (/v1/...)
PHONE_DIR = STATE_DIR / "phone"
DEVICES = PHONE_DIR / "devices.json"
PAIRING = PHONE_DIR / "pairing.json"
INBOX = PHONE_DIR / "inbox.jsonl"
FILES = PHONE_DIR / "files"
CERT = PHONE_DIR / "cert.pem"
KEY = PHONE_DIR / "key.pem"
STATUS = RUNTIME_DIR / "phone.json"     # the daemon's server: on or off, where, why not, and who's connected
PAIR_QR = RUNTIME_DIR / "phone-pair.png"    # auren phone pair --json's QR codes, for the bar panel (gone after):
                                            # each pairing has its own phone-pair-<random>.png (pair_qr_file)
APP_QR = RUNTIME_DIR / "phone-app.png"      # auren phone app --json's QR code: where to download the app
RELEASES = "https://github.com/kvm404/Auren/releases"
TAILSCALE_SEEN = PHONE_DIR / "tailscale.json"   # this PC's Tailscale address, from when Tailscale was last up
PAIR_TTL = 300                      # seconds a pairing code works
PAIR_TRIES = 5                      # wrong codes before the pairing is dropped
MAX_FILE = 50 * 1024 * 1024         # biggest file send_to_phone sends
KEEP = 200                          # inbox items kept (older ones and their files go)
KINDS = ("text", "link", "image", "file")
TAILNET = ipaddress.ip_network("100.64.0.0/10")    # Tailscale's addresses (CGNAT range)
PRIVATE = tuple(ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))  # RFC 1918
# Interfaces that aren't the room's Wi-Fi or Ethernet: containers, bridges, VMs and VPN tunnels.
VIRTUAL = ("docker", "br-", "veth", "virbr", "vmnet", "vboxnet", "lxc", "lxd", "podman", "cni", "flannel",
           "zt", "wg", "tun", "tap", "tailscale")


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


def end_pairing(code: str) -> None:
    """Cancel the pairing this code opened, and not a newer one opened since (by the panel or a terminal)."""
    with _locked("devices"):
        p = _read(PAIRING, {})
        if isinstance(p, dict) and p.get("hash") == _hash(code):
            PAIRING.unlink(missing_ok=True)


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


def private(path: str | Path) -> bool:
    """Auren's own files (its config and secrets, the phones' tokens, this server's TLS key): never sent."""
    p = Path(path).resolve()
    return any(p.is_relative_to(Path(d).resolve()) for d in (CONFIG_DIR, STATE_DIR, PHONE_DIR))


def add(kind: str, title: str = "", content: str = "", path: str = "", secret_ok: bool = False) -> dict:
    """Put something in the phone's inbox: text, a link, or a copy of an image or file. A file is judged by
    the file it opens (a link swapped meanwhile is caught): Auren's own files are refused, and one that looks
    like a secret is sent only when secret_ok (the user confirmed that very path)."""
    if kind not in KINDS:
        raise ValueError("kind is text, link, image or file")
    with contextlib.ExitStack() as stack:
        return _add(stack, kind, title, content, path, secret_ok)


def opened_path(fd: int) -> Path:
    """The path of the file an open descriptor is: links followed, as the kernel opened it. Linux has it in
    /proc; macOS gives it with fcntl's F_GETPATH."""
    if sys.platform == "darwin":
        raw = fcntl.fcntl(fd, getattr(fcntl, "F_GETPATH", 50), bytes(1024))      # 1024: MAXPATHLEN
        return Path(os.fsdecode(raw.split(b"\0", 1)[0]))
    return Path(os.readlink(f"/proc/self/fd/{fd}"))


def _add(stack: contextlib.ExitStack, kind: str, title: str, content: str, path: str, secret_ok: bool) -> dict:
    from .safety import mentions_secrets

    entry: dict = {"kind": kind, "title": str(title or "")[:200], "at": time.time()}
    src = f = None
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
        given = os.path.expanduser(path or content)
        try:
            f = stack.enter_context(open(given, "rb"))
        except OSError:
            raise ValueError(f"no such file: {path or content}") from None
        src = opened_path(f.fileno())                              # the file actually opened
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            raise ValueError(f"no such file: {path or content}")
        if private(src):
            raise ValueError("that is one of Auren's own files (its settings, keys or tokens): it isn't sent")
        if mentions_secrets(str(src)) and not secret_ok:
            raise ValueError(f"{src.name} may hold a secret: it is sent only when the user confirms it")
        if secret_ok and src != Path(given):
            raise ValueError("the file changed after it was confirmed: ask again")
        size = st.st_size
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
            with open(tmp, "wb") as out:
                shutil.copyfileobj(f, out)
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

TAILSCALE_DOWN = {"Stopped": "Tailscale is off", "NeedsLogin": "Tailscale is logged out",
                  "NeedsMachineAuth": "Tailscale is waiting for this machine to be approved",
                  "Starting": "Tailscale is starting", "NoState": "Tailscale is starting"}
# Where else the Tailscale CLI is on a Mac: Homebrew's (not always in the launch agent's PATH), and the app's
# own binary (the App Store and standalone apps), which works as the CLI when run with arguments.
MAC_TAILSCALE = ("/opt/homebrew/bin/tailscale", "/usr/local/bin/tailscale",
                 "/Applications/Tailscale.app/Contents/MacOS/Tailscale")


def tailscale_cli() -> str | None:
    """The tailscale command, or None if Tailscale isn't installed. Every caller (the server, doctor) uses it."""
    found = shutil.which("tailscale")
    if found or sys.platform != "darwin":
        return found
    return next((p for p in MAC_TAILSCALE if os.access(p, os.X_OK)), None)


def tailscale_env() -> dict | None:
    """The environment to run the tailscale command in: on a Mac, TAILSCALE_BE_CLI=1, which the Tailscale app's
    own binary needs to act as the CLI (other tailscale commands ignore it). None: as it is."""
    return {**os.environ, "TAILSCALE_BE_CLI": "1"} if sys.platform == "darwin" else None


def tailscale_up() -> str:
    """How to turn Tailscale on here."""
    return "turn Tailscale on (its menu bar icon, or tailscale up)" if sys.platform == "darwin" else "tailscale up"


def tailscale_ip() -> tuple[str | None, str]:
    """This machine's Tailscale IPv4 address while Tailscale runs, or (None, why not). From `tailscale status
    --json`: `tailscale ip` prints the address even while Tailscale is stopped, when nothing can bind it. A
    stopped Tailscale's address is still kept ([remember_tailscale]): it's the same once it's up again."""
    cli = tailscale_cli()
    if not cli:
        return None, "Tailscale isn't installed"
    try:
        out = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=5,
                             env=tailscale_env())
    except (OSError, subprocess.TimeoutExpired) as e:
        return None, f"tailscale didn't answer ({type(e).__name__})"
    try:
        st = json.loads(out.stdout)
    except ValueError:
        st = None
    if not isinstance(st, dict):
        why = (out.stderr or out.stdout).strip().splitlines()
        return None, "tailscale didn't say how it is" + (f" ({why[0][:80]})" if why else "") + f": {tailscale_up()}"
    ip = next((a for a in st.get("TailscaleIPs") or [] if isinstance(a, str) and _tailnet4(a)), None)
    state = str(st.get("BackendState") or "")
    if ip:
        remember_tailscale(ip)
    if state == "Running" and ip:
        return ip, ""
    return None, TAILSCALE_DOWN.get(state, f"Tailscale isn't running ({state or 'unknown'})") + f": {tailscale_up()}"


def _tailnet4(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a in TAILNET


def remember_tailscale(ip: str) -> None:
    """Keep this PC's Tailscale address for while Tailscale is off ([reach]). It stays the same across
    tailscale down/up; only leaving the tailnet changes it, and the next time it's up this catches that."""
    if known_tailscale() == ip:
        return
    try:
        _write(TAILSCALE_SEEN, json.dumps({"ip": ip, "at": int(time.time())}))
    except OSError as e:
        log.warning("phone: couldn't keep the Tailscale address: %s", e)


def known_tailscale() -> str | None:
    """This PC's Tailscale address from when Tailscale was last up, or None if it never was."""
    ip = _read(TAILSCALE_SEEN, {})
    ip = ip.get("ip") if isinstance(ip, dict) else None
    return ip if isinstance(ip, str) and is_tailnet(ip) else None


def reach(addrs: list[dict]) -> list[dict]:
    """The addresses a phone is given (the pairing code, each hello): where the server listens, and while
    Tailscale is off, this PC's Tailscale address from before, marked "later". So a phone paired on the Wi-Fi
    with Tailscale off, or that only ever connected then, still finds the PC from elsewhere once Tailscale is
    on, without pairing again. (The phone keeps a Tailscale address that a later hello doesn't list.)"""
    if any(a.get("via") == "tailscale" for a in addrs):
        return addrs
    ip = known_tailscale()
    return addrs + [{"ip": ip, "via": "tailscale", "later": True}] if ip else addrs


def is_tailnet(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip) in TAILNET
    except ValueError:
        return False


def is_lan(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and any(a in n for n in PRIVATE)


def lan_addresses() -> tuple[list[str], str]:
    """The PC's private IPv4 addresses on real Wi-Fi or Ethernet, or ([], why none). Loopback, point-to-point
    links (VPN tunnels such as WARP), single-host /32s, container and VM bridges and public addresses are
    left out: a phone in the same room only reaches the PC through its LAN address."""
    if not shutil.which("ip"):
        return [], "the ip command isn't installed"
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5)
        links = json.loads(out.stdout or "[]")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return [], f"couldn't list the network interfaces ({type(e).__name__})"
    return pick_lan(links), ""


SYS_NET = Path("/sys/class/net")


def on_hardware(name: str, depth: int = 0) -> bool:
    """Is this interface a network card (Wi-Fi, Ethernet, a USB adapter), or a bridge, bond or VLAN over one?
    A VM or container bridge (whatever its name, br0 too), a tunnel or a veth has no device behind it."""
    d = SYS_NET / name
    if (d / "device").exists():
        return True
    below = [p.name.removeprefix("lower_") for p in d.glob("lower_*")] + [p.name for p in d.glob("brif/*")]
    return depth < 4 and any(on_hardware(n, depth + 1) for n in below if n and "/" not in n)


def pick_lan(links: list[dict], real=on_hardware) -> list[str]:
    """The kept addresses of `ip -j -4 addr` output (see lan_addresses). real(name): the interface has a
    network card behind it."""
    found = []
    for link in links if isinstance(links, list) else []:
        name = str(link.get("ifname", ""))
        flags = set(link.get("flags") or [])
        if "LOOPBACK" in flags or "POINTOPOINT" in flags or name.startswith(VIRTUAL) or not real(name):
            continue
        for a in link.get("addr_info") or []:
            ip, plen = str(a.get("local", "")), a.get("prefixlen", 32)
            if a.get("family", "inet") == "inet" and plen < 32 and is_lan(ip) and ip not in found:
                found.append(ip)
    return found


def addresses(lan: bool = True) -> tuple[list[tuple[str, str]], dict]:
    """Where the server listens: [(ip, "tailscale" | "lan")], Tailscale first, and why a path is missing
    ({"tailscale": why, "lan": why}). Never 0.0.0.0, never a public address."""
    why = {}
    ts, why_ts = tailscale_ip()
    out = [(ts, "tailscale")] if ts else []
    if ts:
        remember_tailscale(ts)
    else:
        why["tailscale"] = why_ts
    if lan:
        ips, why_lan = lan_addresses()
        out += [(ip, "lan") for ip in ips]
        if not ips:
            why["lan"] = why_lan or "no Wi-Fi or Ethernet with a private address"
    else:
        why["lan"] = "off (phone.lan = false)"
    return out, why


def cert() -> tuple[Path, Path, str]:
    """The server's self-signed certificate and key, made on first use and then kept: (cert, key, SHA-256
    fingerprint of the certificate, lowercase hex). It names no address, so a new LAN IP needs no new one;
    the app checks the fingerprint, not the name."""
    with _locked("cert"):
        if not _pair_ok():
            if CERT.exists() or KEY.exists():
                log.warning("phone: the certificate and key don't go together: made new ones (pair phones again)")
            _make_cert()
        for f in (KEY, CERT):
            os.chmod(f, 0o600)
        return CERT, KEY, fingerprint(CERT.read_bytes())


def _pair_ok() -> bool:
    """The certificate and key exist, parse, and belong together (as the TLS server will load them)."""
    import ssl
    try:
        ssl.create_default_context(ssl.Purpose.CLIENT_AUTH).load_cert_chain(CERT, KEY)
        fingerprint(CERT.read_bytes())
    except (OSError, ValueError, ssl.SSLError):
        return False
    return True


def fingerprint(pem: bytes) -> str:
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    der = x509.load_pem_x509_certificate(pem).public_bytes(Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def _make_cert() -> None:
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Auren on {socket.gethostname()}"[:64])])
    now = dt.datetime.now(dt.UTC)
    crt = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
           .serial_number(x509.random_serial_number())
           .not_valid_before(now - dt.timedelta(days=1)).not_valid_after(now + dt.timedelta(days=3650))
           .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
           .sign(key, hashes.SHA256()))
    _write(KEY, key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                  serialization.NoEncryption()))
    _write(CERT, crt.public_bytes(serialization.Encoding.PEM))


def pair_uri(hosts: list[str], port: int, code: str, fp: str) -> str:
    """What the QR code says: every address the phone may reach the PC on, and the certificate's pin."""
    from urllib.parse import urlencode
    return "auren://pair?" + urlencode({"v": API, "hosts": ",".join(hosts), "port": port, "code": code,
                                        "fp": fp, "pc": socket.gethostname()})


def status() -> dict:
    """What the daemon wrote about its server: {"on": bool, "port", "addrs": [{"ip", "via", "ok", "why"}],
    "fp", "why"}."""
    data = _read(STATUS, {})
    return data if isinstance(data, dict) else {}


def listening(st: dict) -> list[dict]:
    return [a for a in st.get("addrs") or [] if a.get("ok")]


# --- the CLI ------------------------------------------------------------------------------------

SERVE_WAIT = 15                     # seconds `auren phone pair` waits for the daemon to start the server


def _alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (TypeError, ValueError, ProcessLookupError):
        return False
    except PermissionError:
        return True
    return True


def _serving(since: float, wait: float | None = None) -> dict:
    """The daemon's server status once it listens somewhere for the pairing opened at `since`, or once it
    wrote, after that, why it can't; else as it is when `wait` runs out."""
    end = time.time() + (SERVE_WAIT if wait is None else wait)
    while True:
        st = status()
        if _alive(st.get("pid")) and (st.get("on") and listening(st)
                                      or not st.get("idle") and st.get("at", 0) >= since):
            return st
        if time.time() >= end:
            return st
        time.sleep(0.3)


def _ago(t: float) -> str:
    if not t:
        return "never"
    s = time.time() - t
    return "just now" if s < 60 else f"{int(s // 60)} min ago" if s < 3600 else \
        f"{int(s // 3600)} h ago" if s < 86400 else time.strftime("%Y-%m-%d", time.localtime(t))


def tailscale_off(st: dict) -> str:
    """What pairing now means for reaching the PC from elsewhere, or "": Tailscale is installed here, but not up.
    The code carries the Tailscale address from before if there is one ([reach]); if not, the phone learns it
    the first time it connects on this Wi-Fi after Tailscale is up. Never "pair again"."""
    why = (st.get("missing") or {}).get("tailscale", "")
    if not why or why.startswith("Tailscale isn't installed"):
        return ""
    if known_tailscale():
        return ("Tailscale is off on this PC. The code still carries its Tailscale address, so the phone reaches"
                f" the PC from elsewhere once Tailscale is on again ({tailscale_up()}).")
    if sys.platform == "darwin":
        turn_on = "turn Tailscale on (its menu bar icon, or tailscale up)"
    else:
        turn_on = "turn Tailscale on (tailscale up)"
    return ("Tailscale is off on this PC, so for now the phone works only on this Wi-Fi. To use it from"
            f" elsewhere, {turn_on}: the phone picks up the address the next time it connects here.")


def lan_networks(ips: list[str]) -> list[str]:
    """The subnets of these LAN addresses, by their interface's prefix (192.168.31.214 -> 192.168.31.0/24): a
    firewall rule for one lets in just the phones on that network. A /24 when the prefix can't be found."""
    prefix = {}
    with contextlib.suppress(OSError, subprocess.TimeoutExpired, ValueError, AttributeError, TypeError):
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5)
        for link in json.loads(out.stdout or "[]"):
            for a in link.get("addr_info") or []:
                prefix[str(a.get("local", ""))] = int(a.get("prefixlen") or 24)
    return sorted({str(ipaddress.ip_interface(f"{ip}/{prefix.get(ip, 24)}").network) for ip in ips})


def firewall(port: int, bound: list[dict]) -> dict:
    """Does ufw keep phones out of the server at `bound` ([{"ip", "via"}]) on `port`? {} when it lets them in,
    when ufw isn't running, and on a Mac (its firewall asks by itself); {"unknown": why} when ufw is on but its
    rules can't be read; else {"why", "cmds"}: what it blocks and the ufw commands that open just this port on
    just those paths (auren phone allow runs them; none when only a deny rule blocks it)."""
    if sys.platform == "darwin":
        return {}
    from .doctor import ufw_gaps
    lan = [a["ip"] for a in bound if a.get("via") == "lan"]
    try:
        gaps = ufw_gaps(port, lan_networks(lan) if lan else [], any(a.get("via") == "tailscale" for a in bound))
    except OSError as e:
        return {"unknown": f"ufw is on, but its rules couldn't be read ({e.strerror or e})."}
    if not gaps or not gaps["blocked"]:
        return {}
    where = " or ".join({"Wi-Fi/LAN": "on this Wi-Fi", "Tailscale": "over Tailscale"}[p] for p in gaps["blocked"])
    why = f"The firewall (ufw) keeps phones out {where}."
    if gaps["denied"]:
        why += " A ufw deny rule blocks Auren's port: sudo ufw status numbered shows it, to delete."
    return {"why": why, "cmds": gaps["cmds"]}


PKEXEC = "/usr/bin/pkexec"
UFW = ("/usr/bin/ufw", "/usr/sbin/ufw")        # fixed paths: never a ufw found first in the user's PATH


def allow(cmds: list[list[str]]) -> str:
    """Run ufw commands as root through pkexec, which shows the desktop's password prompt (or asks in the
    terminal): "" when they all ran, else why not."""
    ufw = next((u for u in UFW if os.access(u, os.X_OK)), None)
    if not os.access(PKEXEC, os.X_OK) or not ufw:
        return f"{'ufw' if not ufw else 'pkexec'} isn't installed: run the commands with sudo (auren doctor prints them)"
    for cmd in cmds:
        try:
            r = subprocess.run([PKEXEC, ufw, *cmd[1:]], capture_output=True, text=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            return f"Couldn't run ufw: {e}"
        err = (r.stderr or r.stdout).strip()
        if r.returncode == 126:                  # pkexec: the password prompt was closed
            return "Not allowed: the password prompt was closed."
        if r.returncode == 127:                  # pkexec: not authorized, no prompt to ask with, or another error
            if "authentication agent" in err:
                return "Nothing is here to ask for your password: run the commands with sudo (auren doctor prints them)"
            return "Not allowed: the password was refused." if "Not authorized" in err else f"pkexec failed: {err}"
        if r.returncode:
            return f"ufw failed: {err or f'exit {r.returncode}'}"
    return ""


def _allow(as_json: bool) -> int:
    """auren phone allow: open the phone's port in ufw on the paths it blocks (the panel's Let phones in)."""
    from .settings import current as settings

    def done(msg: str, ok: bool) -> int:
        print(json.dumps({"ok": True} if ok else {"error": msg}) if as_json else msg,
              file=sys.stdout if ok or as_json else sys.stderr)
        return 0 if ok else 1
    if sys.platform == "darwin":
        return done("On a Mac, macOS asks by itself the first time Auren listens for a phone: auren doctor says more.",
                    True)
    conf = settings()
    port = int(conf.get("phone.port") or 8735)
    addrs, _ = addresses(conf.get("phone.lan", True) is not False)
    bound = [{"ip": ip, "via": via} for ip, via in addrs]
    fw = firewall(port, bound)
    if "unknown" in fw:
        return done(fw["unknown"], False)
    if not fw:
        return done("The firewall already lets phones in.", True)
    if not fw["cmds"]:
        return done(fw["why"], False)
    if not as_json:
        print("This runs, as root (it asks for your password):")
        for c in fw["cmds"]:
            print("  " + shlex.join(c))
    if why := allow(fw["cmds"]):
        return done(why, False)
    if after := firewall(port, bound):
        return done(after.get("unknown") or "ufw ran, but still keeps phones out: auren doctor says why.", False)
    return done(f"Done: ufw lets phones in on port {port}.", True)


def app_link(version: str | None = None) -> tuple[str, str]:
    """Where to download the phone app that goes with this Auren: (url, version). A release (X.Y.Z) has it
    attached as auren-phone-X.Y.Z.apk, so the link downloads it straight away; a build that isn't one (a
    checkout, a dev version) gets the newest release's page instead. ("" for the version then.)"""
    if version is None:
        from importlib.metadata import PackageNotFoundError
        from importlib.metadata import version as installed
        try:
            version = installed("auren")
        except PackageNotFoundError:
            version = ""
    if re.fullmatch(r"\d+\.\d+\.\d+", version or ""):
        return f"{RELEASES}/download/v{version}/auren-phone-{version}.apk", version
    return f"{RELEASES}/latest", ""


def app_qr() -> dict:
    """The download link and a PNG of its QR code: {"url", "qr", "version"} (Get the app, in the bar panel
    and in the Mac's Phone window)."""
    import segno
    url, version = app_link()
    APP_QR.parent.mkdir(parents=True, exist_ok=True)
    tmp = APP_QR.with_name(f".{APP_QR.name}.{os.getpid()}.tmp")
    segno.make(url, error="m").save(str(tmp), kind="png", scale=10, border=2, dark="#000", light="#fff")
    os.replace(tmp, APP_QR)
    return {"url": url, "qr": str(APP_QR), "version": version}


def device_rows() -> list[dict]:
    """The paired phones as auren phone devices --json lists them (without the token hashes)."""
    return [{k: d.get(k) for k in ("id", "name", "created", "lastSeen")} for d in devices()]


def _app(as_json: bool) -> int:
    """auren phone app: a QR code to download the app on the phone (the panel's Get the app)."""
    if as_json:
        print(json.dumps(app_qr()))
        return 0
    url, version = app_link()
    print("Scan this with your phone's camera to download the Auren app" + (f" {version}" if version else "") + ":")
    print(_qr(url))
    print(url)
    print("Open the download, and allow installing from your browser when Android asks. Then pair: auren phone pair")
    return 0


def _qr(text: str) -> str:
    import io

    import segno
    buf = io.StringIO()
    segno.make(text, error="m").terminal(out=buf, compact=True, border=2)
    return buf.getvalue()


def _open_pairing() -> tuple[dict, str, float] | str:
    """Open a pairing and wait for the server: (its status, the code, when it expires), or why it can't."""
    from .settings import current as settings
    if settings()["phone.enabled"] is False:
        return "The phone remote is turned off: auren config set phone.enabled auto"
    # The server listens only while a phone is paired or a pairing is open: open one, then wait for the server.
    opened = time.time()
    code, expires = start_pairing()
    try:
        st = _serving(opened)
    except KeyboardInterrupt:              # cancelled while it waited: this code goes, not a newer one
        end_pairing(code)
        raise
    if not (st.get("on") and listening(st) and _alive(st.get("pid"))):     # a dead daemon's status doesn't count
        PAIRING.unlink(missing_ok=True)
        start = "auren service start" if sys.platform == "darwin" else "systemctl --user start auren"
        why = st.get("why") if _alive(st.get("pid")) and not st.get("idle") else \
            f"the Auren daemon isn't running ({start})"
        return f"The phone server is off: {why}"
    return st, code, expires


def pair_qr_file() -> Path:
    """A QR code file of its own for one pairing, so one that ends late never deletes the next one's."""
    return PAIR_QR.with_name(f"{PAIR_QR.stem}-{secrets.token_hex(4)}{PAIR_QR.suffix}")


def is_pair_qr(path: str | Path) -> bool:
    """Is this one of pair_qr_file's files (the only ones the Mac's Phone window may delete)?"""
    p = Path(path)
    return (p.parent == PAIR_QR.parent
            and re.fullmatch(rf"{re.escape(PAIR_QR.stem)}-[0-9a-f]{{8}}\.png", p.name) is not None)


def _pair_json() -> int:
    """auren phone pair --json, for the bar panel: one JSON line with the code and its QR code (a PNG), then one
    when a phone paired or the code expired. Stopping it (the panel's Cancel) ends the pairing at once."""
    import signal

    def out(**msg):
        print(json.dumps(msg), flush=True)

    def done(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, done)
    code = qr = None
    try:
        opened = _open_pairing()
        if isinstance(opened, str):
            out(error=opened)
            return 1
        st, code, expires = opened
        bound = listening(st)
        uri = pair_uri([x["ip"] for x in reach(bound)], int(st["port"]), code, st["fp"])
        import segno
        qr = pair_qr_file()
        qr.parent.mkdir(parents=True, exist_ok=True)
        tmp = qr.with_name(f".{qr.name}.{os.getpid()}.tmp")
        segno.make(uri, error="m").save(str(tmp), kind="png", scale=10, border=2, dark="#000", light="#fff")
        os.chmod(tmp, 0o600)                     # it holds the code
        tmp.replace(qr)
        before = {d["id"] for d in devices()}
        fw = firewall(int(st["port"]), bound)
        out(uri=uri, qr=str(qr), expires=expires,
            where=[{"ip": x["ip"], "via": x["via"]} for x in bound], warn=tailscale_off(st),
            firewall=fw.get("why", ""), can_allow=bool(fw.get("cmds")))
        while time.time() < expires:
            new = [d for d in devices() if d["id"] not in before]
            if new:
                out(paired={"id": new[0]["id"], "name": new[0]["name"]})
                return 0
            time.sleep(0.5)
        out(expired=True)
        return 1
    except KeyboardInterrupt:
        if code:
            end_pairing(code)
        out(cancelled=True)
        return 130
    finally:
        if qr:                                   # its own file only: a newer pairing's QR code stays
            qr.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="auren phone", description="Pair the Auren phone app with this PC.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pair", help="show a one-time QR code for the app (works for 5 minutes)")
    p.add_argument("--no-wait", action="store_true", help="print the code and return, don't wait for the phone")
    p.add_argument("--no-qr", action="store_true", help="only the text to paste into the app")
    p.add_argument("--json", action="store_true",
                   help="JSON lines for the bar panel: the code (and a PNG of the QR), then paired or expired")
    dv = sub.add_parser("devices", help="list paired phones")
    dv.add_argument("--json", action="store_true", help="as JSON")
    r = sub.add_parser("revoke", help="unpair a phone (its id from auren phone devices)")
    r.add_argument("id")
    g = sub.add_parser("app", help="a QR code to download the phone app (scan it with the phone's camera)")
    g.add_argument("--json", action="store_true", help="for the bar panel: the link and a PNG of its QR code")
    al = sub.add_parser("allow", help="let phones through the firewall (ufw) on this port: asks for your password")
    al.add_argument("--json", action="store_true", help="for the bar panel: {ok} or {error}")
    a = ap.parse_args(argv)
    if a.cmd == "allow":
        return _allow(a.json)
    if a.cmd == "app":
        return _app(a.json)

    if a.cmd == "devices":
        found = devices()
        if a.json:
            print(json.dumps(device_rows()))
            return 0
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

    if a.json:
        return _pair_json()
    opened = _open_pairing()
    if isinstance(opened, str):
        print(opened, file=sys.stderr)
        if not opened.startswith("The phone remote is turned off"):
            print("auren doctor says more.", file=sys.stderr)
        return 1
    st, code, expires = opened
    bound = listening(st)
    uri = pair_uri([a["ip"] for a in reach(bound)], int(st["port"]), code, st["fp"])
    before = {d["id"] for d in devices()}
    if why := firewall(int(st["port"]), bound).get("why"):      # first: before the code, which can't work yet
        print(f"{why} To let phones in first: auren phone allow\n")
    if not a.no_qr:
        print(_qr(uri))
    print("Scan this with the Auren app (Pair), or paste the line below into it:\n")
    print(uri + "\n")
    where = ", ".join(f"{a['ip']} ({'Tailscale' if a['via'] == 'tailscale' else 'Wi-Fi/LAN'})" for a in bound)
    print(f"It works once, for {PAIR_TTL // 60} minutes, from a phone on the same Wi-Fi or your tailnet: {where},"
          f" port {st['port']}.")
    if warn := tailscale_off(st):
        print(f"\n{warn}")
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
        end_pairing(code)
        print("\nCancelled: the code no longer works.")
        return 130
    print("The code expired. Run auren phone pair again.")
    return 1


if sys.platform == "darwin":
    from .mac.phone import *  # noqa: E402, F403 - the Mac's network interfaces
