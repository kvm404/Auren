"""The phone remote: pairing and tokens, the inbox and its cursor, send_to_phone, the server's API, and one
turn at a time across the desk and the phone."""

import asyncio
import contextlib
import json
import os
import stat
import sys
import threading
import time

import pytest
from starlette.testclient import TestClient, WebSocketDenialResponse

from auren import daemon, ears, integrations, phone, phone_server, tools
from auren.daemon import Turn

REAL_INSTALLED = integrations._installed       # before conftest replaces it with "everything is installed"
SESSION = daemon.SESSION


@pytest.fixture(autouse=True)
def phone_dir(tmp_path, monkeypatch):
    d = tmp_path / "phone"
    for name, value in {"PHONE_DIR": d, "DEVICES": d / "devices.json", "PAIRING": d / "pairing.json",
                        "INBOX": d / "inbox.jsonl", "FILES": d / "files", "STATUS": tmp_path / "phone.json",
                        "CERT": d / "cert.pem", "KEY": d / "key.pem"}.items():
        monkeypatch.setattr(phone, name, value)
    monkeypatch.setattr(phone_server, "STATE_FILE", tmp_path / "state.json")
    return d


def _paired(name="Pixel") -> tuple[dict, str]:
    code, _ = phone.start_pairing()
    return phone.pair(code, name)


# --- pairing and tokens --------------------------------------------------------------------------

def test_a_pairing_code_works_once_and_only_its_hash_is_kept(phone_dir):
    code, expires = phone.start_pairing()
    assert expires - time.time() <= phone.PAIR_TTL
    assert code not in phone.PAIRING.read_text()
    dev, token = phone.pair(code, "Pixel 9")
    assert dev["name"] == "Pixel 9" and "hash" not in dev
    saved = phone.DEVICES.read_text()
    assert token not in saved and phone._hash(token) in saved
    assert stat.S_IMODE(os.stat(phone.DEVICES).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(phone_dir).st_mode) == 0o700
    with pytest.raises(phone.PhoneError, match="no pairing"):
        phone.pair(code, "again")                    # used up
    assert phone.authenticate(token)["id"] == dev["id"]
    assert phone.authenticate("not-a-token") is None and phone.authenticate("") is None


def test_an_expired_code_or_too_many_wrong_ones_end_the_pairing():
    code, _ = phone.start_pairing(ttl=-1)
    with pytest.raises(phone.PhoneError, match="expired"):
        phone.pair(code, "x")
    code, _ = phone.start_pairing()
    for _ in range(phone.PAIR_TRIES):
        with pytest.raises(phone.PhoneError, match="wrong"):
            phone.pair("guess", "x")
    with pytest.raises(phone.PhoneError, match="no pairing"):
        phone.pair(code, "x")                        # even the right one: it was dropped


def test_a_revoked_token_stops_working():
    dev, token = _paired()
    other, other_token = _paired("Tablet")
    assert phone.revoke(dev["id"])["name"] == "Pixel"
    assert phone.authenticate(token) is None and phone.authenticate(other_token)["id"] == other["id"]
    assert phone.revoke(dev["id"]) is None


def test_the_cli_lists_and_revokes(capsys):
    dev, _ = _paired()
    assert phone.main(["devices"]) == 0 and dev["id"] in capsys.readouterr().out
    assert phone.main(["revoke", "nope"]) == 1
    assert phone.main(["revoke", dev["id"]]) == 0 and phone.devices() == []


def test_pair_needs_the_daemons_server(capsys, monkeypatch):
    monkeypatch.setattr(phone, "SERVE_WAIT", 0.1)
    assert phone.main(["pair", "--no-wait"]) == 1
    assert "phone server is off" in capsys.readouterr().err
    phone.STATUS.write_text(json.dumps({"on": True, "port": 8735, "fp": "ab" * 32, "pid": os.getpid(), "addrs": [
        {"ip": "100.100.1.2", "via": "tailscale", "ok": True}, {"ip": "192.168.1.20", "via": "lan", "ok": True},
        {"ip": "10.0.0.5", "via": "lan", "ok": False, "why": "taken"}]}))
    assert phone.main(["pair", "--no-wait", "--no-qr"]) == 0
    uri = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("auren://pair?"))
    from urllib.parse import parse_qs, urlparse
    q = {k: v[0] for k, v in parse_qs(urlparse(uri).query).items()}
    assert q["hosts"] == "100.100.1.2,192.168.1.20" and q["port"] == "8735" and q["fp"] == "ab" * 32
    assert "code" in q and phone.pairing_open()


# --- the certificate and the addresses -----------------------------------------------------------------

def test_the_certificate_is_made_once_and_its_key_is_private():
    import hashlib
    import ssl
    crt, key, fp = phone.cert()
    assert stat.S_IMODE(os.stat(key).st_mode) == 0o600
    assert fp == hashlib.sha256(ssl.PEM_cert_to_DER_cert(crt.read_text())).hexdigest() and len(fp) == 64
    before = (crt.read_bytes(), key.read_bytes())
    assert phone.cert()[2] == fp and (crt.read_bytes(), key.read_bytes()) == before    # kept, not made again


def test_a_certificate_and_key_that_dont_match_are_made_again():
    crt, key, _ = phone.cert()
    old_key = key.read_bytes()
    phone._make_cert()
    new_cert = crt.read_bytes()
    key.write_bytes(old_key)                                   # a partial restore: the old key, the new certificate
    os.chmod(crt, 0o644)
    assert not phone._pair_ok()
    phone.cert()
    assert phone._pair_ok() and crt.read_bytes() != new_cert
    assert stat.S_IMODE(os.stat(crt).st_mode) == stat.S_IMODE(os.stat(key).st_mode) == 0o600


IP_ADDR = [   # `ip -j -4 addr show up`, trimmed
    {"ifname": "lo", "flags": ["LOOPBACK", "UP"], "addr_info": [{"family": "inet", "local": "127.0.0.1", "prefixlen": 8}]},
    {"ifname": "enp7s0", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "192.168.0.100", "prefixlen": 24}]},
    {"ifname": "wlo1", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "10.1.2.3", "prefixlen": 16},
                                                                   {"family": "inet", "local": "203.0.113.9", "prefixlen": 24}]},
    {"ifname": "tailscale0", "flags": ["POINTOPOINT", "UP"], "addr_info": [{"family": "inet", "local": "100.92.1.2", "prefixlen": 32}]},
    {"ifname": "CloudflareWARP", "flags": ["POINTOPOINT", "UP"], "addr_info": [{"family": "inet", "local": "172.16.0.2", "prefixlen": 32}]},
    {"ifname": "docker0", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "172.17.0.1", "prefixlen": 16}]},
    {"ifname": "br-1a2b", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "172.18.0.1", "prefixlen": 16}]},
    {"ifname": "virbr0", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "192.168.122.1", "prefixlen": 24}]},
    {"ifname": "eth9", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "172.20.5.5", "prefixlen": 32}]},
    {"ifname": "eth1", "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": "198.51.100.7", "prefixlen": 24}]},
]


def test_the_lan_addresses_are_private_and_on_real_interfaces(tmp_path, monkeypatch):
    assert phone.pick_lan(IP_ADDR, real=lambda name: True) == ["192.168.0.100", "10.1.2.3"]   # no loopback, VPN,
    # docker, VM, /32, public
    net = tmp_path / "net"                                     # /sys/class/net
    for card in ("enp7s0", "wlo1", "eno1"):
        (net / card / "device").mkdir(parents=True)
    (net / "br0" / "brif" / "vnet0").mkdir(parents=True)       # a VM bridge: only virtual ports
    (net / "vnet0").mkdir()
    (net / "br1" / "brif" / "eno1").mkdir(parents=True)        # the LAN bridged over the Ethernet card
    (net / "warp0").mkdir()                                    # a tunnel under a name no list knows
    monkeypatch.setattr(phone, "SYS_NET", net)
    bridges = [{"ifname": n, "flags": ["BROADCAST", "UP"], "addr_info": [{"family": "inet", "local": ip, "prefixlen": 24}]}
               for n, ip in (("br0", "192.168.50.1"), ("br1", "192.168.1.20"), ("warp0", "172.16.9.2"))]
    assert phone.pick_lan(IP_ADDR + bridges) == ["192.168.0.100", "10.1.2.3", "192.168.1.20"]


