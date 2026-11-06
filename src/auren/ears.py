"""The ears: AssemblyAI streaming (Universal-3.5 Pro), push-to-talk (decision 0002).

    session = Listening(vocab)
    await session.start()        # key pressed: mic + websocket open, partial text arrives
    text = await session.finish()  # key released: ForceEndpoint, final text in ~0.3 s

Connection settings (see docs/decisions/0002-speech-to-text.md):
- speech_model universal-3-5-pro (3.6 Pro answered in Hindi script on this voice)
- language_codes ["en"]: Auren is English-only. 3.5 Pro ignores it (Hindi speech still comes back in Devanagari);
  it is sent for the models that honor it
- min/max_turn_silence 10000 so pauses never end the turn; the key release does (ForceEndpoint)
- keyterms_prompt (≤100 terms) in the URL; UpdateConfiguration closes the session
- audio messages 50-1000 ms; the connection is open only while the key is held (billed per second)

`quiet_seconds` is how long no new words have come in (since the start, before the first word). The daemon
uses it to remind the user to send, or to send by itself (auto-send). It counts words, not loudness, so a
fan or music in the room doesn't keep a turn open. Only live (partial) words count: a final transcript comes
only after the key (ForceEndpoint) or 10 s of silence (the turn-silence setting), and the words it adds were
said before that silence. Those last words of a sentence often show up only in the final (~2 s of speech,
measured on the user's clips), so for up to TAIL seconds after the last new word, a mic that still sounds
like speech holds the clock too.
"""

from __future__ import annotations

import asyncio
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
TAIL = 2.5                                 # seconds after the last new word in which a loud mic still counts as talking
SPEECH_ABOVE = 0.2                         # loudness over the quietest moment so far that counts as speech (8 dB)


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


def connection_params(vocab: list[str]) -> dict:
    """The query parameters of the streaming connection."""
    params = {"sample_rate": RATE, "encoding": "pcm_s16le", "speech_model": "universal-3-5-pro",
              "language_codes": json.dumps(["en"]),
              "min_turn_silence": 10000, "max_turn_silence": 10000}
    if vocab:
        params["keyterms_prompt"] = json.dumps(vocab[:100])
    return params


class Listening:
    """One push-to-talk turn."""

    def __init__(self, vocab: list[str], on_partial: Callable[[str], None] | None = None, source: list[str] | None = None,
                 on_level: Callable[[float], None] | None = None):
        self.vocab, self.on_partial, self.on_level = vocab, on_partial, on_level
        self.source = source or []           # extra pw-record args (e.g. --target) for testing
        self.mic: asyncio.subprocess.Process | None = None
        self.ws = None
        self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.finals: list[str] = []
        self.partial = ""                    # the turn in progress (empty again once it's final)
        self.opened_at = 0.0
        self.words = 0                       # words heard so far: the final turns' and the most of the live one's
        self.last_word_at = 0.0              # when a live word last came in
        self._final_words = 0                # words in the final turns
        self._live_words = 0                 # the most words the live turn has had
        self.last_voice_at = 0.0             # when the mic last sounded like speech
        self.floor = 1.0                     # the quietest mic level so far (the room's noise)
        self.released_at = 0.0
        self._tasks: list[asyncio.Task] = []
        self._done = asyncio.Event()
        self._final = asyncio.Event()     # a final turn arrived after the key was released
        self.error: str | None = None

    async def start(self) -> None:
        key = secret("ASSEMBLYAI_API_KEY")
        if not key:
            raise RuntimeError("no AssemblyAI key (ASSEMBLYAI_API_KEY in ~/.config/auren/secrets.env)")
        self.opened_at = time.perf_counter()
        # The mic starts first so nothing said while the socket connects is lost.
        self.mic = await asyncio.create_subprocess_exec(
            "pw-record", *self.source, "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        self._tasks.append(asyncio.create_task(self._read_mic()))
        self._tasks.append(asyncio.create_task(self._stream(f"{URL}?{urlencode(connection_params(self.vocab))}", key)))

    async def _read_mic(self) -> None:
        while chunk := await self.mic.stdout.read(CHUNK):
            await self.queue.put(chunk)
            if len(chunk) >= 2:
                samples = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype=np.int16) / 32768.0
                level = loudness(samples)
                self._hear_level(level)
                if self.on_level:
                    self.on_level(level)
        await self.queue.put(None)

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

                sender = asyncio.create_task(send())
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") == "Turn":
                        text = msg.get("transcript", "")
                        if msg.get("end_of_turn"):
                            self.finals.append(text.strip())
                            self.partial = ""
                            self._turn_ended(text)
                            if self.released_at:
                                self._final.set()
                        elif text:
                            self.partial = text
                            self._heard_live(text)
                            if self.on_partial:          # after counting: the daemon reads quiet_seconds
                                self.on_partial(text)
                    elif msg.get("type") == "Termination":
                        break
                    elif msg.get("type") == "Error" or "error" in msg:
                        self.error = str(msg)[:200]
                await sender
        except Exception as e:  # noqa: BLE001 - reported to the user, never crashes the daemon
            self.error = f"{type(e).__name__}: {e}"[:200]
        finally:
            self._done.set()

    def _heard_live(self, text: str) -> None:
        """A live transcript of the turn in progress. More words than it had reset the quiet clock; a partial
        being revised (the same or fewer words) isn't the user speaking."""
        n = len(text.split())
        if n > self._live_words:
            self._live_words, self.last_word_at = n, time.perf_counter()
        self.words = self._final_words + self._live_words

    def _turn_ended(self, text: str) -> None:
        """A final transcript: counted as it is written (it may add the turn's last words, or write "twenty five"
        as "25"), but it doesn't reset the clock. It comes after the key or 10 s of silence, so its words were
        said before that."""
        self._final_words += len(text.split())
        self._live_words = 0
        self.words = self._final_words

    @property
    def text(self) -> str:
        """Everything heard so far: the final turns and the one in progress."""
        return " ".join(t for t in (*self.finals, self.partial) if t).strip()

    def _hear_level(self, level: float) -> None:
        if level > 0:                        # exact zeros are digital silence (the stream starting), not the room
            self.floor = min(self.floor, level)
        if level >= self.floor + SPEECH_ABOVE:
            self.last_voice_at = time.perf_counter()

    @property
    def quiet_seconds(self) -> float:
        """How long no new words have come in; before the first word, how long it has been listening. Up to
        TAIL after the last new word, a mic that still sounds like speech counts as words not written yet."""
        if not self.last_word_at:
            return time.perf_counter() - self.opened_at
        talking = min(self.last_voice_at, self.last_word_at + TAIL)
        return time.perf_counter() - max(self.last_word_at, talking)

    async def finish(self, timeout: float = 8) -> str:
        """Key released: stop the mic, let the last audio and ForceEndpoint through, return the text."""
        self.released_at = time.perf_counter()
        if self.mic and self.mic.returncode is None:
            self.mic.send_signal(signal.SIGINT)
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
    def held_seconds(self) -> float:
        return (self.released_at or time.perf_counter()) - self.opened_at
