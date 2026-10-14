"""Hands-free: say "Auren" instead of pressing Super+A (decision 0004).

While hands-free is on, the microphone stays open (one pw-record, named "auren-mic") and a small keyword
spotter runs on this laptop: sherpa-onnx with a 3.3M-parameter zipformer, about 2% of one core. Nothing
goes to the speech service until it hears the name. Then the daemon listens as after Super+A, with the
audio from a moment before the name, and the turn ends when you pause instead of on a second press.

    hf = HandsFree(on_wake, on_audio, on_change)
    await hf.start()        # mic open, listening for the name
    hf.stop()

The spotter alone is easily fooled by names that sound alike ("Lauren", "Warren"), so the daemon checks
the transcript too (`addressed`): what was said must start with the name. Other checks live here as well:
`strip_wake` takes the name off the front of a request, `mentions` tells the daemon when its own voice is
saying the name (the speakers reach the mic), and `mic_busy` pauses the spotter while another app records
(a dictation app, a call), since what you say there isn't meant for Auren.

The model (~6 MB of a 17 MB download) is fetched by `auren wake-model` into
~/.local/share/auren/models/kws-gigaspeech, checked against a pinned SHA-256.
"""

from __future__ import annotations

import asyncio
import collections
import hashlib
import json
import logging
import queue
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import MODELS_DIR, RUNTIME_DIR

log = logging.getLogger("auren.wake")

RATE, CHUNK = 16000, 1600            # bytes per read: 50 ms of 16-bit mono, the same format as the ears
PREROLL = 1.5                        # seconds of audio before the name, sent with it for the transcript check
MIC_NAME = "auren-mic"               # our pw-record's node name: mic_busy() doesn't count it
BUSY_EVERY = 2.0                     # seconds between checks for other apps recording

MODEL = "kws-gigaspeech"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/kws-models/"
             "sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01.tar.bz2")
MODEL_SHA256 = "f170013b4716e41b62b9bfd809687c207cef798ef9bc6534d524e17af9b6561a"
_STEM = "epoch-12-avg-2-chunk-16-left-64.int8.onnx"
MODEL_FILES = {"tokens.txt": "tokens.txt", f"encoder-{_STEM}": "encoder.onnx", f"decoder-{_STEM}": "decoder.onnx",
               f"joiner-{_STEM}": "joiner.onnx"}

# Spellings of how "Auren" can sound, in the model's word pieces (its bpe.model). Measured on Supertonic
# voices and the user's own recordings (decision 0004): OREN and ORAN catch most; ORAN also fires on
# "Lauren" and "Warren", which the transcript check turns away.
KEYWORDS = ["▁A URE N @AUREN", "▁OR EN @OREN", "▁OR IN @ORIN", "▁OR AN @ORAN", "▁ UR AN @URAN", "▁A W RE N @AWREN"]
# name: (threshold, boost). Lower thresholds hear the name more often, and other words for it too.
SENSITIVITY = {"low": (0.3, 1.0), "medium": (0.2, 1.5), "high": (0.12, 2.0)}

# How speech-to-text may write the name (its vocabulary has "Auren", but not every time).
_NAME = r"(?:auren|oren|orin|oran|auran|aurin|awren|orren|aaron)"
_FILLER = r"(?:hey|hi|ok|okay|oh|um+|uh+|so|yo|and)"
_ADDRESSED = re.compile(rf"^\W*(?:{_FILLER}\W+){{0,2}}{_NAME}\b(?!['’]s)", re.I)     # "Auren's voice..." is about it
_LEADING = re.compile(rf"^\W*(?:{_FILLER}\W+){{0,2}}{_NAME}\b(?!['’]s)\W*", re.I)
_MENTION = re.compile(rf"\b{_NAME}\b|auren", re.I)


def addressed(text: str) -> bool:
    """Does the transcript start with the name ("Auren, ...", "Hey Auren ...", "Um, Oren")?"""
    return bool(_ADDRESSED.match(text))


def strip_wake(text: str) -> str:
    """The request without the name in front: "Auren, open Firefox" -> "open Firefox"."""
    rest = _LEADING.sub("", text, count=1).strip()
    return rest[:1].upper() + rest[1:] if rest != text.strip() else rest


def mentions(text: str) -> bool:
    """Does this sentence say the name (or something the spotter takes for it)?"""
    return bool(text and (_MENTION.search(text) or re.search(r"lauren|warren", text, re.I)))


# --- the model ---------------------------------------------------------------------------------------


class WakeModelMissing(RuntimeError):
    pass


def model_dir() -> Path | None:
    d = MODELS_DIR / MODEL
    return d if all((d / name).is_file() for name in MODEL_FILES.values()) else None


