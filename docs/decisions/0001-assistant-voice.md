# 0001: Assistant voice is local Supertonic 3, voice F2

Date: 2026-10-09 · Status: accepted

## Decision

The assistant speaks with **Supertonic 3** running locally (ONNX, CPU), configured as:

| Setting | Value | Why |
|---|---|---|
| Voice | **F2** | Chosen by ear from all 10 built-in voices |
| Alternatives | M3 (normal pace), F1 / M1 (slower, calmer) | Same cost; switch per preference |
| Steps | **5** | Cheapest setting; quality was good enough by ear. 8 costs ~45% more CPU |
| Speed | **1.05** | Supertonic's default; what F2 sounded like when it was chosen |
| Language | **English (`en`)** | What the user speaks most. Hindi is supported but out of scope for now |

## Why Supertonic

Measured on this laptop (i3-1115G4, 4 threads, 7.5 GB RAM) against Pocket TTS and Kokoro; details and
scripts in `experiments/tts/`.

- Lowest CPU per second of speech (0.37 s), tied with quantized Pocket TTS.
- About half Pocket's RAM (~0.5 GB vs ~1 GB).
- First audio in ~0.5 s for a short sentence; generates 6x faster than real time.
- The only tested engine that speaks Hindi properly.
- Custom voices are possible later via Supertonic Voice Builder (JSON export).

## Hindi and Hinglish

Picked up on 2026-10-10 as a reply-language setting (`auren-ctl language hi`, the panel's Voice page): the
brain writes option C below (Hindi in Devanagari, English names in Latin) and each sentence with
Devanagari is synthesized with `lang="hi"`. What the original test found:

- Hindi must be written in **Devanagari** and synthesized with `lang="hi"`.
- Hinglish in Roman letters ("main abhi Firefox khol raha hoon") is spoken badly by every engine tested.
- Hinglish works when the brain writes it in Devanagari: either all Devanagari (English words
  transliterated) or Hindi words in Devanagari with English words left in Latin. Both sounded good by
  ear (`experiments/tts/hinglish.py` options B and C). Splitting by script into separate Hindi and
  English calls (option D) came out twice as long because of pauses at every switch; not judged by ear.

## Known gaps

- No streaming within a sentence: the delay before speech grows with the first sentence's length, so
  replies should start with a short sentence.
- Pocket TTS and Kokoro were uninstalled after the comparison; reinstalling needs CPU-only torch as a
  direct dependency (otherwise uv pulls ~3 GB of CUDA wheels).