def test_the_server_listens_on_tailscale_and_the_lan(monkeypatch):
    monkeypatch.setattr(phone, "tailscale_ip", lambda: ("100.92.1.2", ""))
    monkeypatch.setattr(phone, "lan_addresses", lambda: (["192.168.0.100"], ""))
    assert phone.addresses() == ([("100.92.1.2", "tailscale"), ("192.168.0.100", "lan")], {})
    assert phone.addresses(lan=False) == ([("100.92.1.2", "tailscale")], {"lan": "off (phone.lan = false)"})
    monkeypatch.setattr(phone, "tailscale_ip", lambda: (None, "Tailscale is off"))
    monkeypatch.setattr(phone, "lan_addresses", lambda: ([], ""))
    addrs, why = phone.addresses()
    assert addrs == [] and why["tailscale"] == "Tailscale is off" and "Wi-Fi" in why["lan"]


def test_doctor_warns_but_doesnt_fail_with_one_path(monkeypatch):
    from auren import doctor, settings
    monkeypatch.setattr(settings, "current", lambda: {"phone.enabled": True, "phone.lan": True})
    monkeypatch.setattr(phone, "addresses", lambda lan=True: ([("192.168.0.100", "lan")], {"tailscale": "Tailscale is off"}))
    monkeypatch.setattr(doctor, "_run", lambda cmd: (0, "inactive"))
    phone.STATUS.write_text(json.dumps({"on": True, "port": 8735, "fp": "ab" * 32,
                                        "addrs": [{"ip": "192.168.0.100", "via": "lan", "ok": True}]}))
    found = {c.name: c for c in doctor.phone_checks()}
    assert found["Tailscale"].status == doctor.WARN and found["Wi-Fi/LAN"].status == doctor.OK
    assert found["listening on 192.168.0.100"].status == doctor.OK
    assert all(c.status != doctor.FAIL for c in found.values())
    monkeypatch.setattr(phone, "addresses", lambda lan=True: ([], {"tailscale": "off", "lan": "none"}))
    assert any(c.status == doctor.FAIL for c in doctor.phone_checks())          # phone.enabled = true, nowhere to listen


def test_doctor_says_how_to_open_the_port_when_ufw_blocks_it(monkeypatch, tmp_path):
    from auren import doctor
    rules = tmp_path / "user.rules"
    rules.write_text("-A ufw-user-input -p tcp --dport 53317 -j ACCEPT\n")
    monkeypatch.setattr(doctor, "UFW_RULES", rules)
    monkeypatch.setattr(doctor, "_run", lambda cmd: (0, "active"))
    blocked = doctor.firewall_check(8735, ["192.168.0.105"], tailscale=True)
    assert blocked.status == doctor.WARN
    assert "from 192.168.0.0/16 to any port 8735" in blocked.fix and "in on tailscale0" in blocked.fix
    rules.write_text("-A ufw-user-input -p tcp --dport 8735 -s 192.168.0.0/16 -j ACCEPT\n")
    assert doctor.firewall_check(8735, ["192.168.0.105"], tailscale=False).status == doctor.OK
    monkeypatch.setattr(doctor, "_run", lambda cmd: (3, "inactive"))
    assert doctor.firewall_check(8735, ["192.168.0.105"], tailscale=True) is None       # no ufw: nothing to say


# --- the inbox -----------------------------------------------------------------------------------

def test_the_inbox_cursor_returns_only_what_came_after(tmp_path):
    a = phone.add("text", "Note", "buy milk")
    b = phone.add("link", "Docs", "https://example.com/x")
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-1")
    c = phone.add("file", "Report", path=str(f))
    assert [a["id"], b["id"], c["id"]] == [1, 2, 3]
    assert [it["id"] for it in phone.items(after=1)] == [2, 3]
    assert phone.items(after=3) == [] and phone.last_id() == 3
    assert phone.file_of(phone.item(3)).read_bytes() == b"%PDF-1"
    assert "stored" not in phone.shown(c)["file"] and phone.shown(c)["file"]["name"] == "report.pdf"


def test_the_inbox_refuses_what_it_cant_send(tmp_path):
    with pytest.raises(ValueError):
        phone.add("link", "", "javascript:alert(1)")
    with pytest.raises(ValueError):
        phone.add("text", "", "  ")
    with pytest.raises(ValueError):
        phone.add("file", "", path=str(tmp_path / "missing"))
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises(ValueError, match="isn't an image"):
        phone.add("image", "", path=str(tmp_path / "a.txt"))


def test_the_inbox_keeps_the_newest(monkeypatch):
    monkeypatch.setattr(phone, "KEEP", 3)
    for i in range(7):                     # trimmed once it passes twice KEEP, back down to KEEP
        phone.add("text", "", f"n{i}")
    assert [it["id"] for it in phone.items()] == [5, 6, 7]
    assert phone.add("text", "", "next")["id"] == 8


# --- send_to_phone ---------------------------------------------------------------------------------

@pytest.fixture
def rt(tmp_path):
    r = tools.Runtime(tmp_path / "tools.sock")
    r.new_turn(SESSION)
    return r


def test_send_to_phone_puts_it_in_the_inbox(rt):
    assert "no phone is paired" in rt.call(SESSION, "send_to_phone", {"kind": "link", "content": "https://x.org"})[0]
    _paired()
    out = rt.call(SESSION, "send_to_phone", {"kind": "link", "title": "X", "content": "https://x.org"})[0]
    assert "sent to the phone" in out and phone.items()[0]["url"] == "https://x.org"


def test_send_to_phone_asks_before_sending_secrets(rt, tmp_path):
    _paired()
    env = tmp_path / ".env"
    env.write_text("KEY=1")
    rt.set_level("free")
    asked = json.loads(rt.call(SESSION, "send_to_phone", {"kind": "file", "path": str(env)})[0])
    assert asked["status"] == "needs_confirmation" and phone.items() == []


def test_send_to_phone_follows_a_link_before_deciding_it_holds_no_secret(rt, tmp_path):
    _paired()
    (tmp_path / ".env").write_text("KEY=1")
    (tmp_path / "notes.txt").symlink_to(tmp_path / ".env")
    rt.set_level("free")
    asked = json.loads(rt.call(SESSION, "send_to_phone", {"kind": "file", "path": str(tmp_path / "notes.txt")})[0])
    assert asked["status"] == "needs_confirmation" and phone.items() == []


def test_send_to_phone_never_sends_aurens_own_files_and_asks_for_key_files(rt, tmp_path):
    _paired()
    phone.cert()                                              # phone/key.pem: the server's TLS private key
    rt.set_level("free")
    out = rt.call(SESSION, "send_to_phone", {"kind": "file", "path": str(phone.KEY)})[0]
    assert "Auren's own files" in out and phone.items() == []
    (tmp_path / "id_ed25519").write_text("k")
    (tmp_path / "server.key").write_text("k")
    for name in ("id_ed25519", "server.key"):
        asked = json.loads(rt.call(SESSION, "send_to_phone", {"kind": "file", "path": str(tmp_path / name)})[0])
        assert asked["status"] == "needs_confirmation"
    assert phone.items() == []


def test_the_inbox_judges_the_file_it_actually_opens(tmp_path):
    (tmp_path / ".env").write_text("KEY=1")
    (tmp_path / "notes.txt").write_text("hello")
    link = tmp_path / "link.txt"
    link.symlink_to(tmp_path / ".env")                        # swapped to a secret after the tool checked it
    with pytest.raises(ValueError, match="may hold a secret"):
        phone.add("file", path=str(link))
    with pytest.raises(ValueError, match="changed after it was confirmed"):
        phone.add("file", path=str(link), secret_ok=True)     # the user confirmed link.txt, not .env
    with pytest.raises(ValueError, match="Auren's own files"):
        phone.add("file", path=str(phone.KEY if phone.cert() else ""), secret_ok=True)
    assert phone.add("file", path=str(tmp_path / ".env"), secret_ok=True)["file"]["name"] == ".env"
    assert phone.add("file", path=str(tmp_path / "notes.txt"))["file"]["name"] == "notes.txt"


def test_send_to_phone_is_offered_only_with_a_paired_phone(monkeypatch):
    monkeypatch.setattr(integrations, "_installed", lambda name: REAL_INSTALLED(name) if name == "phone" else (True, ""))
    integrations._cache.clear()
    assert "send_to_phone" in integrations.hidden_tools()
    _paired()
    integrations._cache.clear()
    assert "send_to_phone" not in integrations.hidden_tools()
    from auren.brain import prompt
    assert "send_to_phone" in prompt.rules({"phone"}) and "send_to_phone" not in prompt.rules(set())


# --- the server ------------------------------------------------------------------------------------

