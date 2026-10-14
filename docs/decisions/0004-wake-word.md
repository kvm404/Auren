# 0004: The wake word is heard by a small speech-to-text model on the laptop, then checked in the transcript

Date: 2026-10-14 · Status: accepted (revised twice the same day, after the user's own voice)

## Decision

- **Spotter:** a small streaming speech-to-text model on the laptop,
  [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx)'s `sherpa-onnx-streaming-zipformer-en-2023-06-26`
  (~70M parameters, int8 `chunk-16-left-128`, 73 MB: four files from its Hugging Face repo at a pinned
  revision, each pinned by SHA-256), with `blank_penalty` 1.0. `wake.find_name` looks for the name in its
  words.
- **Spellings:** how the model writes the name. One word (`ORIN`, `OREN`, `ORAN`, `URAN`, `AUREN`…)
  counts anywhere, so the name can stop Auren mid-reply; two words (`OR IN`, `'RE IN`, `ORE AN`…) only
  where a phrase starts (after a 0.5 s pause or a filler like "hey"), where "or in the…" rarely is.
  Sensitivity low = one word only, medium (default) = both, high = looser ones too (`ARE IN`, `ORN`).
- **Learn my voice:** the user says the name four times and one sentence without it; what the model
  wrote for the name is kept in `handsfree.heard_as` (minus words that were in the sentence, and minus
  common words on their own, and minus anything not within two letters of a known spelling: "ART",
  "MARRIED") and counts where a phrase starts.
- **Second check:** a hit from idle opens the speech service with the audio from just before the name
  (the model gives each word's time; 0.7 s more for its delay); AssemblyAI's transcript has to *start*
  with the name (fillers like "hey", "um" allowed). "I told Lauren…" is dropped without an answer. When
  the local model heard the name where a phrase starts, a transcript that doesn't start with it still
  counts unless it starts with another name ("Lauren", "Warren"…) or is about Auren ("Auren's…"):
  AssemblyAI wrote the user's lone "Auren" as "Auren." most times, but also as "All right.", "What is
  it?", "Or in" or nothing. If the local model heard nothing after the name, a short first turn (three
  words or fewer) is taken for the name; otherwise the text is sent as it is. (AssemblyAI's word times
  can't place the name: it spread a misheard "What is it?" over 2.6 s of a 0.6 s word.)
- **The speech service too (optional, `hear_with = service`):** each phrase the local model hears start
  also goes to AssemblyAI, quietly, up to its first sentence end; it counts if it starts with the name.
  At most `service_minutes` (30) a day of speech that wasn't a call, not while other apps play through
  the speakers. Off by default: it sends room speech, and costs.
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

The 20M model's 8/10 then turned out to be luck. Live, it caught the name once or twice after hands-free
was turned on and then rarely, and Learn my voice saved "ART", "MARRIED", "PARDON". The model decodes
in fixed chunks (~0.3 s), so what it writes depends on where the speech falls in them: the same recording
after 2 s or 8 s of silence comes out as different words. Fed after a random stretch of quiet, as a mic
that's been open a while is, it caught 0 to 7 of the 10 (4.2 on average, over 12 tries). The larger
2023-06-26 model writes the user's name the same way far more often ("OR IN", "ORIN", "ORAN"): 6 to 10.
Running two or four 20M streams offset by part of a chunk reached 5.5 and 6.2 at 8% and 14% of a core;
hotwords (contextual biasing) and resetting the encoder at each pause didn't help.

| Option | Custom word without training | CPU on this laptop (i3-1115G4) | Cost |
|---|---|---|---|
| sherpa-onnx KWS 3.3M (first version) | yes (word pieces) | 1.9% of one core, +34 MB RAM | free; 2/10 on the user |
| sherpa-onnx streaming ASR 20M, text match (second version) | yes | 3% of one core, +106 MB RAM | free; 4.2/10 |
| **sherpa-onnx streaming ASR 2023-06-26 (~70M), text match (chosen)** | yes | **~6% of one core**, +148 MB RAM | free; 7.5/10 |
| sherpa-onnx streaming Kroko (2025-08-06, 70 MB) | yes | 4% of one core | free; 4.2/10, written "Orion", "Urn" |
| openWakeWord | no: a model per word, trained from synthetic speech | small | free, training takes hours |
| Porcupine | yes, from their console | small | access key, licence limits |
| Streaming to AssemblyAI all the time | yes | none | ~$0.45 an hour: ~$11 a day |

A second recording (2026-10-15, 60 s, laptop mic, a noisier room: voice to noise 13 dB against 27 dB the
first time) made even the larger model fail: "HORRID", "AUDIUM", "ARID", "AEON" for the name, "HOW WOULD HE
AGENTS" for "How many agents are running?". 1.8 of 10 on average; 5.8 with spellings learned from the same
audio. A high-pass filter (150 or 300 Hz) gained 1 to 2 dB and nothing in words. AssemblyAI's batch model
wrote every name as "Orange"; its streaming model, with "Auren" as a key term, as "Auren". Fed in real
time through the speech-service check, all 10 were calls, and the plain questions weren't (42 of the 63
seconds were sent: the recording is nearly all speech). Hence the option, rather than a still larger local
model (the next, NeMo's streaming fast-conformer, is 457 MB).

## Measured (2026-10-14)

The user's recording: 30 s, "Auren" said 10 times with pauses, fed after 1 to 20 s of quiet (12 tries,
each its own alignment). Negatives: 68 clips (7 minutes) without the name: synthetic ones (with "Lauren",
"Warren", "or in the morning", "Oregon", "Aaron", "our engineers"), the user's 20 recordings from the
speech-to-text test, TTS samples and LibriSpeech clips.

| | User's "Auren": mean (worst) of 12 | Synthetic "Auren" (24) | False hits (68 clips) |
|---|---|---|---|
| KWS 3.3M, medium | 2/10 (one try) | 14/24 | 4 ("Lauren", "Warren") |
| 20M, medium | 4.2 (0) | 9/24 | 4 ("URAN") |
| **2023-06-26, low** | | 10/24 | **0** |
| **2023-06-26, medium** | **7.5 (6)** | 15/24 | **0** |
| **2023-06-26, high** | 7.5 (6) | 18/24 | **0** |
| **2023-06-26, medium, one learned spelling ("more in")** | **7.7 (6)** | | |

- The misses are "Hello. Orange." in one breath, and the name written as "MODERN", "GE", "WHAT".
  "HELLOORIN" (the greeting joined to the name) now counts.
- The name is reported about 1 s after it's said; the model's word times are 0.25 to 0.45 s late, so the
  audio sent (from 0.7 s before that) starts just before the name.
- End to end with the first version (a synthetic "Hey Auren, open Firefox." fed as the mic, real
  AssemblyAI): the brain got `Open Firefox.` ~1 s after the speech ended. "Auren stop." stopped without
  asking the brain.

## Notes

- `sherpa-onnx` and `sherpa-onnx-core` are pinned together (1.13.8): uv locked the first without the
  second, whose `libonnxruntime.so` it needs. It lives in the same process as Supertonic's onnxruntime
  without trouble.
- Downloading the new model removes the old ones (`models/kws-gigaspeech`, `models/asr-en-20m`).
- Speakers reach the mic. The daemon ignores the name while Auren's own sentence contains it (or
  "Lauren"/"Warren"/"or in"); headphones, or PipeWire's echo cancellation, make interrupting by name more
  reliable while Auren talks.
