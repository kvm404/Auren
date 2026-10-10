"""Audition Supertonic's voices live, each one announcing itself first.

  uv run voices.py                       # all 10 voices, English
  uv run voices.py --lang hi             # all 10 voices, Hindi
  uv run voices.py F2 M3                 # only these voices
  uv run voices.py --text "Build finished. Two tests failed."
  uv run voices.py --steps 8 --speed 1.1 # higher quality, slightly faster speech
  uv run voices.py ~/Downloads/my_voice.json   # a Voice Builder export
"""

from __future__ import annotations

import argparse
import sys
import time

from engines import SupertonicTTS
from say import speak

LINES = {
    "en": "Hi, I'm your assistant. I found three failing tests in the Voisu project. "
          "Should I ask Codex to fix them?",
    "hi": "नमस्ते, मैं आपका असिस्टेंट हूँ। वॉइसू प्रोजेक्ट में तीन टेस्ट फ़ेल हो रहे हैं। "
          "क्या मैं कोडेक्स से इन्हें ठीक करवाऊँ?",
}
INTRO = {"en": "Voice {}.", "hi": "आवाज़ {}।"}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("voices", nargs="*", help="voice names or Voice Builder .json files (default: all 10)")
    p.add_argument("--lang", default="en", choices=sorted(LINES))
    p.add_argument("--text", help="what each voice says (default: an assistant-style line)")
    p.add_argument("--steps", type=int, default=5, help="quality 5 (fast, default) to 12 (best)")
    p.add_argument("--speed", type=float, help="0.7 (slow) to 2.0 (fast)")
    args = p.parse_args()

    engine = SupertonicTTS(steps=args.steps, speed=args.speed)
    voices = args.voices or engine.voices
    text = args.text or LINES[args.lang]
    for voice in voices:
        engine.prepare(voice)
        label = voice.rsplit("/", 1)[-1].removesuffix(".json")
        print(f"▶ {label}", file=sys.stderr, flush=True)
        # Spell the name out so "F1" is read as "F one", not a word.
        speak(engine, INTRO[args.lang].format(" ".join(label)), voice, args.lang)
        speak(engine, text, voice, args.lang)
        time.sleep(0.8)


if __name__ == "__main__":
    main()
