# Local TTS experiment

Which free, local text-to-speech engine is good enough for the assistant's voice on this laptop
(i3-1115G4, 2 cores / 4 threads, 7.5 GB RAM, no GPU)?

**Outcome: Supertonic 3, voice F2, 5 steps, speed 1.05**, English first. The decision and its
reasons are in [`docs/decisions/0001-assistant-voice.md`](../../docs/decisions/0001-assistant-voice.md).
Pocket TTS and Kokoro were compared, then uninstalled; their numbers are kept below.

## Try it

```sh
uv run say.py supertonic-fast "On it. Opening Firefox on workspace two."   # live, voice F2
uv run say.py supertonic-fast -i          # type lines, Ctrl-D to quit
uv run voices.py                          # all 10 voices, each announced first
uv run voices.py F2 M3 --speed 0.95       # a shortlist at another speed
uv run hinglish.py B C                    # Hinglish written all-Devanagari vs mixed script
```

Supertonic has 10 built-in voices (F1–F5 female, M1–M5 male) and can load custom voices cloned in
its [Voice Builder](https://supertonic.supertone.ai/voice_builder) (`uv run voices.py my_voice.json`).

## Results on this laptop (2026-10-09)

Speed and latency (`./run_all.sh`, then `uv run compare.py`):

| engine | first audio (short line) | speed (long text) | peak RAM | Hindi |
|---|---|---|---|---|
| **supertonic-fast** (5 steps, chosen) | 0.51 s | **6.2x** real-time | 0.5 GB | yes |
| supertonic (8 steps) | 0.72 s | 4.3x | 0.6 GB | yes |
| pocket-int8 *(removed)* | 0.05 s | 4.5x | 1.0 GB | no |
| pocket *(removed)* | 0.11 s | 3.4x | 1.0 GB | no |
| kokoro *(removed)* | 1.53 s | 2.3x | 0.6 GB | yes |
| kokoro-int8 *(removed)* | 3.27 s | 0.8x, stalls | 0.4 GB | yes |

Resource cost while generating (`./run_resources.sh`):

| engine | idle RAM | peak RAM | CPU per second of speech | cores busy |
|---|---|---|---|---|
| **supertonic-fast** | 489 MB | 508 MB | **0.37 s** | 2.3 of 4 |
| pocket-int8 *(removed)* | 853 MB | 1020 MB | 0.38 s | 1.9 |
| pocket *(removed)* | 843 MB | 1019 MB | 0.51 s | 1.7 |
| supertonic (8 steps) | 487 MB | 509 MB | 0.54 s | 2.2 |
| kokoro *(removed)* | 469 MB | 566 MB | 0.86 s | 2.0 |
| kokoro-int8 *(removed)* | 263 MB | 389 MB | 2.24 s | 2.0 |

By ear: every engine was fine in English. Only Supertonic spoke Hindi properly. Hinglish in Roman
letters failed everywhere; written in Devanagari (all of it, or Hindi words only with English words
left in Latin) it works on Supertonic. Supertonic voices differ in natural pace for the same line:
M3 7.7 s, F2 8.3 s, F1/M1 8.8 s.

## Files

- `engines.py`: Supertonic adapter with a `stream(text, voice, lang)` that yields one chunk per sentence
- `say.py`: speak live through `pw-cat` and print the latency
- `voices.py`: audition the 10 voices live
- `hinglish.py`: four ways of writing Hinglish (romanized, all Devanagari, mixed script, split by script)
- `bench.py`, `run_all.sh`, `compare.py`: latency and speed benchmark, results table, `compare.py play <sample>`
- `resources.py`, `run_resources.sh`: RAM and CPU cost

Python 3.12. The model (~380 MB) is cached in `~/.cache/supertonic3/`.
