"""The voice: Gradium's Phonon 31m through ptts, local on the CPU (decision 0001).

Four voices from Phonon's catalog, listed in VOICES (the first is the default). The phone speaks with the
same model and reads the PC's choice from the state.

Speech is generated sentence by sentence, and each sentence is streamed into pw-cat in chunks as the model
decodes them, so the first words play about 0.15 s after synthesis starts. `stop()` cuts it off (the user
pressed the talk key again).

Adjustable (the bar panel's Voice page, `auren-ctl tts ...`): speed (Phonon's duration_delta), volume
(scales the samples, relative to the system volume) and a pause between sentences.

All speech is synthesized in English (decision 0001).

For the mascot, a small clock thread follows the playback: it reports which sentence is being heard
(captions) and how loud it is (the mascot bounces), using each chunk's loudness envelope and when its
audio starts playing.

The model (~100 MB) and the four voices (~256 KB each) are fetched by `auren voice-model` (install.sh runs
it) into the Hugging Face cache (~/.cache/huggingface/hub), at the revisions ptts pins. The daemon loads
them from that cache without going online.
"""

from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np

from . import config
from .config import MAC
from .live import envelope

LATENCY = 0.12        # pw-cat's start-up and buffering, roughly

# The voices Auren offers, by Phonon catalog name, the default first. To swap one, change it here and in
# the bar plugin's voiceChoices (omarchy-plugin/Panel.qml), then run `auren voice-model` to fetch it.
VOICES = {"Everly": "US female", "Juniper": "US female", "Hugh": "UK male", "Trevor": "US male"}
DEFAULT_VOICE = next(iter(VOICES))

# name: (default, lowest, highest). Phonon's own pace is speed 1.0; past these ends duration_delta barely
# changes the pace (measured 2026-11-19: about 0.85x to 1.25x).
TUNING = {"speed": (1.0, 0.8, 1.25), "volume": (1.0, 0.1, 1.0), "pause": (0.0, 0.0, 1.0)}


def tuning(values: dict | None = None) -> dict:
    """Speed, volume and pause from `values`, each clamped to its range (defaults for the rest)."""
    out = {}
    for name, (default, low, high) in TUNING.items():
        try:
            v = float((values or {}).get(name, default))
        except (TypeError, ValueError):
            v = default
        out[name] = round(min(high, max(low, v)), 2)
    return out


def duration_delta(speed: float) -> float:
    """Phonon's duration_delta for a speed: negative speaks faster. 1.25 -> -0.5, 1.0 -> 0, 0.8 -> 0.4."""
    return round(2 * (1 - speed), 2)


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


class VoiceModelMissing(RuntimeError):
    pass


REPOS = ("gradium/phonon-31m-en", "gradium/phonon-voices")     # what download_model puts in the cache


def hf_cache() -> Path:
    """The Hugging Face cache ptts downloads into: HF_HUB_CACHE, else HF_HOME/hub, else
    $XDG_CACHE_HOME/huggingface/hub."""
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"]).expanduser()
    if os.environ.get("HF_HOME"):
        return Path(os.environ["HF_HOME"]).expanduser() / "hub"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache").expanduser() / "huggingface/hub"


def cache_dirs() -> list[Path]:
    """The voice model's folders in the cache (auren setup --remove --purge deletes them)."""
    return [hf_cache() / f"models--{repo.replace('/', '--')}" for repo in REPOS]


@contextlib.contextmanager
def _offline() -> Iterator[None]:
    """Hugging Face files from the cache only (ptts reads HF_HUB_OFFLINE on each load)."""
    old = os.environ.get("HF_HUB_OFFLINE")
    os.environ["HF_HUB_OFFLINE"] = "1"
    try:
        yield
    finally:
        if old is None:
            os.environ.pop("HF_HUB_OFFLINE", None)
        else:
            os.environ["HF_HUB_OFFLINE"] = old


def _register(tts, name: str) -> bool:
    """Make a catalog voice known to `tts` (downloading it, unless offline). False: it isn't available."""
    try:
        with tts.stream(".", voice=name):
            pass
    except LookupError:
        return False
    return True


def _load(offline: bool):
    import ptts

    with _offline() if offline else contextlib.nullcontext():
        return ptts.TTS(lang="en")


def missing() -> list[str]:
    """What isn't downloaded yet, checked in the cache only: "model" and/or voice names."""
    try:
        tts = _load(offline=True)
    except Exception:  # noqa: BLE001 - ptts raises LookupError or OSError for a missing file
        return ["model", *VOICES]
    with _offline():
        return [v for v in VOICES if not _register(tts, v)]


def download_model(log: Callable[[str], None] = print) -> str:
    """Fetch the model and the four voices into the Hugging Face cache; says what is there."""
    import ptts

    log(f"downloading the voice model ({ptts.DEFAULT_MODEL.split('@')[0]}, about 100 MB) and its voices")
    try:
        tts = _load(offline=False)
        lost = [v for v in VOICES if not _register(tts, v)]
    except Exception as e:  # noqa: BLE001 - offline, the Hub down, a full disk
        raise VoiceModelMissing(f"couldn't download the voice model: {e}") from e
    if lost:
        raise VoiceModelMissing(f"couldn't download the voices {', '.join(lost)}")
    retire_old_model(log)
    return f"Phonon 31m with {', '.join(VOICES)}"