class _Daemon:
    """What the hub calls on the daemon, recorded."""
    listening = _hearing = phone = None
    state = "idle"
    settings: dict = {}

    def __init__(self):
        self.asked, self.cancelled, self.answers, self.busy, self.open = [], 0, [], False, set()
        self.taken_meanwhile = False         # busy_for says free, but the turn is gone by the time it's asked

    def _origin(self):
        return "desk" if self.busy else None

    def busy_for(self, origin, device=""):
        return self.busy

    def open_turn(self, device, rid):
        return (device, rid) in self.open

    def phone_ask(self, turn, text, source="phone"):
        if self.busy or self.taken_meanwhile:
            return "busy"
        self.asked.append((turn.id, text, source))
        self.open.add((turn.device, turn.id))           # running until it replies or ends
        return "ok"

    async def phone_talk(self, turn, audio, hands_free=False, fresh=False):
        return "busy" if self.busy else "ok"

    async def phone_talk_stop(self, device):
        return "not listening"

    async def cancel(self):
        self.cancelled += 1

    def yes_no(self, yes, turn, source, ask=None):
        self.answers.append((yes, ask))
        return "approved 1"

    async def phone_gone(self, device):
        pass

    async def phone_paired_changed(self):
        pass


@pytest.fixture
def server():
    d = _Daemon()
    hub = phone_server.Hub(d)
    with TestClient(phone_server.app(hub)) as client:
        yield client, d, hub


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_pairing_over_http_gives_a_working_token(server):
    client, _, hub = server
    assert client.post("/v1/pair", json={"code": "nope", "name": "x"}).status_code == 403
    code, _ = phone.start_pairing()
    r = client.post("/v1/pair", json={"code": code, "name": "Pixel"})
    assert r.status_code == 200
    token = r.json()["token"]
    assert client.get("/v1/hello", headers=_auth(token)).json()["device"]["name"] == "Pixel"
    assert hub.paired


def test_requests_without_a_valid_token_are_refused(server):
    client, _, _ = server
    _, token = _paired()
    assert client.get("/v1/hello").status_code == 200                       # who is this: no token needed
    for bad in ({}, _auth("wrong")):
        assert client.get("/v1/inbox", headers=bad).status_code == 401
        assert client.post("/v1/share", headers=bad, json={}).status_code == 401
        with pytest.raises(WebSocketDenialResponse) as e:
            with client.websocket_connect("/v1/ws", headers=bad):
                pass
        assert e.value.status_code == 401
    assert client.get("/v1/hello", headers=_auth("wrong")).status_code == 401
    assert client.get("/v1/inbox", headers=_auth(token)).status_code == 200


def test_inbox_sync_and_files_over_http(server, tmp_path):
    client, _, _ = server
    _, token = _paired()
    phone.add("text", "", "one")
    img = tmp_path / "shot.png"
    img.write_bytes(b"\x89PNG....")
    phone.add("image", "Screen", path=str(img))
    r = client.get("/v1/inbox?after=1", headers=_auth(token)).json()
    assert [it["id"] for it in r["items"]] == [2] and r["next"] == 2 and r["last"] == 2
    f = client.get("/v1/inbox/2/file", headers=_auth(token))
    assert f.status_code == 200 and f.content == b"\x89PNG...." and f.headers["content-type"] == "image/png"
    assert client.get("/v1/inbox/1/file", headers=_auth(token)).status_code == 404


def test_a_resent_request_id_runs_once(server):
    client, d, _ = server
    _, token = _paired()
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["api"] == phone.API
        ws.send_json({"type": "ask", "id": "r1", "text": "what time is it"})
        assert ws.receive_json() == {"type": "ack", "id": "r1", "status": "ok"}
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:       # reconnected, sent again
        ws.receive_json()
        ws.send_json({"type": "ask", "id": "r1", "text": "what time is it"})
        assert ws.receive_json()["duplicate"] is True
    assert d.asked == [("r1", "what time is it", "phone")]


def test_request_ids_are_each_phones_own(server):
    client, d, _ = server
    _, one = _paired()
    _, two = _paired()
    for token in (one, two):                    # the same id from another phone is a new request
        with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
            ws.receive_json()
            ws.send_json({"type": "ask", "id": "r1", "text": "hi"})
            assert ws.receive_json() == {"type": "ack", "id": "r1", "status": "ok"}
    assert len(d.asked) == 2
    for token in (one, two):
        r = client.post("/v1/share", headers=_auth(token), json={"id": "s1", "kind": "text", "action": "ask", "text": "x"})
        assert "duplicate" not in r.json()


def test_a_busy_pc_says_so_and_the_request_can_be_sent_again(server):
    client, d, _ = server
    _, token = _paired()
    d.busy = True
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        ws.receive_json()
        ws.send_json({"type": "ask", "id": "r2", "text": "hi"})
        assert ws.receive_json() == {"type": "busy", "id": "r2", "owner": "desk"}
        assert ws.receive_json()["status"] == "busy"
        d.busy = False
        ws.send_json({"type": "ask", "id": "r2", "text": "hi"})             # busy wasn't remembered: it runs now
        assert ws.receive_json()["status"] == "ok"
        ws.send_json({"type": "cancel"})
        assert ws.receive_json()["status"] == "cancelled" and d.cancelled == 1
        ws.send_json({"type": "answer", "id": "a1", "yes": True})
        assert ws.receive_json()["status"] == "approved 1" and d.answers == [(True, "")]   # no ask id: none matches
        ws.send_json({"type": "answer", "id": "a2", "yes": False, "ask": "abc"})
        assert ws.receive_json()["status"] == "approved 1" and d.answers[-1] == (False, "abc")


def test_a_revoked_phone_is_cut_off(server):
    client, _, _ = server
    dev, token = _paired()
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        ws.receive_json()
        phone.revoke(dev["id"])
        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "revoked"}


def test_shared_text_can_be_asked(server):
    client, d, _ = server
    _, token = _paired()
    r = client.post("/v1/share", headers=_auth(token),
                    json={"id": "s1", "kind": "link", "action": "ask", "text": "https://a.b/c", "note": "summarize"})
    assert r.json() == {"status": "ok"}
    assert d.asked == [("s1", phone_server.shared_ask("link", "https://a.b/c", "summarize"), "phone-share")]
    assert d.asked[0][1].endswith("</shared>)\nsummarize") and "<shared>\nhttps://a.b/c\n</shared>" in d.asked[0][1]
    r = client.post("/v1/share", headers=_auth(token), json={"id": "s2", "kind": "link", "action": "ask", "text": "ftp://x"})
    assert r.json()["status"].startswith("error")


def test_a_phone_sets_the_pcs_voice_and_reads_it_back_from_the_state(server, tmp_path, monkeypatch):
    from auren import settings

    client, d, hub = server
    _, token = _paired()
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(settings, "LEGACY", tmp_path / "settings.json")
    state = tmp_path / "state.json"
    d.settings, d.voice = {"voiceName": "Everly"}, None       # the PC's voice may not be loaded at all
    d.set_voice_name = daemon.Daemon.set_voice_name.__get__(d)
    d._save = daemon.Daemon._save.__get__(d)
    d._refresh_state = lambda: state.write_text(json.dumps({"state": "idle", "voiceName": d.settings["voiceName"]}))
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        ws.receive_json()
        ws.send_json({"type": "voice.set", "id": "v1", "name": "hugh"})
        assert ws.receive_json() == {"type": "ack", "id": "v1", "status": "ok"}
        assert settings.load()["voiceName"] == "Hugh" and hub.snapshot()["voiceName"] == "Hugh"
        for bad in ("F2", ""):
            ws.send_json({"type": "voice.set", "id": "v2", "name": bad})
            ack = ws.receive_json()
            assert ack["id"] == "v2" and ack["status"].startswith(f"error: no voice {bad!r}")
            assert "Everly, Juniper, Hugh or Trevor" in ack["status"]
    assert settings.load()["voiceName"] == "Hugh"


def test_the_state_the_phone_gets_has_the_voice_name():
    phone_server.STATE_FILE.write_text(json.dumps({"state": "idle", "voiceName": "Juniper", "voices": ["x"]}))
    snap = phone_server.Hub(_Daemon()).snapshot()
    assert snap["voiceName"] == "Juniper" and "voices" not in snap


# --- one turn at a time ------------------------------------------------------------------------------

class _Brain:
    mode, using_backup = "auto", False

    def __init__(self):
        self.asked, self.go = [], threading.Event()
        self.go.set()

    def ask(self, text):
        self.asked.append(text)
        self.go.wait(5)
        yield ("say", "It's noon.")

    def interrupt(self):
        self.go.set()


class _Ears:
    """Listening without a microphone or AssemblyAI: it hears `heard`."""
    heard = "what time is it"

    def __init__(self, vocab, on_partial=None, source=None, on_level=None, on_end=None, audio=None):
        self.audio, self.text, self.words, self.error = audio, "", 0, None
        self.quiet_seconds, self.held_seconds, self.cancelled = 0.0, 1.0, False

    async def start(self):
        pass

    async def finish(self, timeout=8):
        return self.heard

    async def cancel(self):
        self.cancelled = True


