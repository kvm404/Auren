# 0004: The wake word is heard by a small speech-to-text model on the laptop, then checked in the transcript

Date: 2026-10-14 · Status: accepted (revised the same day, after the user's own voice)

## Decision

- **Spotter:** a small streaming speech-to-text model on the laptop,
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)'s `sherpa-onnx-streaming-zipformer-en-20M-2023-02-17`
  (int8, 44 MB: four files from its Hugging Face repo at a pinned revision, each pinned by SHA-256).
  `wake.find_name` looks for the name in its words.
- **Spellings:** how the model writes the name. One word (`ORIN`, `OREN`, `ORAN`, `URAN`, `AUREN`…)
  counts anywhere, so the name can stop Auren mid-reply; two words (`OR IN`, `'RE IN`, `ORE AN`…) only
  where a phrase starts (after a 0.5 s pause or a filler like "hey"), where "or in the…" rarely is.
  Sensitivity low = one word only, medium (default) = both, high = looser ones too (`ARE IN`, `ORN`).
- **Learn my voice:** the user says the name four times and one sentence without it; what the model
  wrote for the name is kept in `handsfree.heard_as` (minus words that were in the sentence, and minus
  common words on their own) and counts where a phrase starts.
- **Second check:** a hit from idle opens the speech service with the audio from just before the name
  (the model gives each word's time; 0.7 s more for its delay); AssemblyAI's transcript has to *start*
  with the name (fillers like "hey", "um" allowed). "I told Lauren…" is dropped without an answer. When
  the local model heard the name where a phrase starts, a transcript that doesn't start with it still
  counts unless it starts with another name ("Lauren", "Warren"…) or is about Auren ("Auren's…"):
  AssemblyAI wrote the user's lone "Auren" as "Auren." most times, but also as "All right.", "What is
  it?", "Or in" or nothing. If the local model heard nothing after the name, a short first turn (three
  words or fewer) is taken for the name; otherwise the text is sent as it is. (AssemblyAI's word times
  can't place the name: it spread a misheard "What is it?" over 2.6 s of a 0.6 s word.)
- **End of a request:** AssemblyAI's own turn detection, `min_turn_silence` 500 ms and
  `max_turn_silence` = `handsfree.end_silence` (1.2 s by default). Push-to-talk keeps 10 000/10 000.

## Why

The first version used sherpa-onnx's keyword spotter (`kws-zipformer-gigaspeech-3.3M`), which hears a
list of spellings given as word pieces. It caught 14 of 24 synthetic "Auren"s, but on the user's own
voice it caught the first two and then almost never: **2 of 10** in a recording of them saying it, at
any threshold, gain or spelling. Their "Auren" sounds like "Orange" to AssemblyAI, and the 3.3M model
hears it as "BUT IN", "MORRI", "ORDIN". The 20M speech-to-text model writes the same recording as "OR
IN", "ORIN", "ORAN", "MORN": close enough to match, and different for each voice, hence Learn my voice.
Running the 20M model as a keyword spotter instead (its `KeywordSpotter` mode) heard 0 of 10.

| Option | Custom word without training | CPU on this laptop (i3-1115G4) | Cost |
|---|---|---|---|
| sherpa-onnx KWS 3.3M (first version) | yes (word pieces) | 1.9% of one core, +34 MB RAM | free; 2/10 on the user |
| **sherpa-onnx streaming ASR 20M, text match (chosen)** | yes | **~4% of one core**, +96 MB RAM | free; 8/10, 9/10 learned |
| openWakeWord | no: a model per word, trained from synthetic speech | small | free, training takes hours |
| Porcupine | yes, from their console | small | access key, licence limits |
| Streaming to AssemblyAI all the time | yes | none | ~$0.45 an hour: ~$11 a day |

## Measured (2026-10-14)

The user's recording: 30 s, "Auren" said 10 times with pauses. Negatives: 70 clips (7 minutes) without
the name: 32 synthetic (with "Lauren", "Warren", "or in the morning", "Oregon", "Aaron", "our
engineers"), the user's 20 recordings from the speech-to-text test, 16 TTS samples and 2 LibriSpeech
clips.

| | User's "Auren" | Synthetic "Auren" (24) | False hits (70 clips) |
|---|---|---|---|
| KWS 3.3M, medium | 2/10 | 14/24 | 4 ("Lauren", "Warren") |
| **20M, low** | 3/10 | 4/24 | 2 (both "Lauren went to the store") |
| **20M, medium** | **8/10** | 11/24 | **2** (the same) |
| **20M, high** | 8/10 | 14/24 | 2 (the same) |
| **20M, medium, after Learn my voice** | **9/10** | | |

- The miss after learning is "Hello. Orange." said in one breath (no pause before the name).
- Synthetic misses are mostly "Auren." said alone, which this model writes as "N" or nothing; each voice
  can teach it with Learn my voice.
- The name is reported 0.5 to 1 s after it's said; the audio sent starts 0.1 to 0.2 s before it.
- The "Lauren" false hits are turned away by the transcript check.
- End to end with the first version (a synthetic "Hey Auren, open Firefox." fed as the mic, real
  AssemblyAI): the brain got `Open Firefox.` ~1 s after the speech ended. "Auren stop." stopped without
  asking the brain.

## Notes

- `sherpa-onnx` and `sherpa-onnx-core` are pinned together (1.13.8): uv locked the first without the
  second, whose `libonnxruntime.so` it needs. It lives in the same process as Supertonic's onnxruntime
  without trouble.
- Downloading the new model removes the old one (`models/kws-gigaspeech`).
- Speakers reach the mic. The daemon ignores the name while Auren's own sentence contains it (or
  "Lauren"/"Warren"/"or in"); headphones, or PipeWire's echo cancellation, make interrupting by name more
  reliable while Auren talks.
