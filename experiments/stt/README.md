# STT accuracy experiment

Which cloud speech-to-text gives the best accuracy on **this user's voice and vocabulary**, and
does letting an LLM combine two transcripts beat any single provider?

Background: Voisu's history on this laptop (122 recordings) shows Deepgram and Groq disagree on
~11% of words, mostly names and technical terms, and each catches the other's mistakes.

## Try it live

```sh
uv run live.py
```

Press Enter, speak, press Enter. The main setup (AssemblyAI streaming) shows your words live; when
you stop, all three setups report what they heard and how fast, and you can type what you said to
score them. The first round can be slower while connections warm up.

## Run it

```sh
cd ~/Projects/auren/experiments/stt
uv run record.py            # read 15 commands aloud (Enter to start, Enter to stop)
uv run record.py --free 5   # optional: 5 commands in your own words, then type what you said
uv run run.py               # every provider, with and without the vocabulary
uv run merge.py             # an LLM combines transcripts (pairs, and single-transcript fixes)
uv run score.py             # leaderboard
uv run score.py --errors    # every mistake, line by line
```

Recording tips: speak the way you'd talk to the assistant, not like reading. If you said something
different from the prompt, type what you actually said when asked. That's the answer key.

## Providers

| name | model | key |
|---|---|---|
| `deepgram-nova3` | Deepgram Nova-3 + `keyterm` boosting | Voisu keyring ✓ |
| `groq-whisper-v3` | Whisper large-v3 (Voisu's choice), temperature 0, vocabulary prompt | Voisu keyring ✓ |
| `groq-whisper-turbo` | Whisper large-v3-turbo, same settings | Voisu keyring ✓ |
| `elevenlabs-scribe2` | ElevenLabs Scribe v2 + `keyterms` | `ELEVENLABS_API_KEY` |
| `assemblyai-u35pro` | AssemblyAI Universal-3.5 Pro + `keyterms_prompt` | `ASSEMBLYAI_API_KEY` |

Providers without a key are skipped. To add one, put it in `experiments/stt/.env` (git-ignored):

```
ELEVENLABS_API_KEY=...
ASSEMBLYAI_API_KEY=...
```

Then `uv run run.py` again: it only runs what's missing. The ElevenLabs keyterm encoding (repeated
form fields) is unverified until a real key is tried.

## Results (2026-10-09, 20 recordings of the user, 215 words, 41 vocabulary terms)

| config | WER | terms right | median time (batch) |
|---|---|---|---|
| **AssemblyAI Universal-3.5 Pro + vocab** | **1.9%** | **41/41** | 3.75 s |
| Deepgram + Groq Whisper v3 (both + vocab), combined by LLM | 2.4% | 40/41 | ~0.8 s + 0.35 s LLM |
| Groq Whisper v3 + vocab, LLM-fixed | 4.3% | 38/41 | 0.65 s + 0.35 s |
| Deepgram Nova-3 + vocab, LLM-fixed | 5.3% | 38/41 | 0.77 s + 0.35 s |
| Deepgram Nova-3 + vocab | 8.7% | 33/41 | 0.77 s |
| Groq Whisper v3 + vocab | 9.1% | 26/41 | 0.65 s |
| AssemblyAI, no vocab | 10.6% | 23/41 | 3.56 s |
| Groq Whisper turbo + vocab | 10.6% | 23/41 | 0.53 s |
| Deepgram / Groq v3, no vocab | 15.4% / 15.9% | 21–22/41 | |

- AssemblyAI's 4 "errors" are not real misses: "notification"/"benchmark" (every provider heard the
  singular, so that's probably what was said) and "subagents" vs "sub agents" (spacing).
- The vocabulary list is the biggest lever: it cut every provider's errors by 40–80%.
- LLM combining rescues the weaker providers (Voisu's pair: 8.7% → 2.4%) but can't improve AssemblyAI.
- AssemblyAI batch mode is slow (~3.7 s); see the streaming follow-up below.
- f1/f2 answer keys were corrected where every provider independently heard extra words
  ("a new tab", "I want you to"); the originals are in `recordings/refs.original.json`.

### Streaming follow-up (same 20 recordings, sent at real-time speed)

Push-to-talk simulation (`stream_test.py`): audio streamed in 50 ms chunks, `ForceEndpoint` at
the end, time measured from the end of the audio to the final text.

| config | WER | terms | after you stop |
|---|---|---|---|
| **AssemblyAI streaming Universal-3.5 Pro, push-to-talk, vocab** | **1.9%** | **41/41** | **0.32 s** |
| ElevenLabs Scribe v2 (batch) + vocab | 1.9% | 41/41 | 1.32 s (whole request) |
| AssemblyAI streaming 3.5 Pro, turns ended by pauses | 3.8% | 40/41 | 0.30 s |
| AssemblyAI streaming 3.6 Pro, push-to-talk, pinned to English | 3.8% | 37/41 | 0.32 s |
| AssemblyAI streaming 3.6 Pro, push-to-talk | 6.2% | 36/41 | 0.34 s |

- Streaming 3.5 Pro matches batch accuracy at ~0.3 s instead of ~3.7 s.
- Push-to-talk mode matters: letting pauses split the turn broke sentences ("…tests. And the part
  project"). Use `min_turn_silence`/`max_turn_silence` = 10000 and end the turn with `ForceEndpoint`.
- 3.6 Pro (the new default) is worse here: it once answered in Hindi script, and misses herdr/Voisu/
  Hyprland even when pinned to English. Request `speech_model=universal-3-5-pro` explicitly.
- Keyterms must go in the connection URL (`keyterms_prompt` as a JSON list, max 100). Sending them via
  `UpdateConfiguration` got the session closed (1008).
- Opening sessions back-to-back sometimes returns 1008; a short wait and retry fixes it.
- ElevenLabs ties on accuracy (same 4 non-errors) but its streaming model wasn't tested.

## What the scores mean

- **WER**: word errors divided by words said; lower is better. Normalization ignores case,
  punctuation, number words vs digits ("two" = "2"), and spacing of vocabulary terms
  ("Open Code" = "OpenCode").
- **terms**: vocabulary words (herdr, Voisu, GitHub, F2…) that came out right.
- **best pick: A + B**: if you could always choose the better of the two transcripts. This is the
  ceiling for combining that pair.
- **pair:A+B@model**: an LLM given both transcripts and the vocabulary. **fix:A@model**: the same with
  one transcript. The real assistant brain would also know the screen and herdr panes, so these are
  a lower bound.
- **median s**: request time for a whole file, including upload. Streaming in the real assistant
  would finish sooner.

## Files

- `prompts.txt`: the commands to read (edit freely)
- `vocab.py` → `vocab.txt`: terms to bias toward: base list, `~/Projects` names, live herdr agents
- `record.py`: records `recordings/NN.wav` (16 kHz mono) and the answer key `recordings/refs.json`
- `providers.py`: one function per provider; `run.py`: runs them into `results/transcripts.json`
- `merge.py`: LLM combining into `results/merged.json` (Groq `qwen/qwen3.8-27b`, as in Voisu)
- `live.py`: talk to MAIN / BACKUP / COMBINATION live, with timings and scoring
- `stream_test.py`: AssemblyAI streaming at real-time speed (push-to-talk)
- `score.py`: the leaderboard; `common.py`: keys, normalization, WER

Keys are read from an env var, then `.env`, then Voisu's keyring entry (`secret-tool lookup
voisu-provider <name>`). They are never printed or saved.
