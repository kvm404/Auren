"""The voice: Supertonic 3, voice F2, 5 steps, local (decision 0001).

Speech is generated sentence by sentence and piped into pw-cat, so the first
sentence plays while the rest is still being generated. `stop()` cuts it off
(the user pressed the talk key again).

Adjustable (the bar panel's Voice page, `auren-ctl tts ...`): speed, steps
(quality: more steps cost more CPU), volume (scales the samples, relative to the
system volume) and a pause between sentences.

All speech is synthesized in English (decision 0001).

For the mascot, a small clock thread follows the playback: it reports which
sentence is being heard (captions) and how loud it is (the mascot bounces), using
each sentence's loudness envelope and when its audio starts playing.

The model (~380 MB) lives in ~/.local/share/auren/models/supertonic-3 and is fetched by
`auren voice-model` (install.sh runs it). Supertonic's repository is archived: the download
uses the commit the SDK pins, and the archive's copy if that is gone. An existing download
in the SDK's own cache (~/.cache/supertonic3) is used as is.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import MODELS_DIR
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

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(text.strip()) if s.strip()]


MODEL = "supertonic-3"
ARCHIVE_REPO, ARCHIVE_REVISION = "supertone-oss-archive/supertonic-3", "aafc6e32416a594460b32413efc49d7fe4ce6d46"
MODEL_FILES = ["config.json", "LICENSE", "onnx/*", "voice_styles/*"]


class VoiceModelMissing(RuntimeError):
    pass


def model_dir() -> Path | None:
    """Where a complete model is, or None."""
    from supertonic.config import get_model_cache_dir
    from supertonic.loader import has_all_onnx_modules

    for d in (MODELS_DIR / MODEL, get_model_cache_dir(MODEL)):
        if d.is_dir() and has_all_onnx_modules(d) and any((d / "voice_styles").glob("*.json")):
            return d
    return None


def download_model(log: Callable[[str], None] = print) -> Path:
    """Fetch the model into MODELS_DIR (if it isn't anywhere yet) and return its folder."""
    if found := model_dir():
        return found
    from huggingface_hub import snapshot_download
    from supertonic.config import get_model_repo, get_model_revision

    target = MODELS_DIR / MODEL
    tmp = target.with_name(f".{MODEL}.tmp")
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    sources = [(get_model_repo(MODEL), get_model_revision(MODEL)), (ARCHIVE_REPO, ARCHIVE_REVISION)]
    errors = []
    for repo, revision in sources:
        log(f"downloading the voice model from {repo} ({revision[:12]}), about 380 MB")
        try:
            shutil.rmtree(tmp, ignore_errors=True)
            snapshot_download(repo_id=repo, revision=revision, local_dir=str(tmp), allow_patterns=MODEL_FILES)
        except Exception as e:  # noqa: BLE001 - try the next source
            errors.append(f"{repo}: {e}")
            continue
        shutil.rmtree(target, ignore_errors=True)
        tmp.rename(target)
        if model_dir():
            return target
        errors.append(f"{repo}: incomplete download")
    shutil.rmtree(tmp, ignore_errors=True)
    raise VoiceModelMissing("couldn't download the voice model: " + "; ".join(errors))


class Voice:
    def __init__(self, voice: str = "F2", steps: int = 5, speed: float = 1.05, volume: float = 1.0, pause: float = 0.0,
                 on_level: Callable[[float], None] | None = None, on_sentence: Callable[[str], None] | None = None):
        from supertonic import TTS

        folder = model_dir()
        if folder is None:
            raise VoiceModelMissing("the voice model isn't downloaded: run `auren voice-model`")
        self.tts = TTS(model=MODEL, model_dir=folder, auto_download=False)
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
                                                 speed=self.speed, lang="en")
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