class _Level:
    def set(self, v):
        pass


class _Hub:
    def __init__(self):
        self.replies, self.ended = [], []

    def reply(self, turn, text):
        self.replies.append((turn.id, text))

    def closed(self, turn):
        if turn.id not in [r for r, _ in self.replies]:
            self.ended.append(turn.id)

    def state_changed(self):
        pass


class _Harness(daemon.Daemon):
    def __init__(self, tmp_path):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "Everly", "safety": "ask"}
        self.tools = tools.Runtime(tmp_path / "tools.sock")
        self.brain = _Brain()
        self.voice = self.profile = self.listening = None
        self.vocab, self.said, self.history = [], [], []
        self.level = _Level()
        self.phone = _Hub()
        self.state = "idle"

    async def say(self, text):
        self.said.append(text)

    def set_state(self, state, detail="", **extra):
        self.state, self.extra = state, extra

    def _history(self, record):
        self.history.append(record)

    def _refresh_state(self):
        pass


def _turns(tmp_path, monkeypatch, scenario):
    monkeypatch.setattr(daemon, "Listening", _Ears)
    monkeypatch.setattr(daemon, "QUIET_TICK", 0.01)

    async def go():
        d = _Harness(tmp_path)
        d.busy = asyncio.Lock()
        d.loop = asyncio.get_running_loop()
        await scenario(d)
        await asyncio.sleep(0.05)
        return d

    return asyncio.run(go())


