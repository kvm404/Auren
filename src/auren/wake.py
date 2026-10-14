"""Hands-free: say "Auren" instead of pressing Super+A (decision 0004).

While hands-free is on, the microphone stays open (one pw-record, named "auren-mic") and a small streaming
speech-to-text model runs on this laptop: sherpa-onnx with a 20M-parameter zipformer, about 3% of one
core. Nothing goes to the speech service until its words have the name in them (`find_name`). Then the
daemon listens as after Super+A, with the audio from just before the name, and the turn ends when you
pause instead of on a second press.

    hf = HandsFree(on_wake, on_audio, on_change)
    await hf.start()        # mic open, listening for the name
    hf.stop()

The local model writes the name the way it hears it: "ORIN", "OR IN", "ORAN"..., and each voice has its
own. `Learner` ("Learn my voice") keeps how it hears *you* (handsfree.heard_as). It's also fooled by
words that sound alike, so the daemon checks the speech service's transcript too (`addressed`): what was
said must start with the name. Other checks live here as well: `strip_wake` takes the name off the front
of a request, `mentions` tells the daemon when its own voice is saying the name (the speakers reach the
mic), and `mic_busy` pauses the spotter while another app records (a dictation app, a call), since what
you say there isn't meant for Auren.

The model (44 MB) is fetched by `auren wake-model` into ~/.local/share/auren/models/asr-en-20m, each file
checked against a pinned SHA-256.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import hashlib
import json
import logging
import math
import os
import queue
import re
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import MODELS_DIR

log = logging.getLogger("auren.wake")

RATE, CHUNK = 16000, 1600            # bytes per read: 50 ms of 16-bit mono, the same format as the ears
STEP = CHUNK / 2 / RATE              # seconds per chunk
RING = 3.0                           # seconds of audio kept, for the pre-roll
LEAD = 0.7                           # pre-roll starts this long before the name's first word piece (they come late)
STABLE_STEPS = 1                     # a phrase's last word counts once a model step (~0.3 s) left it as it was
MIC_NAME = "auren-mic"               # our pw-record's node name: mic_busy() doesn't count it
BUSY_EVERY = 2.0                     # seconds between checks for other apps recording

MODEL = "asr-en-20m"
OLD_MODELS = ("kws-gigaspeech",)     # the keyword spotter used before: removed once this one is in
MODEL_URL = ("https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-en-20M-2023-02-17/resolve/"
             "d42f2d9f7ca24806fb667456a18a9f1b60f70d16/")
_STEM = "epoch-99-avg-1.int8.onnx"
# file in the model's repo: (saved as, SHA-256)
MODEL_FILES = {
    "tokens.txt": ("tokens.txt", "49e3c2646595fd907228b3c6787069658f67b17377c60aeb8619c4551b2316fb"),
    f"encoder-{_STEM}": ("encoder.onnx", "3810755ce7c3ab26b42a8bcf39d191308fa27fb0f53358823ba46141d03b7eb3"),
    f"decoder-{_STEM}": ("decoder.onnx", "21e2a2acd961b3ac72f55be2f10f1a285e1b0b0ba010d7c0b6eab141411b163c"),
    f"joiner-{_STEM}": ("joiner.onnx", "e085d73b593cf9b0707f370dbd656d58327d3fe36d80d849202ef81df02cb01e"),
}

# How the local model writes the name (upper case; measured on the user's voice and Supertonic's, decision
# 0004). One word: anywhere, so it can stop Auren mid-sentence. Two: only where a phrase starts ("or in
# the..." rarely does). Loose ones only on high.
NAME_WORDS = frozenset("AUREN AURIN AURAN ORIN OREN ORAN URAN UREN URIN ORREN ORRIN ORRAN OREAN OWREN AWREN".split())
NAME_PAIRS = frozenset({"OR IN", "OR AN", "OR EN", "ORE IN", "ORE AN", "ORE EN", "RE IN", "AU REN", "AUR IN",
                        "UR IN", "UR AN"})
NAME_LOOSE = frozenset({"ORN", "ARIN", "AREN", "ERIN", "ARE IN", "ARE AN", "R IN", "HORIN"})
FILLERS = frozenset("HEY HI HELLO OK OKAY O K OH UM UH SO YO".split())
PAUSE = 0.5                          # a gap this long between word pieces starts a new phrase
SENSITIVITY = ("low", "medium", "high")
# Too common to stand for the name on their own (Learn my voice won't keep them).
COMMON = frozenset("A AN AND ARE AS AT BE BUT BY DO FOR GO HE I IF IN IS IT ME MY NO NOT OF OK ON OR OUR SO THE "
                   "THIS TO UP US WE YES YOU HEY HI OH UM UH YEAH WHAT".split())

# How speech-to-text may write the name (its vocabulary has "Auren", but not every time).
_NAME = r"(?:auren|oren|orin|oran|auran|aurin|awren|orren|aaron|or[ -](?:in|an|en))"
_FILLER = r"(?:hey|hi|ok|okay|oh|um+|uh+|so|yo|and)"
_ADDRESSED = re.compile(rf"^\W*(?:{_FILLER}\W+){{0,2}}{_NAME}\b(?!['’]s)", re.I)     # "Auren's voice..." is about it
_LEADING = re.compile(rf"^\W*(?:{_FILLER}\W+){{0,2}}{_NAME}\b(?!['’]s)\W*", re.I)
_MENTION = re.compile(rf"\b{_NAME}\b|auren", re.I)
# A transcript starting with another name it hears as its own ("Hey Lauren"), or talking about it ("Auren's
# voice"), wasn't a call, however clear it sounded.
_OTHER = r"(?:lauren|laurin|lauryn|loren|lorraine|warren|darren|karen|sharon|florence)"
LOOKALIKE = re.compile(rf"^\W*(?:{_FILLER}\W+){{0,2}}(?:{_OTHER}\b|{_NAME}['’]s\b)", re.I)


def addressed(text: str) -> bool:
    """Does the transcript start with the name ("Auren, ...", "Hey Auren ...", "Um, Oren")?"""
    return bool(_ADDRESSED.match(text))


def strip_wake(text: str) -> str:
    """The request without the name in front: "Auren, open Firefox" -> "open Firefox"."""
    rest = _LEADING.sub("", text, count=1).strip()
    return rest[:1].upper() + rest[1:] if rest != text.strip() else rest


def mentions(text: str) -> bool:
    """Does this sentence say the name (or something the spotter takes for it)?"""
    return bool(text and (_MENTION.search(text) or re.search(r"lauren|warren|\bor in\b", text, re.I)))


# --- finding the name in what the local model heard ---------------------------------------------------


@dataclass
class Word:
    text: str          # upper case, as the model writes it ("'RE" keeps its apostrophe)
    at: float          # seconds into the stream of its first word piece
    last: float        # ... and of its last


def words_of(tokens: Iterable[str], times: Iterable[float], start: float = 0.0) -> list[Word]:
    """The model's word pieces (" OR", "IN") put together into words, with their times."""
    out: list[Word] = []
    for tok, t in zip(tokens, times, strict=False):
        if tok.startswith(" ") or not out:
            out.append(Word(tok.strip(), start + t, start + t))
        else:
            out[-1].text += tok
            out[-1].last = start + t
    return [w for w in out if w.text]


