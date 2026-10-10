"""Speak text live through your speakers and report how fast it started.

  uv run say.py supertonic-fast "On it. Opening Firefox on workspace two."
  uv run say.py supertonic-fast --voice M3 "Hello there"
  uv run say.py supertonic --lang hi "ठीक है, मैं अभी फ़ायरफ़ॉक्स खोल रहा हूँ।"
  uv run say.py supertonic-fast -i            # type lines interactively, Ctrl-D to quit

Audio is piped chunk by chunk into pw-cat, so you hear the first sentence
while the rest is still being generated, like the real assistant would.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

import numpy as np

import engines


def speak(engine, text: str, voice: str, lang: str) -> None:
    player = subprocess.Popen(
        ["pw-cat", "--playback", "--raw", "--format", "f32", "--channels", "1",
         "--rate", str(engine.sample_rate), "-"],
        stdin=subprocess.PIPE,
    )
    start = time.perf_counter()
    first = None
    samples = 0
    for chunk in engine.stream(text, voice, lang):
        if first is None:
            first = time.perf_counter() - start
        samples += len(chunk)
        player.stdin.write(np.clip(chunk, -1, 1).astype(np.float32).tobytes())
        player.stdin.flush()
    generated = time.perf_counter() - start
    player.stdin.close()
    player.wait()
    audio = samples / engine.sample_rate
    print(f"  first audio {first:.2f}s · generated {audio:.1f}s of speech in {generated:.2f}s "
          f"({audio / generated:.1f}x real-time)", file=sys.stderr)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("engine", choices=engines.ENGINES)
    p.add_argument("text", nargs="?")
    p.add_argument("--voice")
    p.add_argument("--lang", default="en", help="en or hi")
    p.add_argument("-i", "--interactive", action="store_true")
    args = p.parse_args()

    t = time.perf_counter()
    engine, default_voice = engines.load(args.engine)
    voice = args.voice or default_voice
    engine.prepare(voice)
    print(f"{args.engine} loaded in {time.perf_counter() - t:.1f}s · voice {voice} · "
          f"try voices: {', '.join(engine.voices)}", file=sys.stderr)

    if args.interactive or not args.text:
        for line in sys.stdin:
            if line.strip():
                speak(engine, line.strip(), voice, args.lang)
    else:
        speak(engine, args.text, voice, args.lang)


if __name__ == "__main__":
    main()