async def _until(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while not cond():
        assert time.monotonic() < end, "timed out"
        await asyncio.sleep(0.01)


def test_the_phone_is_busy_while_the_desk_listens_and_answers(tmp_path, monkeypatch):
    async def scenario(d):
        assert await d.command("talk") == "listening"
        assert d.phone_ask(Turn("phone", "p1", "dev"), "hello") == "busy"
        assert await d.phone_talk(Turn("phone", "p2", "dev"), asyncio.Queue()) == "busy"
        d.brain.go.clear()
        assert (await d.command("talk")).startswith("heard")
        await _until(lambda: d.state == "thinking")
        assert d.phone_ask(Turn("phone", "p3", "dev"), "hello") == "busy"
        d.brain.go.set()
        await _until(lambda: d.said)
        await _until(lambda: not d.busy_for("phone"))
        assert d.phone_ask(Turn("phone", "p4", "dev"), "and now?") == "ok"
        await _until(lambda: d.phone.replies)

    d = _turns(tmp_path, monkeypatch, scenario)
    assert d.said == ["It's noon."]                        # the desk's turn is spoken
    assert d.phone.replies == [("p4", "It's noon.")]       # the phone's is only shown on the phone
    assert d.brain.asked[1].startswith(daemon.PHONE_NOTE)


def test_the_desk_is_busy_while_the_phone_has_a_turn_and_either_can_stop_it(tmp_path, monkeypatch):
    async def scenario(d):
        audio = asyncio.Queue()
        assert await d.phone_talk(Turn("phone", "t1", "dev"), audio, hands_free=True) == "ok"
        assert d.listening.audio is audio
        assert await d.command("talk") == "busy"
        assert await d.command("ask what's up") == "busy"
        d.brain.go.clear()
        assert (await d.phone_talk_stop("dev")).startswith("heard")
        await _until(lambda: d.state == "thinking")
        assert await d.command("start") == "busy"
        await d.command("cancel")                          # Stop from the desk
        await _until(lambda: not d.busy_for("desk"))
        assert await d.command("ask what's up") == "asked"

    d = _turns(tmp_path, monkeypatch, scenario)
    assert d.history[0]["source"] == "phone-voice"


def test_a_phone_that_drops_mid_listen_cancels_it(tmp_path, monkeypatch):
    async def scenario(d):
        assert await d.phone_talk(Turn("phone", "t1", "dev"), asyncio.Queue()) == "ok"
        session = d.listening
        await d.phone_gone("other-device")
        assert d.listening is session
        await d.phone_gone("dev")
        assert d.listening is None and session.cancelled and not d.busy_for("desk")

    _turns(tmp_path, monkeypatch, scenario)


def test_nothing_heard_on_the_phone_frees_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(_Ears, "heard", "")

    async def scenario(d):
        assert await d.phone_talk(Turn("phone", "t1", "dev"), asyncio.Queue()) == "ok"
        assert await d.phone_talk_stop("dev") == "heard nothing"
        assert await d.command("ask hi") == "asked"

    _turns(tmp_path, monkeypatch, scenario)


def test_a_stop_while_the_transcript_comes_wins(tmp_path, monkeypatch):
    gate = asyncio.Event

    class SlowEars(_Ears):
        async def finish(self, timeout=8):
            self.finishing.set()
            await self.go.wait()                       # the transcript is on its way
            return self.heard

    async def scenario(d):
        SlowEars.finishing, SlowEars.go = gate(), gate()
        assert await d.phone_talk(Turn("phone", "t1", "dev"), asyncio.Queue()) == "ok"
        release = asyncio.create_task(d.phone_talk_stop("dev"))
        await SlowEars.finishing.wait()
        await d.cancel()                               # Stop, acked while release waits for the words
        SlowEars.go.set()
        assert await release == "cancelled"
        assert not d.busy_for("desk") and d.phone.ended == ["t1"]

    monkeypatch.setattr(daemon, "Listening", SlowEars)
    monkeypatch.setattr(daemon, "QUIET_TICK", 0.01)

    async def go():
        d = _Harness(tmp_path)
        d.busy, d.loop = asyncio.Lock(), asyncio.get_running_loop()
        await scenario(d)
        await asyncio.sleep(0)
        return d

    d = asyncio.run(go())
    assert d.brain.asked == [] and d.history == []     # nothing was sent


def test_overlapping_desk_releases_each_send_their_words(tmp_path, monkeypatch):
    """A second press and release while the first transcript is still coming: both are sent, and a Stop
    afterwards drops only what is still coming."""
    class SlowEars(_Ears):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            self.go, self.heard = asyncio.Event(), f"words {len(made)}"
            made.append(self)

        async def finish(self, timeout=8):
            await self.go.wait()
            return self.heard

    made: list = []
    monkeypatch.setattr(daemon, "MIN_HOLD", 0)

    async def scenario(d):
        assert await d.command("start") == "listening"
        first = asyncio.create_task(d.command("stop"))
        await asyncio.sleep(0)
        assert await d.command("start") == "listening"
        second = asyncio.create_task(d.command("stop"))
        await asyncio.sleep(0)
        made[0].go.set()
        assert await first == "heard: words 0"
        made[1].go.set()
        assert await second == "heard: words 1"
        assert await d.command("start") == "listening"
        third = asyncio.create_task(d.command("stop"))
        await asyncio.sleep(0)
        await d.cancel()
        made[2].go.set()
        assert await third == "cancelled"

    monkeypatch.setattr(daemon, "Listening", SlowEars)
    monkeypatch.setattr(daemon, "QUIET_TICK", 0.01)

    async def go():
        d = _Harness(tmp_path)
        d.busy, d.loop = asyncio.Lock(), asyncio.get_running_loop()
        await scenario(d)
        await _until(lambda: len(d.said) == 2)
        return d

    assert asyncio.run(go()).brain.asked == ["words 0", "words 1"]


def test_one_phone_at_a_time_and_only_it_stops_its_listen(tmp_path, monkeypatch):
    async def scenario(d):
        assert await d.phone_talk(Turn("phone", "a1", "phone-a"), asyncio.Queue()) == "ok"
        assert d.phone_ask(Turn("phone", "b1", "phone-b"), "hi") == "busy"          # another phone is someone else
        assert await d.phone_talk_stop("phone-b") == "not listening" and d.listening is not None
        assert d.open_turn("phone-a", "a1") and not d.open_turn("phone-b", "a1")
        assert (await d.phone_talk_stop("phone-a")).startswith("heard")
        await _until(lambda: d.phone.replies)
        await _until(lambda: not d.busy_for("phone", "phone-b"))
        assert d.phone_ask(Turn("phone", "b2", "phone-b"), "now me") == "ok"

    d = _turns(tmp_path, monkeypatch, scenario)
    assert d.phone.replies[0] == ("a1", "It's noon.")


def test_a_second_start_from_the_same_phone_is_busy(tmp_path, monkeypatch):
    async def scenario(d):
        d.brain.go.clear()
        assert d.phone_ask(Turn("phone", "p1", "dev"), "hello") == "ok"
        assert d.phone_ask(Turn("phone", "p2", "dev"), "and this") == "busy"
        assert await d.phone_talk(Turn("phone", "p3", "dev"), asyncio.Queue()) == "busy"
        d.brain.go.set()
        await _until(lambda: d.phone.replies)
        await _until(lambda: not d._live())
        assert d.phone_ask(Turn("phone", "p4", "dev"), "now") == "ok"
        await _until(lambda: len(d.phone.replies) == 2)

    d = _turns(tmp_path, monkeypatch, scenario)
    assert [r for r, _ in d.phone.replies] == ["p1", "p4"]


def test_a_desk_ask_doesnt_hide_an_open_desk_listen(tmp_path, monkeypatch):
    async def scenario(d):
        assert await d.command("talk") == "listening"
        assert await d.command("ask what's the date") == "asked"     # the desk's own: it queues
        await _until(lambda: d.said)                                   # that answer ended...
        assert d.phone_ask(Turn("phone", "p1", "dev"), "hi") == "busy"   # ...but the listen is still open
        assert (await d.command("talk")).startswith("heard")
        await _until(lambda: len(d.said) == 2)
        await _until(lambda: not d.busy_for("phone", "dev"))

    _turns(tmp_path, monkeypatch, scenario)


def test_a_phone_yes_approves_only_the_question_it_showed(tmp_path, monkeypatch):
    ran = []

    async def scenario(d):
        d.tools.new_turn(SESSION)
        turn = d.tools._session(SESSION).turn
        d.tools.confirmations.request(SESSION, turn, "Close Calc?", lambda: ran.append("calc") or "ok")
        d.idle()
        shown = d.extra["ask"]
        assert shown == daemon.ask_id(d.tools.confirmations.batch(SESSION))
        d.tools.new_turn(SESSION)                       # a later message asked something else
        d.tools.confirmations.request(SESSION, turn + 1, "Delete the logs?", lambda: ran.append("logs") or "ok")
        assert d.yes_no(True, Turn("phone", "y1", "dev"), "phone", shown) == "that question is no longer open"
        assert d.yes_no(True, Turn("phone", "y2", "dev"), "phone", "") == "that question is no longer open"
        d.idle()
        assert d.yes_no(True, Turn("phone", "y3", "dev"), "phone", d.extra["ask"]) == "approved 1"
        await _until(lambda: ran)

    _turns(tmp_path, monkeypatch, scenario)
    assert ran == ["logs"]


# --- the hub: replies, resends, the microphone ------------------------------------------------------

def _conn(dev_id: str) -> phone_server.Conn:
    return phone_server.Conn(None, {"id": dev_id, "name": dev_id})


def _drain(conn) -> list[dict]:
    out = []
    while not conn.out.empty():
        out.append(conn.out.get_nowait())
    return out


def test_a_retry_while_the_first_still_runs_gets_in_progress():
    class Slow(_Daemon):
        async def phone_talk(self, turn, audio, hands_free=False, fresh=False):
            self.started.set()
            await self.go.wait()
            return "ok"

    async def go():
        d = Slow()
        d.started, d.go = asyncio.Event(), asyncio.Event()
        hub, conn = phone_server.Hub(d), _conn("dev")
        first = asyncio.create_task(hub.request(conn, {"type": "talk.start", "id": "t1"}))
        await d.started.wait()
        again = await hub.request(conn, {"type": "talk.start", "id": "t1"})
        d.go.set()
        return again, await first, await hub.request(conn, {"type": "talk.start", "id": "t1"})

    again, first, later = asyncio.run(go())
    assert again == {"type": "ack", "id": "t1", "status": "in progress", "duplicate": True}
    assert first["status"] == "ok" and later["duplicate"] is True and later["status"] == "ok"


def test_replies_go_to_their_phone_and_wait_for_it_to_come_back():
    d = _Daemon()
    hub = phone_server.Hub(d)
    a, b = _conn("phone-a"), _conn("phone-b")
    hub.conns |= {a, b}
    hub.reply(Turn("phone", "r1", "phone-a"), "It's noon.")
    assert [m["text"] for m in _drain(a)] == ["It's noon."] and _drain(b) == []
    hub.closed(Turn("phone", "r1", "phone-a"))                 # replied: no "ended" on top
    hub.closed(Turn("phone", "r2", "phone-a"))                 # nothing heard: it ended without a reply
    assert _drain(a) == [{"type": "ended", "id": "r2"}]
    back = _conn("phone-a")                                    # the socket dropped and came back
    d.open.add(("phone-a", "r3"))
    hub.resume(back, ["r1", "r2", "r3", "r4"])
    got = _drain(back)
    assert [m["type"] for m in got] == ["reply", "ended", "lost"] and got[2]["id"] == "r4"   # r3 is still coming
    hub.resume(_conn("phone-b"), ["r1"])                        # another phone's id is not its own
    assert hub.replies and _drain(b) == []


def test_a_resend_after_a_lost_ack_brings_the_reply_with_it():
    """The link dropped before the ACK and the reply came while it was down: the phone resends the ask (not
    a resume), and the duplicate's ACK must not leave its line waiting for the reply."""
    async def go():
        d = _Daemon()
        hub, conn = phone_server.Hub(d), _conn("dev")
        await hub.request(conn, {"type": "ask", "id": "a1", "text": "hi"})
        hub.reply(Turn("phone", "a1", "dev"), "It's noon.")
        hub.closed(Turn("phone", "a1", "dev"))
        await hub.request(conn, {"type": "ask", "id": "a2", "text": "and?"})   # still running: its reply comes later
        back = _conn("dev")
        acks = [await hub.request(back, {"type": "ask", "id": i, "text": "x"}) for i in ("a1", "a2")]
        return d, acks, _drain(back)

    d, acks, got = asyncio.run(go())
    assert [a["duplicate"] for a in acks] == [True, True] and len(d.asked) == 2
    assert [(m["type"], m["id"], m.get("text")) for m in got] == [("reply", "a1", "It's noon.")]


def test_kept_replies_expire(monkeypatch):
    hub = phone_server.Hub(_Daemon())
    hub.reply(Turn("phone", "r1", "dev"), "old")
    monkeypatch.setattr(phone_server, "REPLY_TTL", -1)
    back = _conn("dev")
    hub.resume(back, ["r1"])
    assert _drain(back) == [{"type": "lost", "id": "r1"}]


def test_microphone_frames_only_feed_the_phone_that_started_the_listen():
    d = _Daemon()
    hub = phone_server.Hub(d)
    a, b = _conn("phone-a"), _conn("phone-b")
    audio: asyncio.Queue = asyncio.Queue()
    a.audio = b.audio = audio                                  # b pretending to feed a's listen
    d.listening = type("L", (), {"audio": audio})()
    d._hearing = Turn("phone", "t1", "phone-a")
    hub.audio(b, b"\x01\x02")
    hub.audio(a, b"\x03\x04")
    assert audio.get_nowait() == b"\x03\x04" and audio.empty()


def test_what_phones_asked_outlives_a_server_restart(monkeypatch):
    """A reload (phone.lan toggled) makes a new hub: an accepted request resent after a lost ACK must not run
    again, and a reply that came meanwhile still waits for its phone."""
    monkeypatch.setattr(phone, "addresses", lambda lan=True: ([], {"tailscale": "off"}))

    async def go():
        d = _Daemon()
        first = asyncio.create_task(phone_server.supervise(d))
        await _until(lambda: d.phone is not None)
        hub, conn = d.phone, _conn("dev")
        assert (await hub.request(conn, {"type": "ask", "id": "a1", "text": "hi"}))["status"] == "ok"
        first.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await first
        second = asyncio.create_task(phone_server.supervise(d))
        await _until(lambda: d.phone is not None)
        assert d.phone is not hub
        d.phone.reply(Turn("phone", "a1", "dev"), "It's noon.")
        again = await d.phone.request(conn, {"type": "ask", "id": "a1", "text": "hi"})
        back = _conn("dev")
        d.phone.resume(back, ["a1"])
        second.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await second
        return d, again, _drain(back)

    d, again, resumed = asyncio.run(go())
    assert d.asked == [("a1", "hi", "phone")]
    assert again == {"type": "ack", "id": "a1", "status": "ok", "duplicate": True}
    assert [m["text"] for m in resumed] == ["It's noon."]


def test_overlapping_reloads_leave_one_server_and_disable_stops_it(tmp_path, monkeypatch):
    live = set()

    async def serve(self):
        if self.settings.get("phone.enabled") is False:
            return
        me = object()
        live.add(me)
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)                 # shutting down takes a moment (uvicorn winding down)
            live.discard(me)

    async def go():
        d = _Harness(tmp_path)
        d.settings["phone.enabled"] = True
        d._phone_task = asyncio.create_task(serve(d))
        await asyncio.sleep(0)
        await asyncio.gather(d._restart_phone(), d._restart_phone())
        await asyncio.sleep(0)
        assert len(live) == 1
        d.settings["phone.enabled"] = False
        await d._restart_phone()
        await asyncio.sleep(0.05)
        return len(live)

    monkeypatch.setattr(daemon.Daemon, "_serve_phone", serve)
    assert asyncio.run(go()) == 0