def spelling(text: str) -> str:
    """A phrase as the matcher compares it: upper case, letters and spaces ("'RE IN" -> "RE IN")."""
    return " ".join(re.sub(r"[^A-Z]", "", w) for w in text.upper().split() if re.sub(r"[^A-Z]", "", w))


def find_name(words: list[Word], sensitivity: str = "medium", mine: Iterable[str] = (), done: bool = False,
              after: int = 0) -> tuple[int, int, bool] | None:
    """Where the name is in a phrase the local model heard: word indexes [i, j), and whether a phrase starts
    there (a pause, or "hey", before it: a clear call, not a word that sounds like it in passing). `mine`: how it
    hears this user (Learn my voice). `done`: the phrase is over; until then its last word may still grow
    ("MORN" -> "MORNING"), so a name can't end it. Only words from `after` on count (the ones before were
    already reported)."""
    text = [spelling(w.text) for w in words]
    at_start = set(NAME_WORDS) | {spelling(m) for m in mine if m.strip()}
    if sensitivity != "low":
        at_start |= NAME_PAIRS
    if sensitivity == "high":
        at_start |= NAME_LOOSE
    longest = max((len(f.split()) for f in at_start), default=1)
    starts = {0} | {i for i in range(1, len(words)) if words[i].at - words[i - 1].last >= PAUSE}
    heads = set()                                  # where a phrase starts, after "hey", "ok"...
    for i in starts:
        while i < len(text) and text[i] in FILLERS:
            i += 1
        heads.add(i)
    for i in range(after, len(words)):
        forms = at_start if i in heads else NAME_WORDS
        word = text[i][3:] if text[i].startswith("HEY") and text[i][3:] in forms else text[i]   # "HEYORN"
        for n in range(min(longest, len(words) - i), 0, -1):
            if " ".join([word, *text[i + 1:i + n]]) in forms and (i + n < len(words) or done):
                return i, i + n, i in heads
    return None