def download_model(log: Callable[[str], None] = print) -> Path:
    """Fetch the spotter's model into MODELS_DIR (if it isn't there yet) and return its folder."""
    if found := model_dir():
        return found
    target = MODELS_DIR / MODEL
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    log("downloading the wake-word model (17 MB)")
    with tempfile.TemporaryDirectory(dir=MODELS_DIR) as tmp:
        archive = Path(tmp) / "model.tar.bz2"
        try:
            with urllib.request.urlopen(MODEL_URL, timeout=60) as r, archive.open("wb") as f:
                shutil.copyfileobj(r, f)
        except OSError as e:
            raise WakeModelMissing(f"couldn't download the wake-word model: {e}") from None
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != MODEL_SHA256:
            raise WakeModelMissing(f"the wake-word model download doesn't match its checksum ({digest[:12]}…)")
        out = Path(tmp) / MODEL
        out.mkdir()
        with tarfile.open(archive) as t:
            members = {Path(m.name).name: m for m in t.getmembers() if m.isfile()}
            for src, name in MODEL_FILES.items():
                if src not in members:
                    raise WakeModelMissing(f"the wake-word model has no {src}")
                with t.extractfile(members[src]) as f, (out / name).open("wb") as dst:
                    shutil.copyfileobj(f, dst)
        shutil.rmtree(target, ignore_errors=True)
        out.rename(target)
    return target


class Spotter:
    """The keyword spotter: feed it 16 kHz float samples, it says when it heard the name."""

    def __init__(self, sensitivity: str = "medium", folder: Path | None = None):
        import sherpa_onnx

        folder = folder or model_dir()
        if folder is None:
            raise WakeModelMissing("the wake-word model isn't downloaded: run `auren wake-model`")
        threshold, boost = SENSITIVITY.get(sensitivity, SENSITIVITY["medium"])
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        words = RUNTIME_DIR / "wake-keywords.txt"
        words.write_text("\n".join(KEYWORDS) + "\n")
        self.kws = sherpa_onnx.KeywordSpotter(
            tokens=str(folder / "tokens.txt"), encoder=str(folder / "encoder.onnx"),
            decoder=str(folder / "decoder.onnx"), joiner=str(folder / "joiner.onnx"), num_threads=1,
            keywords_file=str(words), keywords_score=boost, keywords_threshold=threshold, provider="cpu")
        self.stream = self.kws.create_stream()

    def feed(self, samples: np.ndarray) -> str | None:
        self.stream.accept_waveform(RATE, samples)
        heard = None
        while self.kws.is_ready(self.stream):
            self.kws.decode_stream(self.stream)
            if word := self.kws.get_result(self.stream):
                heard = word
                self.kws.reset_stream(self.stream)
        return heard


def samples(chunk: bytes) -> np.ndarray:
    return np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0


# --- other apps on the mic ------------------------------------------------------------------------------


def mic_busy() -> bool:
    """Is another app recording from a microphone (not a monitor of the speakers)? Auren's own streams
    don't count."""
    try:
        outs = json.loads(subprocess.run(["pactl", "-f", "json", "list", "source-outputs"], capture_output=True,
                                         text=True, timeout=3).stdout or "[]")
        sources = json.loads(subprocess.run(["pactl", "-f", "json", "list", "sources"], capture_output=True,
                                            text=True, timeout=3).stdout or "[]") if outs else []
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False
    monitors = {s.get("index") for s in sources if str(s.get("name", "")).endswith(".monitor")
                or s.get("properties", {}).get("device.class") == "monitor"}
    for o in outs:
        props = o.get("properties", {})
        if str(props.get("node.name", "")).startswith("auren") or o.get("corked") or o.get("source") in monitors:
            continue
        if props.get("stream.monitor") == "true" or "peak detect" in str(props.get("media.name", "")).lower():
            continue                                  # level meters (pavucontrol and friends)
        return True
    return False


# --- earcon ---------------------------------------------------------------------------------------------


def chime_samples(rate: int = 44100) -> np.ndarray:
    """Two soft rising notes, ~0.18 s: "I heard my name"."""
    out = []
    for freq, dur in ((660.0, 0.07), (990.0, 0.11)):
        t = np.arange(int(rate * dur)) / rate
        fade = np.minimum(1, np.minimum(t, dur - t) / 0.012)
        out.append(0.22 * np.sin(2 * np.pi * freq * t) * fade)
    return np.concatenate(out).astype(np.float32)