# --- the hub over HTTP: sharing and pairing --------------------------------------------------------

def test_a_phone_unpaired_while_its_upload_came_is_refused(server, monkeypatch):
    client, d, _ = server
    dev, token = _paired()
    calls = []

    def auth(tok):
        calls.append(tok)
        return dev if len(calls) == 1 else None                # revoked after the headers, before the body

    monkeypatch.setattr(phone, "authenticate", auth)
    r = client.post("/v1/share", headers=_auth(token), json={"id": "s1", "kind": "text", "action": "ask", "text": "x"})
    assert r.status_code == 401 and d.asked == [] and len(calls) == 2


def test_a_shared_image_is_kept_only_for_a_request_that_runs(server, monkeypatch, tmp_path):
    client, d, _ = server
    _, token = _paired()
    monkeypatch.setattr(phone_server, "_downloads_dir", lambda: tmp_path / "dl")
    img = {"kind": "image", "action": "ask", "data": "iVBORw0KGgo=", "mime": "image/png"}
    d.busy = True
    assert client.post("/v1/share", headers=_auth(token), json={"id": "i1", **img}).status_code == 409
    assert not (tmp_path / "dl").exists()                      # nothing written for a busy PC
    d.busy, d.taken_meanwhile = False, True
    assert client.post("/v1/share", headers=_auth(token), json={"id": "i2", **img}).status_code == 409
    assert list((tmp_path / "dl").iterdir()) == []             # written, then removed when the turn was gone
    d.taken_meanwhile = False
    for rid in ("i3", "i4"):
        assert client.post("/v1/share", headers=_auth(token), json={"id": rid, **img}).json()["status"] == "ok"
    assert len(list((tmp_path / "dl").iterdir())) == 2         # the same second, two names


def test_pairing_is_refused_before_the_body_when_closed_and_bodies_are_capped(server, monkeypatch, caplog):
    client, _, _ = server
    read = []
    real = phone_server._read_body

    async def spy(request, limit):
        read.append(limit)
        return await real(request, limit)

    monkeypatch.setattr(phone_server, "_read_body", spy)
    caplog.set_level("WARNING", logger="auren")
    r = client.post("/v1/pair", content=b"x" * 100_000)
    assert r.status_code == 403 and read == [] and "refused from" in caplog.text
    phone.start_pairing()
    assert client.post("/v1/pair", content=b"{" + b" " * phone_server.MAX_PAIR + b"}").status_code == 413
    _, token = _paired()
    big = b"x" * (phone_server.MAX_SHARE + 1)
    assert client.post("/v1/share", headers=_auth(token), content=big).status_code == 413


def test_pairing_attempts_are_limited(server):
    client, _, _ = server
    codes = [client.post("/v1/pair", json={"code": "guess", "name": "x"}).status_code
             for _ in range(phone_server.PAIR_RATE + 2)]
    assert codes[:phone_server.PAIR_RATE] == [403] * phone_server.PAIR_RATE and codes[-1] == 429


# --- the server itself: TLS, pinned; reload starts, stops and moves it ----------------------------------

def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _pinned(fp: str):
    """What the app does: no CA, no hostname; the certificate must be the one the QR code named."""
    import ssl
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE

    def check(ssl_object):
        import hashlib
        got = hashlib.sha256(ssl_object.getpeercert(binary_form=True)).hexdigest()
        if got != fp:
            raise ConnectionRefusedError(f"pin mismatch: {got}")

    return ctx, check