class WakeModelMissing(RuntimeError):
    pass


def model_dir() -> Path | None:
    d = MODELS_DIR / MODEL
    return d if all((d / name).is_file() for name, _ in MODEL_FILES.values()) else None


def download_model(log: Callable[[str], None] = print) -> Path:
    """Fetch the local speech model into MODELS_DIR (if it isn't there yet) and return its folder."""
    if found := model_dir():
        return found
    target = MODELS_DIR / MODEL
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    log("downloading the wake-word model (44 MB)")
    with tempfile.TemporaryDirectory(dir=MODELS_DIR) as tmp:
        out = Path(tmp) / MODEL
        out.mkdir()
        for src, (name, sha) in MODEL_FILES.items():
            try:
                with urllib.request.urlopen(MODEL_URL + src, timeout=60) as r, (out / name).open("wb") as f:
                    shutil.copyfileobj(r, f)
            except OSError as e:
                raise WakeModelMissing(f"couldn't download the wake-word model: {e}") from None
            digest = hashlib.sha256((out / name).read_bytes()).hexdigest()
            if digest != sha:
                raise WakeModelMissing(f"the wake-word model's {name} doesn't match its checksum ({digest[:12]}…)")
        shutil.rmtree(target, ignore_errors=True)
        out.rename(target)
    for old in OLD_MODELS:
        shutil.rmtree(MODELS_DIR / old, ignore_errors=True)
    return target


class Spotter:
    """The local model, listening for the name: feed it 16 kHz float samples. Each feed returns what
    happened: ("name", "OR IN", seconds_ago, clear) when the name was heard (seconds_ago: where it
    started, counted back from the end of what was fed; clear: where a phrase starts, see find_name),
    ("said", "OR IN", seconds_ago) when a phrase ended (Learn my voice listens to those).
    `after_name`: how many words it heard after the last name, in the same phrase (0: a pause came)."""

    def __init__(self, sensitivity: str = "medium", mine: Iterable[str] = (), folder: Path | None = None):
        import sherpa_onnx

        folder = folder or model_dir()
        if folder is None:
            raise WakeModelMissing("the wake-word model isn't downloaded: run `auren wake-model`")
        self.rec = sherpa_onnx.OnlineRecognizer.from_transducer(
            tokens=str(folder / "tokens.txt"), encoder=str(folder / "encoder.onnx"),
            decoder=str(folder / "decoder.onnx"), joiner=str(folder / "joiner.onnx"), num_threads=1,
            sample_rate=RATE, decoding_method="greedy_search", provider="cpu", enable_endpoint_detection=True,
            rule1_min_trailing_silence=1.0, rule2_min_trailing_silence=0.6, rule3_min_utterance_length=15)
        self.sensitivity, self.mine = sensitivity, tuple(mine)
        self.after_name = 0
        self.restart()

    def restart(self) -> None:
        """Start over: after a gap in the audio (paused, or it fell behind), so a phrase isn't joined across it."""
        self.stream = self.rec.create_stream()
        self.fed = 0.0                              # seconds of audio in this stream
        self._reported = 0                          # words of this phrase already reported as the name
        self._in_hit = False                        # this phrase had the name: count the words after it
        self._text, self._still = "", 0             # the phrase so far, and model steps it hasn't changed for

    def feed(self, samples: np.ndarray) -> list[tuple]:
        self.stream.accept_waveform(RATE, samples)
        self.fed += len(samples) / RATE
        steps = 0
        while self.rec.is_ready(self.stream):
            self.rec.decode_stream(self.stream)
            steps += 1
        r = self.rec.get_result_all(self.stream)
        ended = self.rec.is_endpoint(self.stream)
        if r.text != self._text:
            self._text, self._still = r.text, 0
        else:
            self._still += steps                    # the model looked at more audio and the words stayed
        events: list = []
        if r.text:
            words = words_of(r.tokens, r.timestamps, r.start_time)
            hit = find_name(words, self.sensitivity, self.mine, done=ended or self._still >= STABLE_STEPS,
                            after=self._reported)
            if hit:
                i, j, clear = hit
                self._reported, self._in_hit = j, True
                events.append(("name", " ".join(w.text for w in words[i:j]), max(0.0, self.fed - words[i].at), clear))
            if self._in_hit:
                self.after_name = len(words) - self._reported
        if ended:
            if r.text.strip():
                events.append(("said", r.text.strip(), max(0.0, self.fed - r.start_time)))
            self.rec.reset(self.stream)
            self._reported, self._in_hit, self._text, self._still = 0, False, "", 0
        return events


