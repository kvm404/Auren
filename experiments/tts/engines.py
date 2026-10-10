"""Supertonic 3 adapter used by the experiment scripts.

Pocket TTS and Kokoro were compared too and removed after Supertonic won;
see docs/decisions/0001-assistant-voice.md and README.md for their numbers.

The engine exposes:
  sample_rate: int
  voices: the built-in voice names
  stream(text, voice, lang) -> yields float32 numpy chunks, one per sentence

Supertonic has no streaming inside a sentence, so text is fed sentence by
sentence. That is also how the real assistant would feed it.
"""

from __future__ import annotations

import re
from typing import Iterator

import numpy as np

# Split after . ! ? or the Hindi danda, keeping the punctuation.
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


def _mono_f32(audio) -> np.ndarray:
    return np.asarray(audio, dtype=np.float32).reshape(-1)


class SupertonicTTS:
    """Supertone Supertonic 3 (99M, ONNX, 31 languages). Sentence-level streaming."""

    languages = {"en", "hi"}
    voices = ["F1", "F2", "F3", "F4", "F5", "M1", "M2", "M3", "M4", "M5"]

    def __init__(self, steps: int = 5, speed: float | None = None):
        from supertonic import TTS

        self.tts = TTS(auto_download=True)
        self.steps = steps
        self.speed = speed  # 0.7 (slow) to 2.0 (fast); None = library default (1.05)
        self.sample_rate = int(getattr(self.tts, "sample_rate", 44100))
        self._styles: dict[str, object] = {}

    def _style(self, voice: str):
        """A built-in name (F1-F5, M1-M5) or a path to a Voice Builder JSON export."""
        if voice not in self._styles:
            if voice.endswith(".json"):
                self._styles[voice] = self.tts.get_voice_style_from_path(voice)
            else:
                self._styles[voice] = self.tts.get_voice_style(voice_name=voice)
        return self._styles[voice]

    def prepare(self, voice: str) -> None:
        self._style(voice)

    def stream(self, text: str, voice: str, lang: str = "en") -> Iterator[np.ndarray]:
        extra = {"speed": self.speed} if self.speed else {}
        for sentence in sentences(text):
            wav, _duration = self.tts.synthesize(
                text=sentence, voice_style=self._style(voice), total_steps=self.steps,
                lang=lang, **extra,
            )
            yield _mono_f32(wav)


# name -> (constructor, default voice). "supertonic-fast" is the locked assistant voice.
ENGINES = {
    "supertonic-fast": (lambda: SupertonicTTS(steps=5), "F2"),
    "supertonic": (lambda: SupertonicTTS(steps=8), "F2"),
}

# How each engine introduces itself before its sample, in its own voice.
SPOKEN_NAMES = {
    "supertonic-fast": "This is Supertonic, fast mode.",
    "supertonic": "This is Supertonic.",
}


def load(name: str):
    if name not in ENGINES:
        raise SystemExit(f"unknown engine {name!r}; choose from: {', '.join(ENGINES)}")
    make, default_voice = ENGINES[name]
    return make(), default_voice
