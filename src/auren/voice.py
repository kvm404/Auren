"""The voice: Supertonic 3, voice F2, 5 steps, local (decision 0001).

Speech is generated sentence by sentence and piped into pw-cat, so the first
sentence plays while the rest is still being generated. `stop()` cuts it off
(the user pressed the talk key again).

Adjustable (the bar panel's Voice page, `auren-ctl tts ...`): speed, steps
(quality: more steps cost more CPU), volume (scales the samples, relative to the
system volume) and a pause between sentences.

Hindi (decision 0001): a sentence with Devanagari in it is spoken in Hindi mode,
English words in Latin letters included; anything else in English mode. So the
brain's Hindi replies and the daemon's own English phrases both sound right.

For the mascot, a small clock thread follows the playback: it reports which
sentence is being heard (captions) and how loud it is (the mascot bounces), using
each sentence's loudness envelope and when its audio starts playing.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable

import numpy as np

from .live import envelope

LATENCY = 0.12        # pw-cat's start-up and buffering, roughly

# name: (default, lowest, highest). The defaults are decision 0001's (speed 1.05 is Supertonic's own).
TUNING = {"speed": (1.05, 0.7, 1.6), "steps": (5, 2, 12), "volume": (1.0, 0.1, 1.0), "pause": (0.0, 0.0, 1.0)}


def tuning(values: dict | None = None) -> dict:
    """Speed, steps, volume and pause from `values`, each clamped to its range (defaults for the rest)."""
    out = {}
    for name, (default, low, high) in TUNING.items():
        try:
            v = float((values or {}).get(name, default))
        except (TypeError, ValueError):
            v = default
        v = min(high, max(low, v))
        out[name] = int(round(v)) if name == "steps" else round(v, 2)
    return out

_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")       # । is the Hindi full stop
_DEVANAGARI = re.compile(r"[\u0900-\u097F]")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def language_of(sentence: str) -> str:
    """Supertonic's language for a sentence: Hindi if it has Devanagari in it, otherwise English."""
    return "hi" if _DEVANAGARI.search(sentence) else "en"


class Voice:
    def __init__(self, voice: str = "F2", steps: int = 5, speed: float = 1.05, volume: float = 1.0, pause: float = 0.0,
                 on_level: Callable[[float], None] | None = None, on_sentence: Callable[[str], None] | None = None):
        from supertonic import TTS  # loads the ONNX models (~380 MB, cached in ~/.cache/supertonic3)

        self.tts = TTS(auto_download=True)
        self.name = voice
        self.style = self.tts.get_voice_style(voice_name=voice)
        self.steps, self.speed, self.volume, self.pause = steps, speed, volume, pause
        self.rate = int(getattr(self.tts, "sample_rate", 44100))
        self._player: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.on_level, self.on_sentence = on_level, on_sentence
        self._timeline: list[tuple[float, float, list[float], str]] = []   # (start, end, envelope, sentence)

    def speak(self, text: str) -> bool:
        """Blocking. Returns False if it was interrupted."""
        with self._lock:
            self._stop.clear()
            self._player = subprocess.Popen(
                ["pw-cat", "--playback", "--raw", "--format", "f32", "--channels", "1", "--rate", str(self.rate),
                 "--media-role", "Assistant", "-"], stdin=subprocess.PIPE, stderr=subprocess.DEVNULL)
            self._timeline = []
            clock = threading.Thread(target=self._follow, daemon=True)
            clock.start()
            try:
                gap = np.zeros(int(self.pause * self.rate), dtype=np.float32)
                for i, sentence in enumerate(sentences(text)):
                    if self._stop.is_set():
                        break
                    wav, _ = self.tts.synthesize(text=sentence, voice_style=self.style, total_steps=self.steps,
                                                 speed=self.speed, lang=language_of(sentence))
                    if self._stop.is_set():
                        break
                    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
                    env = envelope(audio, self.rate)            # before the volume, so the mascot bounces the same
                    if self.volume < 1:
                        audio = audio * np.float32(self.volume)
                    if i and gap.size:                          # the pause goes before every sentence but the first
                        audio = np.concatenate([gap, audio])
                        env = [0.0] * int(self.pause * 20) + env
                    start = max(time.monotonic() + LATENCY, self._timeline[-1][1] if self._timeline else 0)
                    self._timeline.append((start, start + len(audio) / self.rate, env, sentence))
                    self._player.stdin.write(audio.tobytes())
                    self._player.stdin.flush()
                self._player.stdin.close()
                self._player.wait()
            except (BrokenPipeError, ValueError):
                pass
            finally:
                self._player = None
                self._timeline = []
                clock.join(1)
            return not self._stop.is_set()

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
        return sorted(self.tts.voice_style_names)

    def tune(self, values: dict) -> None:
        """Apply speed, steps, volume and pause (clamped); used from the next sentence on."""
        t = tuning(values)
        self.speed, self.steps, self.volume, self.pause = t["speed"], t["steps"], t["volume"], t["pause"]

    def set_voice(self, name: str) -> None:
        if name not in self.tts.voice_style_names:
            raise ValueError(f"no voice {name}; voices: {', '.join(self.names)}")
        self.style = self.tts.get_voice_style(voice_name=name)
        self.name = name

    def stop(self) -> None:
        self._stop.set()
        p = self._player
        if p and p.poll() is None:
            p.kill()
