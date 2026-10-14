"""The ears: AssemblyAI streaming (Universal-3.5 Pro), push-to-talk (decision 0002).

    session = Listening(vocab)
    await session.start()        # key pressed: mic + websocket open, partial text arrives
    text = await session.finish()  # key released: ForceEndpoint, final text in ~0.3 s

Settings that matter (all measured in experiments/stt):
- speech_model universal-3-5-pro (3.6 Pro answered in Hindi script on this voice)
- min/max_turn_silence 10000 so pauses never end the turn; the key release does (ForceEndpoint)
- keyterms_prompt (≤100 terms) in the URL; UpdateConfiguration closes the session
- audio messages 50-1000 ms; the connection is open only while the key is held (billed per second)

Hands-free (wake.py) is the same turn with two differences: the audio comes from its open mic
(`external`, pushed with `push()`) instead of a pw-record of its own, and a pause ends the turn
(`turn_silence`): each final turn sets `turn`, and the daemon decides whether it was the whole request.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import time
from collections.abc import Callable
from urllib.parse import urlencode

import numpy as np
import websockets

from .config import CONFIG_DIR, DATA, secret
from .live import loudness

USER_VOCAB = CONFIG_DIR / "vocab.txt"     # the user's own words (names, projects), most important first
RATE, CHUNK = 16000, 1600                  # 50 ms of 16-bit mono
MIN_SEND, MAX_SEND = 1600, 32000           # AssemblyAI wants 50-1000 ms per message
URL = "wss://streaming.assemblyai.com/v3/ws"


def read_terms(path) -> list[str]:
    """The words in a vocabulary file: one per line, blank lines and # comments skipped."""
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return []
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def vocabulary(profile=None, limit: int = 100) -> list[str]:
    """Terms to bias recognition toward: the user's own (~/.config/auren/vocab.txt), the base list, then
    names from this laptop."""
    terms = read_terms(USER_VOCAB) + read_terms(DATA / "vocab.txt")
    own = len(terms)             # what the user chose is kept as typed; only names from the laptop are filtered
    if profile is not None:      # most useful first: bookmarks, web apps, projects, then apps
        s = profile.static()
        for names in (s.get("bookmarks") or {}).values() if isinstance(s.get("bookmarks"), dict) else []:
            terms += [n.split("/")[-1] for n in names]
        apps = s.get("apps", {}) if isinstance(s.get("apps"), dict) else {}
        terms += [a["name"] for a in apps.get("webapps", [])]
        terms += [p["name"] for p in s.get("projects", [])[:15]] if isinstance(s.get("projects"), list) else []
        terms += [a["name"] for a in apps.get("apps", [])]
    seen, out = set(), []
    for i, t in enumerate(terms):
        k = t.lower()
        if k not in seen and 2 <= len(t) <= 40 and (i < own or not k.startswith(("libreoffice ", "openjdk"))):
            seen.add(k)
            out.append(t)
    return out[:limit]