# --- Learn my voice --------------------------------------------------------------------------------------


class Learner:
    """Learn my voice: the name said a few times, then something without it. How the local model wrote
    the name each time joins the user's own spellings (handsfree.heard_as, `mine` so far); any of them
    it also heard in the other sentence goes."""

    TIMES = 4

    def __init__(self, sensitivity: str = "medium", mine: Iterable[str] = ()):
        self.sensitivity = sensitivity
        self.mine = [spelling(m) for m in mine if spelling(m)]
        self.samples: list[str] = []         # what each "Auren" came out as
        self.caught = 0                      # ...how many of those it already took for the name
        self.step = "name"                   # name, other, done
        self.note = ""                       # a word of advice for the next try
        self.learned: list[str] = []          # every spelling to keep, when done
        self.added = 0                        # ...of them new

    def heard(self, text: str) -> None:
        """A phrase the local model heard (it ended at a pause)."""
        words = [w for w in spelling(text).split() if w not in FILLERS]
        if self.step == "name":
            if not words:
                return
            if len(words) > 3:
                self.note = "Just the name, on its own"
                return
            form = " ".join(words)
            self.samples.append(form)
            self.caught += find_name([Word(w, 0, 0) for w in words], self.sensitivity, done=True) is not None
            self.note = ""
            if len(self.samples) >= self.TIMES:
                self.step = "other"
        elif self.step == "other":
            if len(words) < 2:
                self.note = "A few words, without my name"
                return
            other = f" {' '.join(words)} "
            new = [f for f in dict.fromkeys(self.samples) if f not in self.mine
                   and (" " in f or (f not in COMMON and len(f) >= 3))]
            self.learned = [f.lower() for f in [*self.mine, *new] if f" {f} " not in other][-12:]
            self.added = sum(f.lower() in self.learned for f in new)
            self.step, self.note = "done", ""

    def state(self) -> dict:
        """For state.json: the bubble shows `prompt`, the panel the rest."""
        n, of = len(self.samples), self.TIMES
        if self.step == "name":
            prompt = (f"{self.note} ({n + 1} of {of})" if self.note else
                      f"Say “Auren”, the way you'd call me ({n + 1} of {of})" if n == 0 else
                      f"Got it. Again ({n + 1} of {of})")
        elif self.step == "other":
            prompt = self.note or "Now say something without my name, like “what's the weather today”"
        else:
            prompt = (f"Done: I know how you say my name now. Before, I caught {self.caught} of {of}"
                      if self.added else "Done: I already hear you fine")
        return {"step": self.step, "count": n, "of": of, "prompt": prompt, "learned": self.learned}


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


def _reap(pid: int, wait: float = 1.0) -> None:
    """Wait up to `wait` seconds for a child to exit, then kill it."""
    end = time.monotonic() + wait
    while time.monotonic() < end:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
        except ChildProcessError:
            return
        time.sleep(0.05)
    with contextlib.suppress(ProcessLookupError):
        os.kill(pid, signal.SIGKILL)
    with contextlib.suppress(ChildProcessError):
        os.waitpid(pid, 0)