def old_model() -> Path:
    """Where Auren before 0.1.7 kept its Supertonic model (about 380 MB)."""
    return config.DATA_HOME / "models" / "supertonic-3"


def retire_old_model(log: Callable[[str], None] = print) -> bool:
    """Delete the Supertonic model Auren downloaded before Phonon, once Phonon is here (only Auren's own
    folder: the SDK's cache, ~/.cache/supertonic3, may be another program's). True if it was deleted."""
    old = old_model()
    if not (old.is_dir() or old.is_symlink()):
        return False
    try:
        if old.is_symlink():
            old.unlink()
        else:
            shutil.rmtree(old)
        with contextlib.suppress(OSError):
            old.parent.rmdir()                  # models/, if nothing else is in it
    except OSError as e:
        log(f"couldn't delete the old voice model {old}: {e}")
        return False
    log(f"deleted the old voice model (Supertonic, {old})")
    return True


class Voice:
    def __init__(self, voice: str = DEFAULT_VOICE, speed: float = 1.0, volume: float = 1.0, pause: float = 0.0,
                 on_level: Callable[[float], None] | None = None, on_sentence: Callable[[str], None] | None = None):
        try:
            self.tts = _load(offline=True)
        except Exception as e:  # noqa: BLE001
            raise VoiceModelMissing(f"the voice model isn't downloaded ({e}): run `auren voice-model`") from e
        with _offline():                    # the voices on disk now; a missing one downloads when first used
            for name in VOICES:
                _register(self.tts, name)
        self.name = DEFAULT_VOICE
        self.set_voice(voice)
        self.speed, self.volume, self.pause = speed, volume, pause
        self._delta: float | None = None    # the duration_delta the model has now
        self.rate = int(self.tts.sample_rate)
        self._player: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.on_level, self.on_sentence = on_level, on_sentence
        self._timeline: list[tuple[float, float, list[float], str]] = []   # (start, end, envelope, sentence)

    def speak(self, text: str) -> bool:
        """Blocking. Returns False if it was interrupted."""
        with self._lock:
            self._stop.clear()
            if MAC:
                from .mac.audio import Player
                self._player = Player(self.rate)
            else:
                self._player = subprocess.Popen(
                    ["pw-cat", "--playback", "--raw", "--format", "f32", "--channels", "1", "--rate", str(self.rate),
                     "--media-role", "Assistant", "-"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._timeline = []
            clock = threading.Thread(target=self._follow, daemon=True)
            clock.start()
            try:
                for i, sentence in enumerate(sentences(text)):
                    if self._stop.is_set():
                        break
                    self._sentence(sentence, gap=self.pause if i else 0.0)
                self._player.stdin.close()
                self._player.wait()
            except (BrokenPipeError, ValueError):
                pass
            finally:
                self._player = None
                self._timeline = []
                clock.join(1)
            return not self._stop.is_set()

    def _sentence(self, sentence: str, gap: float) -> None:
        """Stream one sentence into the player as it is decoded; the pause, if any, goes before it."""
        delta = duration_delta(self.speed)
        if delta != self._delta:
            self.tts.set_conditions({"duration_delta": delta})
            self._delta = delta
        with self.tts.stream(sentence, voice=self.name) as chunks:
            for chunk in chunks:
                if self._stop.is_set():
                    return
                audio = np.asarray(chunk, dtype=np.float32).reshape(-1)
                env = envelope(audio, self.rate)            # before the volume, so the mascot bounces the same
                if self.volume < 1:
                    audio = audio * np.float32(self.volume)
                if gap:
                    audio = np.concatenate([np.zeros(int(gap * self.rate), dtype=np.float32), audio])
                    env = [0.0] * int(gap * 20) + env
                    gap = 0.0
                start = max(time.monotonic() + LATENCY, self._timeline[-1][1] if self._timeline else 0)
                self._timeline.append((start, start + len(audio) / self.rate, env, sentence))
                self._player.stdin.write(audio.tobytes())
                self._player.stdin.flush()

    def _follow(self) -> None:
        """While speaking: report the sentence being heard and its loudness, 20 times a second."""
        heard = None
        while self._player is not None or self._timeline:
            now = time.monotonic()
            level, current = 0.0, None
            for start, end, env, sentence in list(self._timeline):
                if start <= now < end:
                    level = env[min(int((now - start) * 20), len(env) - 1)] if env else 0.0
                    current = sentence
                    break
            if current is not None and current != heard and self.on_sentence:
                heard = current
                self.on_sentence(current)
            if self.on_level:
                self.on_level(level)
            time.sleep(0.05)
        if self.on_level:
            self.on_level(0.0)

    @property
    def names(self) -> list[str]:
        return list(VOICES)

    def tune(self, values: dict) -> None:
        """Apply speed, volume and pause (clamped); used from the next sentence on."""
        t = tuning(values)
        self.speed, self.volume, self.pause = t["speed"], t["volume"], t["pause"]

    def set_voice(self, name: str) -> None:
        if name not in VOICES:
            raise ValueError(f"no voice {name}; voices: {', '.join(VOICES)}")
        self.name = name

    def stop(self) -> None:
        self._stop.set()
        p = self._player
        if p and p.poll() is None:
            p.kill()
