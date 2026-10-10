"""The voice: Supertonic 3, voice F2, 5 steps, local (decision 0001).

Speech is generated sentence by sentence and piped into pw-cat, so the first
sentence plays while the rest is still being generated. `stop()` cuts it off
(the user pressed the talk key again).

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

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


class Voice:
    def __init__(self, voice: str = "F2", steps: int = 5, speed: float | None = None,
                 on_level: Callable[[float], None] | None = None, on_sentence: Callable[[str], None] | None = None):
        from supertonic import TTS          # loads the ONNX models (~380 MB, cached in ~/.cache/supertonic3)

        self.tts = TTS(auto_download=True)
        self.name = voice
        self.style = self.tts.get_voice_style(voice_name=voice)
        self.steps, self.speed = steps, speed
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
                extra = {"speed": self.speed} if self.speed else {}
                for sentence in sentences(text):
                    if self._stop.is_set():
                        break
                    wav, _ = self.tts.synthesize(text=sentence, voice_style=self.style, total_steps=self.steps,
                                                 lang="en", **extra)
                    if self._stop.is_set():
                        break
                    audio = np.asarray(wav, dtype=np.float32).reshape(-1)
                    start = max(time.monotonic() + LATENCY, self._timeline[-1][1] if self._timeline else 0)
                    self._timeline.append((start, start + len(audio) / self.rate, envelope(audio, self.rate), sentence))
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