async def _hello(port: int, fp: str) -> dict:
    ctx, check = _pinned(fp)
    reader, writer = await asyncio.open_connection("127.0.0.1", port, ssl=ctx)
    try:
        check(writer.get_extra_info("ssl_object"))
        writer.write(b"GET /v1/hello HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
        raw = await reader.read()
    finally:
        writer.close()
    return json.loads(raw.split(b"\r\n\r\n", 1)[1])


async def _refused(port: int) -> bool:
    try:
        _, w = await asyncio.open_connection("127.0.0.1", port)
    except OSError:
        return True
    w.close()
    return False


def test_the_server_speaks_only_pinned_tls_and_reload_stops_and_moves_it(tmp_path, monkeypatch):
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    monkeypatch.setattr(phone, "addresses", lambda lan=True: ([("127.0.0.1", "lan")], {"tailscale": "off"}))
    _, token = _paired()
    port, moved = _free_port(), _free_port()

    async def go():
        d = _Harness(tmp_path)
        d.busy, d.loop = asyncio.Lock(), asyncio.get_running_loop()
        d.settings.update({"phone.enabled": True, "phone.port": port})
        d._phone_task = asyncio.create_task(d._serve_phone())
        await _until(lambda: phone.status().get("on"), 10)
        st = phone.status()
        fp = phone.cert()[2]
        assert st["fp"] == fp and st["addrs"] == [{"ip": "127.0.0.1", "via": "lan", "ok": True}]
        hello = await _hello(port, fp)
        assert hello["fp"] == fp and hello["addrs"] == [{"ip": "127.0.0.1", "via": "lan"}]
        with pytest.raises(ConnectionRefusedError, match="pin mismatch"):
            await _hello(port, "00" * 32)                           # another certificate: the app hangs up
        r, w = await asyncio.open_connection("127.0.0.1", port)     # cleartext gets no HTTP answer
        w.write(b"GET /v1/hello HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"auren" not in await asyncio.wait_for(r.read(), 5)
        w.close()

        ctx, check = _pinned(fp)
        ws = await connect(f"wss://127.0.0.1:{port}/v1/ws", ssl=ctx, additional_headers=_auth(token))
        check(ws.transport.get_extra_info("ssl_object"))
        assert json.loads(await ws.recv())["type"] == "hello"
        d.settings["phone.enabled"] = False                         # auren config set phone.enabled false; reload
        await d._restart_phone()
        with pytest.raises(ConnectionClosed):
            await asyncio.wait_for(ws.recv(), 5)
        assert await _refused(port) and not phone.STATUS.exists() and d.phone is None
        d.settings.update({"phone.enabled": True, "phone.port": moved})
        await d._restart_phone()
        await _until(lambda: phone.status().get("port") == moved and phone.status().get("on"), 10)
        assert (await _hello(moved, fp))["app"] == "auren" and await _refused(port)
        d._phone_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await d._phone_task
        assert await _refused(moved)

    asyncio.run(go())


# --- the ears, fed from a queue ------------------------------------------------------------------------

def test_listening_streams_pcm_from_a_queue(monkeypatch):
    import websockets

    got = []

    async def fake_assemblyai(ws):
        await ws.send(json.dumps({"type": "Begin"}))
        n = 0
        async for m in ws:
            if isinstance(m, bytes):
                n += len(m)
                continue
            kind = json.loads(m)["type"]
            if kind == "ForceEndpoint":
                got.append(n)
                await ws.send(json.dumps({"type": "Turn", "transcript": f"{n} bytes", "end_of_turn": True}))
            elif kind == "Terminate":
                await ws.send(json.dumps({"type": "Termination"}))

    async def go():
        async with websockets.serve(fake_assemblyai, "127.0.0.1", 0) as srv:
            port = srv.sockets[0].getsockname()[1]
            monkeypatch.setattr(ears, "URL", f"ws://127.0.0.1:{port}")
            monkeypatch.setattr(ears, "secret", lambda name: "key")
            levels = []
            audio: asyncio.Queue = asyncio.Queue()
            session = ears.Listening([], on_level=levels.append, audio=audio)
            await session.start()
            assert session.mic is None                       # no pw-record: the phone is the microphone
            for _ in range(10):
                audio.put_nowait(b"\x10\x00" * 1600)
            await asyncio.sleep(0.1)
            return await session.finish(timeout=3), levels

    text, levels = asyncio.run(go())
    assert text == "32000 bytes" and got == [32000] and len(levels) == 10


def test_listening_leaves_no_sender_behind_when_the_service_drops(monkeypatch):
    """The live log: a phone turn ending printed "Task was destroyed but it is pending!" for the sender. A
    stream that fails (or is cancelled) with the sender still waiting on the queue must cancel and await it."""
    import websockets

    async def drops(ws):
        await ws.send(json.dumps({"type": "Begin"}))
        await ws.send(json.dumps({"type": "Turn", "transcript": "hello", "end_of_turn": True}))
        await ws.close(code=1011)

    async def go():
        async with websockets.serve(drops, "127.0.0.1", 0) as srv:
            monkeypatch.setattr(ears, "URL", f"ws://127.0.0.1:{srv.sockets[0].getsockname()[1]}")
            monkeypatch.setattr(ears, "secret", lambda name: "key")
            session = ears.Listening([], audio=asyncio.Queue(), on_end=lambda: None)
            await session.start()
            await asyncio.wait_for(session._done.wait(), 3)
            await asyncio.sleep(0)
            senders = [t for t in asyncio.all_tasks() if t.get_coro().__qualname__.endswith("<locals>.send")]
            text = await session.finish(timeout=1)
            return senders, text, session.error

    senders, text, error = asyncio.run(go())
    assert senders == [] and text == "hello" and error


# --- the server following its addresses --------------------------------------------------------------------

def _supervised(monkeypatch, tmp_path, addrs_seq, scenario):
    """Run supervise with addresses from addrs_seq (a callable), fast clocks, counting server starts."""
    starts = []
    monkeypatch.setattr(phone_server, "WATCH", 0.05)
    monkeypatch.setattr(phone_server, "RETRY", 0.2)
    monkeypatch.setattr(phone, "addresses", lambda lan=True: addrs_seq())
    real = phone_server._Running.__init__

    def counted(self, *a, **k):
        starts.append(a[1])
        real(self, *a, **k)

    monkeypatch.setattr(phone_server._Running, "__init__", counted)

    async def go():
        d = _Daemon()
        d.settings = {"phone.port": _free_port()}
        _paired()                                   # the server listens only with a phone to serve
        task = asyncio.create_task(phone_server.supervise(d))
        try:
            await scenario(d, starts)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    asyncio.run(go())


def test_a_port_that_comes_free_on_one_address_is_listened_on(monkeypatch, tmp_path):
    import socket
    blocker = socket.socket()

    async def scenario(d, starts):
        port = d.settings["phone.port"]
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.2", port))
        blocker.listen()
        await _until(lambda: phone.status().get("on"), 10)
        assert [r["ok"] for r in phone.status()["addrs"]] == [True, False]
        blocker.close()                                        # the other program let the port go
        await _until(lambda: [r["ok"] for r in phone.status().get("addrs", [])] == [True, True], 10)

    try:
        _supervised(monkeypatch, tmp_path, lambda: ([("127.0.0.1", "tailscale"), ("127.0.0.2", "lan")], {}), scenario)
    finally:
        blocker.close()


def test_one_failed_tailscale_probe_doesnt_restart_the_server(monkeypatch, tmp_path):
    both = [("127.0.0.1", "lan"), ("127.0.0.3", "tailscale")]
    flaky = {"n": 0}

    def addrs():
        flaky["n"] += 1
        return (both[:1], {"tailscale": "timed out"}) if flaky["n"] == 3 else (both, {})

    async def scenario(d, starts):
        await _until(lambda: flaky["n"] > 6, 10)
        assert len(starts) == 1

    _supervised(monkeypatch, tmp_path, addrs, scenario)


def test_the_status_says_on_only_once_the_server_serves(monkeypatch, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    crt, key, fp = phone.cert()
    monkeypatch.setattr(phone, "KEY", other / "key.pem")
    monkeypatch.setattr(phone, "CERT", other / "cert.pem")
    phone._make_cert()                                         # a key that isn't the certificate's
    monkeypatch.setattr(phone, "cert", lambda: (crt, other / "key.pem", fp))

    async def scenario(d, starts):
        await _until(lambda: "didn't start" in phone.status().get("why", ""), 10)
        assert phone.status()["on"] is False and not d.phone.addrs

    _supervised(monkeypatch, tmp_path, lambda: ([("127.0.0.1", "lan")], {}), scenario)


# --- nothing listens without a phone ------------------------------------------------------------------

def test_with_no_phone_paired_nothing_listens_until_a_pairing_opens(monkeypatch, tmp_path):
    """A PC on a café's Wi-Fi with no phone paired has no open port: the server starts when auren phone pair
    opens a pairing, and stops again when it ends with no phone paired."""
    starts = []
    monkeypatch.setattr(phone_server, "WATCH", 0.05)
    monkeypatch.setattr(phone_server, "IDLE", 0.02)
    monkeypatch.setattr(phone, "addresses", lambda lan=True: ([("127.0.0.1", "lan")], {}))
    real = phone_server._Running.__init__
    monkeypatch.setattr(phone_server._Running, "__init__", lambda self, *a, **k: starts.append(1) or real(self, *a, **k))

    async def go():
        d = _Daemon()
        d.settings = {"phone.port": _free_port()}
        task = asyncio.create_task(phone_server.supervise(d))
        try:
            await _until(lambda: phone.status().get("idle"))
            await asyncio.sleep(0.1)
            idle = (phone.status(), list(starts))
            phone.start_pairing()
            await _until(lambda: phone.status().get("on"))
            pairing = phone.status()
            phone.PAIRING.unlink()                  # the code expired, nobody paired
            await _until(lambda: phone.status().get("idle"))
            return idle, pairing, phone.status()
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    (idle, before), pairing, after = asyncio.run(go())
    assert not idle["on"] and idle["why"] == phone_server.IDLE_WHY and before == []
    assert pairing["on"] and [a["ip"] for a in pairing["addrs"]] == ["127.0.0.1"]
    assert not after["on"] and after["idle"]


def test_phone_pair_opens_the_pairing_before_waiting_for_the_server(monkeypatch, capsys):
    """The daemon starts the server only once a pairing is open, so pair opens it first and shows the code
    once the server listens."""
    import os
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("auren.settings.current", lambda: {"phone.enabled": "auto"})
    seen = []

    def status():
        if phone.pairing_open():
            seen.append("open")
            return {"on": True, "port": 8735, "fp": "ab" * 32, "pid": os.getpid(),
                    "addrs": [{"ip": "192.168.1.5", "via": "lan", "ok": True}]}
        return {"on": False, "idle": True, "pid": os.getpid()}

    monkeypatch.setattr(phone, "status", status)
    assert phone.main(["pair", "--no-wait", "--no-qr"]) == 0
    assert seen and "192.168.1.5" in capsys.readouterr().out


def test_phone_pair_says_when_the_daemon_isnt_running(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("auren.settings.current", lambda: {"phone.enabled": "auto"})
    monkeypatch.setattr(phone, "SERVE_WAIT", 0.1)
    monkeypatch.setattr(phone, "status", lambda: {})
    assert phone.main(["pair", "--no-wait", "--no-qr"]) == 1
    assert "daemon isn't running" in capsys.readouterr().err and not phone.pairing_open()


# --- not on macOS yet ------------------------------------------------------------------------------------

def test_the_phone_remote_is_off_on_a_mac(monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "darwin")
    _paired()
    assert REAL_INSTALLED("phone") == (False, "the phone remote isn't on macOS yet")
    assert phone.main(["devices"]) == 2 and "isn't on macOS yet" in capsys.readouterr().err


def test_the_daemon_serves_no_phone_on_a_mac(monkeypatch):
    called = []
    monkeypatch.setattr(daemon, "MAC", True)
    monkeypatch.setattr(phone_server, "supervise", lambda d: called.append(d))
    d = _Daemon()
    d.settings = {"phone.enabled": "auto"}
    asyncio.run(daemon.Daemon._serve_phone(d))
    assert called == []



# --- review fixes ----------------------------------------------------------------------------------

def test_shared_text_is_content_not_the_users_words(server):
    """A shared "yes" (a message from another app, say) is quoted content: it can't be read as the user's own
    yes to a question that is waiting (the daemon also skips its yes/no shortcut for phone-share)."""
    client, d, _ = server
    _, token = _paired()
    client.post("/v1/share", headers=_auth(token), json={"id": "s1", "kind": "text", "action": "ask", "text": "yes"})
    (_, ask, source), = d.asked
    assert source == "phone-share" and ask != "yes" and "<shared>\nyes\n</shared>" in ask
    assert "not their instructions" in ask


def test_a_clipboard_share_keeps_the_text_exactly(server, monkeypatch):
    client, d, _ = server
    _, token = _paired()
    copied = []
    monkeypatch.setattr(phone_server.subprocess, "run", lambda cmd, input=None, **k: copied.append((cmd, input)))
    code = "    def f():\n        return 1\n" + "x" * 5000 + "\n"
    r = client.post("/v1/share", headers=_auth(token), json={"id": "c1", "kind": "text", "action": "clipboard", "text": code})
    assert r.json() == {"status": "ok"} and copied == [(["wl-copy"], code.encode())]


def test_pairing_a_phone_doesnt_hide_a_revoke_from_the_sweeper(server):
    """Phone A is revoked, then B pairs before the sweeper looks: A's socket is still closed."""
    client, d, hub = server
    a, _ = _paired("A")
    conn = _conn(a["id"])
    hub.conns.add(conn)
    phone.revoke(a["id"])
    code, _ = phone.start_pairing()
    assert client.post("/v1/pair", json={"code": code, "name": "B"}).status_code == 200
    hub.check()
    assert {"type": "revoked"} in _drain(conn)


def test_an_old_stop_doesnt_detach_a_newer_listen():
    class D(_Daemon):
        async def phone_talk_stop(self, device):
            await asyncio.sleep(0.05)              # waiting for the transcript
            return "stopped"

    async def go():
        hub = phone_server.Hub(D())
        conn = _conn("dev")
        conn.audio = first = asyncio.Queue()
        stop = asyncio.create_task(hub.request(conn, {"type": "talk.stop", "id": "t1"}))
        await asyncio.sleep(0.01)
        conn.audio = second = asyncio.Queue()       # a new listen started meanwhile
        await stop
        return conn.audio, first, second

    now, first, second = asyncio.run(go())
    assert now is second and now is not first


def test_a_phone_far_behind_gets_every_kept_inbox_item():
    """The inbox keeps up to 2 * KEEP items: a sync sends all of them after the cursor, not the first 200."""
    for i in range(phone.KEEP + 50):
        phone.add("text", content=f"note {i}")
    hub = phone_server.Hub(_Daemon())
    conn = _conn("dev")
    asyncio.run(hub.request(conn, {"type": "sync", "after": 0}))
    assert len([m for m in _drain(conn) if m["type"] == "inbox"]) == phone.KEEP + 50


def test_send_to_phone_asks_before_sending_a_link_with_a_token(rt):
    _paired()
    rt.set_level("free")
    asked = json.loads(rt.call(SESSION, "send_to_phone",
                               {"kind": "link", "content": "https://example.com/reset?token=private-reset-token"})[0])
    assert asked["status"] == "needs_confirmation" and phone.items() == []


def test_doctor_checks_each_path_through_the_firewall(monkeypatch, tmp_path):
    from auren import doctor
    rules = tmp_path / "user.rules"
    monkeypatch.setattr(doctor, "UFW_RULES", rules)
    monkeypatch.setattr(doctor, "_run", lambda cmd: (0, "active"))
    rules.write_text("-A ufw-user-input -i tailscale0 -p tcp --dport 8735 -j ACCEPT\n")
    lan = doctor.firewall_check(8735, ["192.168.0.105"], tailscale=True)
    assert lan.status == doctor.WARN and "Wi-Fi/LAN" in lan.detail
    assert "from 192.168.0.0/16" in lan.fix and "tailscale0" not in lan.fix
    rules.write_text("-A ufw-user-input -p tcp --dport 8735 -s 10.0.0.0/8 -j ACCEPT\n")     # another subnet
    assert doctor.firewall_check(8735, ["192.168.0.105"], tailscale=False).status == doctor.WARN
    rules.write_text("-A ufw-user-input -p tcp --dport 8735 -j ACCEPT\n")                    # from anywhere
    assert doctor.firewall_check(8735, ["192.168.0.105"], tailscale=True).status == doctor.OK


def test_phone_pair_refuses_a_dead_daemons_status(monkeypatch, capsys):
    """A daemon that died left on: true behind: no QR code for a server that isn't there."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("auren.settings.current", lambda: {"phone.enabled": "auto"})
    monkeypatch.setattr(phone, "SERVE_WAIT", 0.1)
    monkeypatch.setattr(phone, "status", lambda: {"on": True, "port": 8735, "fp": "ab" * 32, "pid": 2**22 + 7,
                                                  "addrs": [{"ip": "192.168.1.5", "via": "lan", "ok": True}]})
    assert phone.main(["pair", "--no-wait", "--no-qr"]) == 1
    out = capsys.readouterr()
    assert "auren://" not in out.out and "daemon isn't running" in out.err and not phone.pairing_open()


def test_a_phone_starts_a_fresh_conversation_and_sets_the_brain(server):
    """The bar panel's Fresh, Who answers and Effort, from the phone: the same daemon commands."""
    client, d, _ = server
    _, token = _paired()
    sent, forgot = [], []

    async def command(line):
        sent.append(line)
        name, value = line.split(" ", 1)
        return line if value != "nope" else f"usage: {name} …"

    async def forget(say=True, turn=None):
        forgot.append(say)

    d.command, d.forget = command, forget
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        ws.receive_json()
        ws.send_json({"type": "forget", "id": "f1"})
        assert ws.receive_json() == {"type": "ack", "id": "f1", "status": "ok"}
        for i, (name, value) in enumerate([("brain", "codex"), ("effort", "high"), ("codex-effort", "minimal")]):
            ws.send_json({"type": "setting", "id": f"s{i}", "name": name, "value": value})
            assert ws.receive_json() == {"type": "ack", "id": f"s{i}", "status": "ok"}
        ws.send_json({"type": "setting", "id": "bad", "name": "effort", "value": "nope"})
        assert ws.receive_json()["status"] == "error: usage: effort …"
        # Not safety (a lost phone can't turn off the questions), nor any other command, nor a second word.
        for i, (name, value) in enumerate([("safety", "never"), ("quit", "now"), ("brain", "codex; quit"), ("brain", "")]):
            ws.send_json({"type": "setting", "id": f"x{i}", "name": name, "value": value})
            assert ws.receive_json()["status"].startswith("error: can't set")
    assert forgot == [False]
    assert sent == ["brain codex", "effort high", "codex-effort minimal", "effort nope"]


def test_the_state_the_phone_gets_has_the_brain_settings():
    phone_server.STATE_FILE.write_text(json.dumps({"state": "idle", "mode": "auto", "brain": "claude", "effort": "low",
                                                   "codexEffort": "low", "codexModel": "gpt-6-luna", "pid": 1}))
    snap = phone_server.Hub(_Daemon()).snapshot()
    assert {k: snap[k] for k in ("mode", "brain", "effort", "codexEffort", "codexModel")} == {
        "mode": "auto", "brain": "claude", "effort": "low", "codexEffort": "low", "codexModel": "gpt-6-luna"}
    assert "pid" not in snap


def test_the_status_lists_the_phones_connected_now(server):
    """The bar panel shows which phone is connected, and over which path."""
    client, _, _ = server
    dev, token = _paired("Pixel")
    phone_server._write_status(True, 8735, [{"ip": "192.168.1.5", "via": "lan", "ok": True}], "fp")
    assert phone.status()["connected"] == []
    with client.websocket_connect("/v1/ws", headers=_auth(token)) as ws:
        ws.receive_json()
        assert phone.status()["connected"] == [{"id": dev["id"], "name": "Pixel", "via": "lan"}]
    assert phone.status()["connected"] == []
    phone_server._write_status(False, 8735, why="the phone server stopped")
    assert phone.status()["connected"] == []


def test_doctor_sees_a_rule_for_every_port_and_tailscales_own(monkeypatch, tmp_path):
    """ufw allow from 192.168.31.0/24 opens every port to the LAN; Tailscale's own rules let tailscale0 in."""
    from auren import doctor
    rules = tmp_path / "user.rules"
    monkeypatch.setattr(doctor, "UFW_RULES", rules)
    prefs = {"NetfilterMode": 2}
    monkeypatch.setattr(doctor, "_run", lambda cmd: (0, json.dumps(prefs)) if cmd[0] == "tailscale" else (0, "active"))
    rules.write_text("-A ufw-user-input -s 192.168.31.0/24 -j ACCEPT\n")
    assert doctor.firewall_check(8735, ["192.168.31.214"], tailscale=True).status == doctor.OK
    for other in ("-A ufw-user-input -p udp -s 192.168.31.0/24 -j ACCEPT",           # UDP only
                  "-A ufw-user-input -p tcp --dport 22 -s 192.168.31.0/24 -j ACCEPT",  # another port
                  "-A ufw-user-input -p udp -d 172.17.0.1 --dport 53 -s 192.168.0.0/16 -j ACCEPT"):
        rules.write_text(other + "\n")
        assert doctor.firewall_check(8735, ["192.168.31.214"], tailscale=True).status == doctor.WARN
    rules.write_text("-A ufw-user-input -p tcp -m multiport --dports 8000,8735 -j ACCEPT\n")
    assert doctor.firewall_check(8735, ["192.168.31.214"], tailscale=True).status == doctor.OK
    prefs["NetfilterMode"] = 1                                       # nodivert: ufw decides for tailscale0 too
    rules.write_text("-A ufw-user-input -s 192.168.31.0/24 -j ACCEPT\n")
    ts = doctor.firewall_check(8735, ["192.168.31.214"], tailscale=True)
    assert ts.status == doctor.WARN and "Tailscale" in ts.detail and "tailscale0" in ts.fix