class Listening:
    """One push-to-talk turn."""

    def __init__(self, vocab: list[str], on_partial: Callable[[str], None] | None = None, source: list[str] | None = None,
                 on_level: Callable[[float], None] | None = None, external: bool = False,
                 turn_silence: tuple[int, int] | None = None):
        self.vocab, self.on_partial, self.on_level = vocab, on_partial, on_level
        self.source = source or []           # extra pw-record args (e.g. --target) for testing
        self.external = external             # audio comes through push() (the hands-free mic), not our own pw-record
        self.turn_silence = turn_silence     # (min, max) ms of silence that end a turn; None: only the key does
        self.hands: str | None = None        # set by the daemon: "wake" or "follow-up" for a hands-free listen
        self.check = False                   # set by the daemon: the transcript must start with the name
        self.mic: asyncio.subprocess.Process | None = None
        self.ws = None
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.finals: list[str] = []
        self.partial = ""
        self.opened_at = 0.0
        self.released_at = 0.0
        self._tasks: list[asyncio.Task] = []
        self._sender: asyncio.Task | None = None
        self._done = asyncio.Event()
        self._final = asyncio.Event()     # a final turn arrived after the key was released
        self.turn = asyncio.Event()       # a final turn arrived (set each time; the daemon clears it)
        self.speaking = False             # partial text since the last final turn: words still coming
        self.error: str | None = None

    async def start(self) -> None:
        key = secret("ASSEMBLYAI_API_KEY")
        if not key:
            raise RuntimeError("no AssemblyAI key (ASSEMBLYAI_API_KEY in ~/.config/auren/secrets.env)")
        self.opened_at = time.perf_counter()
        # The mic starts first so nothing said while the socket connects is lost.
        if not self.external:
            self.mic = await asyncio.create_subprocess_exec(
                "pw-record", "-P", '{ node.name = "auren-ears" application.name = "Auren" }', *self.source, "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            self._tasks.append(asyncio.create_task(self._read_mic()))
        low, high = self.turn_silence or (10000, 10000)
        params = {"sample_rate": RATE, "encoding": "pcm_s16le", "speech_model": "universal-3-5-pro",
                  "min_turn_silence": low, "max_turn_silence": high}
        if self.vocab:
            params["keyterms_prompt"] = json.dumps(self.vocab[:100])
        self._tasks.append(asyncio.create_task(self._stream(f"{URL}?{urlencode(params)}", key)))

    async def _read_mic(self) -> None:
        while chunk := await self.mic.stdout.read(CHUNK):
            self.push(chunk)
        await self.queue.put(None)

    def push(self, chunk: bytes) -> None:
        """Audio for this turn (16 kHz s16 mono): from our own mic, or the hands-free one."""
        if self.external and self.released_at:      # our own mic's tail still goes in after the release
            return
        self.queue.put_nowait(chunk)
        if self.on_level and len(chunk) >= 2:
            samples = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype=np.int16) / 32768.0
            self.on_level(loudness(samples))

    async def _stream(self, url: str, key: str) -> None:
        try:
            async with websockets.connect(url, additional_headers={"Authorization": key}, open_timeout=10) as ws:
                self.ws = ws
                await ws.recv()                                   # Begin

                async def send():
                    pending = bytearray()
                    while (chunk := await self.queue.get()) is not None:
                        pending.extend(chunk)
                        while len(pending) >= MIN_SEND:
                            n = min(len(pending), MAX_SEND)
                            await ws.send(bytes(pending[:n]))
                            del pending[:n]
                    if pending:                                   # pad the tail to the 50 ms minimum
                        await ws.send(bytes(pending) + b"\0" * (MIN_SEND - len(pending)))
                    await ws.send(json.dumps({"type": "ForceEndpoint"}))
                    await ws.send(json.dumps({"type": "Terminate"}))

                sender = self._sender = asyncio.create_task(send())
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "Turn":
                        text = msg.get("transcript", "")
                        if msg.get("end_of_turn"):
                            self.finals.append(text.strip())
                            self.speaking = False
                            self.turn.set()
                            if self.released_at:
                                self._final.set()
                        elif text:
                            self.partial, self.speaking = text, True
                            if self.on_partial:
                                self.on_partial(text)
                    elif msg.get("type") == "Termination":
                        break
                    elif msg.get("type") == "Error" or "error" in msg:
                        self.error = str(msg)[:200]
                if not sender.done():       # the service closed first: don't wait for audio that may not come
                    sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
        except Exception as e:  # noqa: BLE001 - reported to the user, never crashes the daemon
            self.error = f"{type(e).__name__}: {e}"[:200]
        finally:
            if self._sender and not self._sender.done():
                self._sender.cancel()
            self._done.set()

    async def finish(self, timeout: float = 8) -> str:
        """Key released: stop the mic, let the last audio and ForceEndpoint through, return the text."""
        self.released_at = time.perf_counter()
        if self.mic and self.mic.returncode is None:
            self.mic.send_signal(signal.SIGINT)
        if self.external:
            self.queue.put_nowait(None)
        if self.turn_silence and self.finals and not self.speaking:
            await self.cancel()              # a pause ended the turn and nothing came after: it's all here
            return " ".join(f for f in self.finals if f).strip()
        # Return as soon as the final turn is in; don't wait for the connection to close.
        waits = [asyncio.create_task(self._final.wait()), asyncio.create_task(self._done.wait())]
        done, pending = await asyncio.wait(waits, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        if not done and not self.finals:
            self.error = self.error or "speech service didn't answer in time"
        await self.cancel()
        return " ".join(f for f in self.finals if f).strip()

    async def cancel(self) -> None:
        if self.external:
            self.released_at = self.released_at or time.perf_counter()
        ws = self.ws
        if ws is not None and not self._done.is_set():
            try:                             # end the session properly (it's billed until it ends)
                await asyncio.wait_for(ws.send(json.dumps({"type": "Terminate"})), 1)
            except Exception:  # noqa: BLE001 - closing it below ends it too
                pass
        if self.mic and self.mic.returncode is None:
            self.mic.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self.mic.wait(), 2)
            except TimeoutError:
                self.mic.kill()
        for t in self._tasks:
            if not t.done():
                t.cancel()

    @property
    def closed(self) -> bool:
        """The connection ended (the service finished, or failed: see `error`)."""
        return self._done.is_set()

    @property
    def held_seconds(self) -> float:
        return (self.released_at or time.perf_counter()) - self.opened_at
