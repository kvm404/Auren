"""The phone remote: pairing and tokens, the inbox and its cursor, send_to_phone, the server's API, and one
turn at a time across the desk and the phone."""

import asyncio
import json
import os
import stat
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
                        "INBOX": d / "inbox.jsonl", "FILES": d / "files", "STATUS": tmp_path / "phone.json"}.items():
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


def test_pair_needs_the_daemons_server(capsys):
    assert phone.main(["pair", "--no-wait"]) == 1
    assert "phone server is off" in capsys.readouterr().err
    phone.STATUS.write_text(json.dumps({"on": True, "host": "100.100.1.2", "port": 8735}))
    assert phone.main(["pair", "--no-wait", "--no-qr"]) == 0
    uri = next(ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("auren://pair?"))
    assert "host=100.100.1.2" in uri and "port=8735" in uri and phone.pairing_open()


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
    listening = None
    state = "idle"
    settings: dict = {}

    def __init__(self):
        self.asked, self.cancelled, self.answers, self.busy = [], 0, [], False

    def _origin(self):
        return "desk" if self.busy else None

    def phone_ask(self, turn, text, source="phone"):
        if self.busy:
            return "busy"
        self.asked.append((turn.id, text, source))
        return "ok"

    async def phone_talk(self, turn, audio, hands_free=False, fresh=False):
        return "busy" if self.busy else "ok"

    async def phone_talk_stop(self, device):
        return "not listening"

    async def cancel(self):
        self.cancelled += 1

    def yes_no(self, yes, turn, source):
        self.answers.append(yes)
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
        assert ws.receive_json()["status"] == "approved 1" and d.answers == [True]


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
    assert d.asked == [("s1", "summarize\nhttps://a.b/c", "phone-share")]
    r = client.post("/v1/share", headers=_auth(token), json={"id": "s2", "kind": "link", "action": "ask", "text": "ftp://x"})
    assert r.json()["status"].startswith("error")


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
        self.replies = []

    def reply(self, turn, text):
        self.replies.append((turn.id, text))

    def state_changed(self):
        pass


class _Harness(daemon.Daemon):
    def __init__(self, tmp_path):
        self.settings = {"voice": True, "brain": "auto", "voiceName": "F2", "safety": "ask"}
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
        self.state = state

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
