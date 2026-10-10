"""Benchmark one engine on assistant-style lines and save the audio.

  uv run bench.py supertonic-fast
  uv run bench.py supertonic

Writes out/<engine>/<sample>.wav, out/<engine>/_intro.wav (the engine saying
its name) and results/<engine>.json. Run each engine in its own process
(run_all.sh does) so memory numbers don't mix. `--intro-only` just writes the intro.
"""

from __future__ import annotations

import json
import resource
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import engines

HERE = Path(__file__).parent

# What the assistant would actually say: quick acks, status lines, a summary,
# awkward tokens (numbers, names, paths), and Hinglish.
SAMPLES = {
    "ack": ("en", "On it."),
    "short": ("en", "Sure, opening Firefox on workspace two."),
    "status": ("en", "I found three failing tests in the Voisu project. Two are timeouts in the "
                     "provider module, and one is a formatting assertion. Should I ask Codex to fix them?"),
    "tricky": ("en", "Battery is at 42 percent. The build took 3 minutes 15 seconds, and PR #234 "
                     "is ready on GitHub. Config lives in ~/.config/hypr/hyprland.conf."),
    "long": ("en", "Here is what happened while you were away. Claude finished the overlay "
                   "refactor and all tests pass. Codex reviewed it and flagged one race condition "
                   "in the audio player, which I have queued as a follow-up. Your download of the "
                   "Kokoro model completed, and Spotify is paused. Nothing else needs your attention."),
    "hinglish": ("en", "Theek hai, main abhi Firefox khol raha hoon. Bas do second."),
    "hindi": ("hi", "ठीक है, मैं अभी फ़ायरफ़ॉक्स खोल रहा हूँ। बस दो सेकंड।"),
}


def peak_rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def write_intro(engine, name: str, voice: str, out: Path) -> None:
    """Save the engine saying its own name, played before its samples."""
    audio = np.concatenate(list(engine.stream(engines.SPOKEN_NAMES[name], voice)))
    sf.write(out / "_intro.wav", audio, engine.sample_rate)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    name = args[0] if args else "supertonic-fast"
    out = HERE / "out" / name
    out.mkdir(parents=True, exist_ok=True)
    (HERE / "results").mkdir(exist_ok=True)

    t = time.perf_counter()
    engine, voice = engines.load(name)
    engine.prepare(voice)
    load_s = time.perf_counter() - t

    if "--intro-only" in sys.argv:
        write_intro(engine, name, voice, out)
        print(f"{name}: wrote intro")
        return

    # The first call pays one-off setup costs; time it separately.
    t = time.perf_counter()
    for _ in engine.stream("Hello.", voice):
        pass
    warmup_s = time.perf_counter() - t

    rows = []
    for sid, (lang, text) in SAMPLES.items():
        if lang not in engine.languages:
            continue
        start = time.perf_counter()
        first = None
        chunks = []
        for chunk in engine.stream(text, voice, lang):
            first = first if first is not None else time.perf_counter() - start
            chunks.append(chunk)
        total = time.perf_counter() - start
        audio = np.concatenate(chunks)
        dur = len(audio) / engine.sample_rate
        sf.write(out / f"{sid}.wav", audio, engine.sample_rate)
        rows.append({"sample": sid, "first_audio_s": round(first, 3), "synth_s": round(total, 3),
                     "audio_s": round(dur, 2), "x_realtime": round(dur / total, 2)})
        print(f"{name:16} {sid:9} first {first:5.2f}s  synth {total:5.2f}s  "
              f"audio {dur:5.1f}s  {dur / total:4.1f}x", flush=True)

    write_intro(engine, name, voice, out)
    result = {"engine": name, "voice": voice, "load_s": round(load_s, 2),
              "warmup_s": round(warmup_s, 2), "peak_rss_mb": round(peak_rss_mb()), "samples": rows}
    (HERE / "results" / f"{name}.json").write_text(json.dumps(result, indent=2))
    print(f"{name}: load {load_s:.1f}s, warmup {warmup_s:.1f}s, peak RAM {peak_rss_mb():.0f} MB")


if __name__ == "__main__":
    main()
