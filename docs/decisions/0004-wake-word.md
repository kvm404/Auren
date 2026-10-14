# 0004: The wake word is spotted on the laptop with sherpa-onnx, then checked in the transcript

Date: 2026-10-14 · Status: accepted (to be tuned on the user's own voice)

## Decision

- **Spotter:** [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)'s keyword spotter with the
  `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` model (int8, ~6 MB used out of a 17 MB download,
  pinned by SHA-256). Open vocabulary: the name is given as word pieces, so no training is needed and the
  name can change later.
- **Keyword list:** `AUREN`, `OREN`, `ORIN`, `ORAN`, `URAN`, `AWREN` (how the name can sound). Sensitivity
  low/medium/high = threshold 0.3/0.2/0.12, boost 1.0/1.5/2.0; medium is the default.
- **Second check:** a spotter hit from idle opens the speech service with the 1.5 s of audio before it;
  the transcript has to *start* with the name (fillers like "hey", "um" allowed). "I told Lauren…" is
  dropped without an answer.
- **End of a request:** AssemblyAI's own turn detection, `min_turn_silence` 500 ms and
  `max_turn_silence` = `handsfree.end_silence` (1.2 s by default). Push-to-talk keeps 10 000/10 000.

## Why

| Option | Custom word without training | CPU on this laptop (i3-1115G4) | Cost |
|---|---|---|---|
| **sherpa-onnx KWS (chosen)** | yes (word pieces) | **1.7% of one core**, +34 MB RAM | free |
| openWakeWord | no: a model per word, trained from synthetic speech | small | free, training takes hours |
| Porcupine | yes, from their console | small | access key, licence limits |
| A small local ASR (sherpa zipformer 20M) matching text | yes | 2.7% of a core, 128 MB download | free |
| Streaming to AssemblyAI all the time | yes | none | ~$0.45 an hour: ~$11 a day |

The keyword spotter is the only one that needs no training, no account and almost no CPU.

## Measured (2026-10-14)

Clips: 24 synthetic positives (Supertonic F2, M3, F1, M1 saying "Auren, …", "Hey Auren, …"), 32 synthetic
negatives (with "Lauren", "Warren", "or in", "Oregon", "Aaron", "our engineers"), the user's 20 recordings
from the speech-to-text test, and 18 TTS sample clips.

| Threshold, boost | Positives heard | False hits |
|---|---|---|
| 0.25, 1.0 (only `AUREN`, `OREN`, `ORIN`, `AWREN`, `AURIN`) | 7/24 | 0 |
| **0.2, 1.5 (the list above)** | **14/24** | **only "Lauren" and "Warren"** (4/32), none in the user's own 20 clips |
| 0.15, 1.5 | 16/24 | 6/32, all "Lauren"/"Warren" |

- Synthetic voices are a rough stand-in: run `auren wake-test` and pick the sensitivity on the user's own
  voice. Saying the name on its own, then a short pause, is the most reliable.
- End to end (a synthetic "Hey Auren, open Firefox." fed as the mic, real AssemblyAI): the brain got
  `Open Firefox.` ~1 s after the speech ended. "Auren stop." stopped without asking the brain.
- sherpa-onnx's per-stream thresholds (`#0.6` in the keyword) made no difference, so there's no stricter
  spotter while Auren speaks; the daemon ignores the name while Auren's own sentence contains it (or
  "Lauren"/"Warren") instead.

## Notes

- `sherpa-onnx` and `sherpa-onnx-core` are pinned together (1.13.8): uv locked the first without the
  second, whose `libonnxruntime.so` it needs. It lives in the same process as Supertonic's onnxruntime
  without trouble.
- Speakers reach the mic. Headphones, or PipeWire's echo cancellation, make interrupting by name more
  reliable while Auren talks.