class HandsFree:
    """The open mic and the spotter. Every chunk goes to `on_audio` (the daemon passes it on to a listen in
    progress); on the event loop, the spotter's hits go to `on_wake(word, at, clear)` (`at`: the chunk the
    name starts in, for `preroll`; `clear`: see find_name), each phrase it heard to `on_said(text, at)`, and
    `on_change` is called when it pauses or resumes (another app took the mic, or gave it back). Nothing
    is called once it's stopped."""

    def __init__(self, on_wake: Callable[[str, int, bool], None], on_audio: Callable[[bytes], None] | None = None,
                 on_change: Callable[[], None] | None = None, sensitivity: str = "medium",
                 pause_when_busy: bool = True, spotter: Spotter | None = None, source: list[str] | None = None,
                 on_said: Callable[[str, int], None] | None = None, mine: Iterable[str] = ()):
        self.on_wake, self.on_audio, self.on_change, self.on_said = on_wake, on_audio, on_change, on_said
        self.sensitivity, self.pause_when_busy, self.mine = sensitivity, pause_when_busy, tuple(mine)
        self.spotter = spotter
        self.source = source or []                 # extra pw-record args, for tests
        self.paused = False
        self.mic: asyncio.subprocess.Process | None = None
        self._ring: collections.deque[tuple[int, bytes]] = collections.deque(maxlen=math.ceil(RING / STEP))
        self._count = 0                            # chunks read so far: the ring's and the spotter's clock
        self._gap = False                          # a chunk didn't reach the spotter: it starts over
        self._work: queue.Queue[tuple[int, bytes, bool] | None] = queue.Queue(maxsize=50)
        self._tasks: list[asyncio.Task] = []
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    @property
    def status(self) -> str:
        return "paused" if self.paused else "on"

    @property
    def position(self) -> int:
        """The chunk just read (on_said's `at` counts in these)."""
        return self._count

    @property
    def words_after_name(self) -> int:
        """Words the local model heard after the last name, before a pause (0: the name on its own)."""
        return self.spotter.after_name if self.spotter else 0

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        if self.spotter is None:
            self.spotter = await asyncio.to_thread(Spotter, self.sensitivity, self.mine)
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
                self._gap = True
                await asyncio.sleep(1)
                if self._running:
                    await self._open_mic()
                continue
            self._count += 1
            self._ring.append((self._count, chunk))
            if self.on_audio:
                self.on_audio(chunk)
            if self.paused:
                self._gap = True
                continue
            try:
                self._work.put_nowait((self._count, chunk, self._gap))
                self._gap = False
            except queue.Full:                             # the spotter fell behind: skip, don't pile up
                self._gap = True

    def _spot(self) -> None:
        while (item := self._work.get()) is not None:
            n, chunk, gap = item
            try:
                if gap:
                    self.spotter.restart()
                events = self.spotter.feed(samples(chunk))
            except Exception as e:  # noqa: BLE001 - one bad chunk mustn't end hands-free
                log.warning("wake word: %s", e)
                continue
            if not (self._running and self._loop):
                continue
            for kind, text, ago, *clear in events:
                if kind == "name":
                    self._loop.call_soon_threadsafe(self._call, self.on_wake, text, n - int((ago + LEAD) // STEP),
                                                    *clear)
                elif self.on_said:
                    self._loop.call_soon_threadsafe(self._call, self.on_said, text, n - int(ago // STEP))

    def _call(self, fn: Callable, *args) -> None:
        """On the loop: unless it was stopped since (a new mic, or hands-free off), when the event is stale."""
        if self._running:
            fn(*args)

    async def _watch_mic(self) -> None:
        while self._running:
            busy = await asyncio.to_thread(mic_busy)
            if busy != self.paused:
                self.paused = busy
                log.info("hands-free %s", "paused: another app is using the microphone" if busy else "resumed")
                if self.on_change:
                    self.on_change()
            await asyncio.sleep(BUSY_EVERY)

    def preroll(self, at: int | None = None) -> list[bytes]:
        """The audio from chunk `at` on (where the name started, from on_wake), up to RING seconds."""
        return [chunk for n, chunk in self._ring if at is None or n >= at]

    def learn(self, mine: Iterable[str]) -> None:
        """How the local model hears this user's name (from Learn my voice), from now on."""
        self.mine = tuple(mine)
        if self.spotter:
            self.spotter.mine = self.mine

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
            if self._loop and self._loop.is_running():    # it should be gone in a moment; if not, kill it
                self._loop.call_later(2, lambda: mic.returncode is None and mic.kill())
            else:                                          # the daemon is exiting: wait here, briefly
                _reap(mic.pid)
        try:
            self._work.put_nowait(None)
        except queue.Full:
            with self._work.mutex:
                self._work.queue.clear()
            self._work.put_nowait(None)


def test(seconds: float = 60, sensitivity: str = "medium", mine: Iterable[str] = ()) -> int:
    """`auren wake-test`: say the name a few times and see what the local model hears (it runs alongside
    the daemon; nothing is sent anywhere)."""
    async def run():
        start = time.monotonic()

        def heard(word: str, at: int, clear: bool) -> None:
            print(f"  {time.monotonic() - start:5.1f}s  the name ({word.lower()})", flush=True)

        def said(text: str, at: int) -> None:
            print(f"  {time.monotonic() - start:5.1f}s  heard: {text.lower()}", flush=True)

        hf = HandsFree(heard, sensitivity=sensitivity, pause_when_busy=False, on_said=said, mine=mine)
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
