"""Which way of writing Hinglish does Supertonic (voice F2) speak best?

  uv run hinglish.py            # play every variant, each announced first
  uv run hinglish.py C D        # only these variants

Variants of the same sentence:
  A  romanized, English mode       "Theek hai, main abhi Firefox khol raha hoon."
  B  all Devanagari, Hindi mode    English words transliterated too ("फ़ायरफ़ॉक्स")
  C  mixed script, Hindi mode      Hindi in Devanagari, English words left in Latin
  D  mixed script, split           Devanagari runs spoken in Hindi mode and Latin
                                   runs in English mode, then joined (same voice)

In the real assistant the brain would write B or C directly; that costs nothing extra.
"""

from __future__ import annotations

import re
import sys
import time
from typing import Iterator

import numpy as np

from engines import SupertonicTTS
from say import speak

SENTENCES = [
    {
        "A": "Theek hai, main abhi Firefox khol raha hoon. Bas do second.",
        "B": "ठीक है, मैं अभी फ़ायरफ़ॉक्स खोल रहा हूँ। बस दो सेकंड।",
        "C": "ठीक है, मैं अभी Firefox खोल रहा हूँ। बस दो second।",
    },
    {
        "A": "Build pass ho gaya, lekin do tests fail ho rahe hain. Kya main Codex se fix karwaun?",
        "B": "बिल्ड पास हो गया, लेकिन दो टेस्ट्स फ़ेल हो रहे हैं। क्या मैं कोडेक्स से फ़िक्स करवाऊँ?",
        "C": "Build pass हो गया, लेकिन दो tests fail हो रहे हैं। क्या मैं Codex से fix करवाऊँ?",
    },
]
LABELS = {
    "A": "Option A. Romanized.",
    "B": "Option B. All Devanagari.",
    "C": "Option C. Mixed script.",
    "D": "Option D. Mixed script, split by language.",
}

# A run of Devanagari (plus spaces/punctuation inside it) or a run of anything else.
_RUNS = re.compile(r"[ऀ-ॿ][ऀ-ॿ\s,।?!.]*|[^ऀ-ॿ]+")


class SplitByScript:
    """Wraps Supertonic: Devanagari runs in Hindi mode, Latin runs in English mode."""

    def __init__(self, engine: SupertonicTTS):
        self.engine = engine
        self.sample_rate = engine.sample_rate

    def stream(self, text: str, voice: str, lang: str = "hi") -> Iterator[np.ndarray]:
        for run in _RUNS.findall(text):
            if not re.search(r"\w", run):
                continue
            run_lang = "hi" if re.search(r"[ऀ-ॿ]", run) else "en"
            for chunk in self.engine.stream(run.strip(), voice, run_lang):
                yield chunk


def main() -> None:
    picked = [v.upper() for v in sys.argv[1:]] or list(LABELS)
    engine = SupertonicTTS(steps=5)  # the locked settings: F2, 5 steps, default speed
    split = SplitByScript(engine)
    for variant in picked:
        print(f"▶ {LABELS[variant]}", file=sys.stderr, flush=True)
        speak(engine, LABELS[variant], "F2", "en")
        for s in SENTENCES:
            if variant == "A":
                speak(engine, s["A"], "F2", "en")
            elif variant == "B":
                speak(engine, s["B"], "F2", "hi")
            elif variant == "C":
                speak(engine, s["C"], "F2", "hi")
            else:
                speak(split, s["C"], "F2", "hi")
            time.sleep(0.4)
        time.sleep(0.8)


if __name__ == "__main__":
    main()