def chime() -> None:
    """Play the chime without waiting for it."""
    def play():
        try:
            subprocess.run(["pw-cat", "--playback", "--raw", "--format", "f32", "--channels", "1", "--rate", "44100",
                            "--media-role", "Notification", "-"], input=chime_samples().tobytes(),
                           stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            pass
    threading.Thread(target=play, daemon=True).start()


# --- the loop -------------------------------------------------------------------------------------------


class HandsFree:
    """The open mic and the spotter. Every chunk goes to `on_audio` (the daemon passes it on to a listen in
    progress); the spotter's hits go to `on_wake(word)` on the event loop; `on_change` when it pauses or
    resumes (another app took the mic, or gave it back)."""

    def __init__(self, on_wake: Callable[[str], None], on_audio: Callable[[bytes], None] | None = None,
                 on_change: Callable[[], None] | None = None, sensitivity: str = "medium",
                 pause_when_busy: bool = True, spotter: Spotter | None = None, source: list[str] | None = None):
        self.on_wake, self.on_audio, self.on_change = on_wake, on_audio, on_change
        self.sensitivity, self.pause_when_busy = sensitivity, pause_when_busy
        self.spotter = spotter
        self.source = source or []                 # extra pw-record args, for tests
        self.paused = False
        self.mic: asyncio.subprocess.Process | None = None
        self._ring: collections.deque[bytes] = collections.deque(maxlen=int(PREROLL * RATE * 2 / CHUNK) + 1)
        self._work: queue.Queue[bytes | None] = queue.Queue(maxsize=50)
        self._tasks: list[asyncio.Task] = []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    @property
    def status(self) -> str:
        return "paused" if self.paused else "on"

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.spotter is None:
            self.spotter = await asyncio.to_thread(Spotter, self.sensitivity)
        self._running = True
        self._thread = threading.Thread(target=self._spot, name="auren-wake", daemon=True)
        self._thread.start()
        await self._open_mic()
        self._tasks = [asyncio.create_task(self._read())]
        if self.pause_when_busy:
            self._tasks.append(asyncio.create_task(self._watch_mic()))

    async def _open_mic(self) -> None:
        self.mic = await asyncio.create_subprocess_exec(
            "pw-record", "-P", f'{{ node.name = "{MIC_NAME}" application.name = "Auren" }}', *self.source,
            "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)

    async def _read(self) -> None:
        while self._running:
            try:                                           # whole chunks: the pre-roll is counted in them
                chunk = await self.mic.stdout.readexactly(CHUNK) if self.mic else b""
            except asyncio.IncompleteReadError:
                chunk = b""
            if not chunk:                                  # pw-record died (the device went away): reopen
                if not self._running:
                    return
                log.warning("microphone closed; reopening in 1 s")
                await asyncio.sleep(1)
                if self._running:
                    await self._open_mic()
                continue
            self._ring.append(chunk)
            if self.on_audio:
                self.on_audio(chunk)
            if not self.paused:
                try:
                    self._work.put_nowait(chunk)
                except queue.Full:                         # the spotter fell behind: skip, don't pile up
                    pass

    def _spot(self) -> None:
        while (chunk := self._work.get()) is not None:
            try:
                word = self.spotter.feed(samples(chunk))
            except Exception as e:  # noqa: BLE001 - one bad chunk mustn't end hands-free
                log.warning("wake word: %s", e)
                continue
            if word and self._running and self._loop:
                self._loop.call_soon_threadsafe(self.on_wake, word)

    async def _watch_mic(self) -> None:
        while self._running:
            busy = await asyncio.to_thread(mic_busy)
            if busy != self.paused:
                self.paused = busy
                log.info("hands-free %s", "paused: another app is using the microphone" if busy else "resumed")
                if self.on_change:
                    self.on_change()
            await asyncio.sleep(BUSY_EVERY)

    def preroll(self) -> list[bytes]:
        """The last PREROLL seconds of audio (with the name in it, just heard)."""
        return list(self._ring)

    def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            if not t.done():
                t.cancel()
        self._tasks = []
        mic, self.mic = self.mic, None
        if mic and mic.returncode is None:
            try:
                mic.send_signal(signal.SIGINT)
            except ProcessLookupError:
                pass
            if self._loop and not self._loop.is_closed():  # it should be gone in a moment; if not, kill it
                self._loop.call_later(2, lambda: mic.returncode is None and mic.kill())
        try:
            self._work.put_nowait(None)
        except queue.Full:
            with self._work.mutex:
                self._work.queue.clear()
            self._work.put_nowait(None)


def test(seconds: float = 60, sensitivity: str = "medium") -> int:
    """`auren wake-test`: say the name a few times and see what the spotter hears (it runs alongside the
    daemon; nothing is sent anywhere)."""
    async def run():
        start = time.monotonic()

        def heard(word: str) -> None:
            print(f"  {time.monotonic() - start:5.1f}s  heard {word.lower()}", flush=True)

        hf = HandsFree(heard, sensitivity=sensitivity, pause_when_busy=False)
        await hf.start()
        print(f"Listening for “Auren” for {seconds:.0f} s (sensitivity {sensitivity}). Say it, and other things too.")
        try:
            await asyncio.sleep(seconds)
        finally:
            hf.stop()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except WakeModelMissing as e:
        print(e)
        return 1
    return 0
