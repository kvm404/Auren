# 0002: Speech-to-text is AssemblyAI streaming, with ElevenLabs as backup

Date: 2026-10-10 · Status: accepted

## Decision

| Role | Choice |
|---|---|
| **Main** | **AssemblyAI streaming, `speech_model=universal-3-5-pro`**, push-to-talk, self-building vocabulary |
| **Backup** | **ElevenLabs Scribe v2**, preferably its realtime streaming model (not yet tested) |
| **Not used** | Deepgram + Groq Whisper combined by an LLM; local models (Parakeet, Moonshine) |

The assistant has its own lean "ears" module. It does not integrate Voisu, which the user keeps for
daily dictation.

### Required settings for the main setup

- Endpoint `wss://streaming.assemblyai.com/v3/ws`, 16 kHz `pcm_s16le`, API key in the `Authorization` header.
- Request `universal-3-5-pro` explicitly. The newer default, 3.6 Pro, was worse on the user's voice:
  it once answered in Hindi script and missed herdr/Voisu/Hyprland even when pinned to English.
- **English only:** `language_codes=["en"]` (a JSON list) in the URL. AssemblyAI documents it for 3.6 Pro;
  **3.5 Pro accepts it but ignores it**: tested 2026-10-17, Hindi speech still comes back in Devanagari
  (so did 3.6 Pro). The only hard English-only option found is `universal-streaming-english`: it never
  writes Devanagari (Hindi comes out as phonetic Latin text) but it ignores the vocabulary
  (herdr became "herdor") and drops punctuation. Not adopted yet.
- **Push-to-talk:** `min_turn_silence=10000` and `max_turn_silence=10000`, and send
  `{"type":"ForceEndpoint"}` when the key is released. Letting pauses end turns split sentences and
  added errors.
- **Vocabulary:** `keyterms_prompt` as a JSON list (max 100) **in the connection URL**. Sending it via
  `UpdateConfiguration` got the session closed (1008).
- Audio messages must be 50–1000 ms each. Smaller mic reads must be regrouped (otherwise error 3007).
- Open the connection only while the key is held: billing is per second the connection is open.

## Why

Tested on the user's own voice (`experiments/stt/`: 20 recorded commands, plus live long-form tests):

| Setup | Error rate | Terms right | Text ready after you stop |
|---|---|---|---|
| **AssemblyAI streaming 3.5 Pro + vocab** | **1.9%** (no real misses) | **41/41** | **~0.3 s**, independent of length |
| ElevenLabs Scribe v2 (batch) + vocab | 1.9% (same non-misses) | 41/41 | 1.3 s; 3–4 s for 45–75 s of speech |
| Deepgram + Groq Whisper, LLM-combined | 2.4% | 40/41 | 5–8 s on long speech; misheard "herdr" as "hardware" |
| Best single provider without help | 8.7% | 33/41 | |

- The vocabulary list was the biggest lever: it cut every provider's errors by 40–80%.
- On long, natural speech (45 s and 74 s), main and backup were both accurate. Only streaming stayed
  fast regardless of length.
- Artificial Analysis (independent, general datasets, no custom vocabulary) also ranks Scribe v2 and
  AssemblyAI near the top, and Deepgram Nova-3 lower.

## Cost (verified in the user's dashboards, 2026-10-10)

- **AssemblyAI:** about $0.45/hour of streaming, vocabulary included. $50 free credit (no card); all
  testing cost $0.16. At ~5 min of speech a day that's ~$1.10/month (credit lasts ~3½ years); at
  20 min a day, ~$4.50/month. Free-tier limit: 5 new streaming connections per minute.
- **ElevenLabs:** free plan, 10,000 credits/month; speech-to-text measured at ~78 credits/minute, so
  ~129 minutes/month, plenty for a backup.

## Open items

- ElevenLabs Scribe v2 Realtime (streaming backup) is untested; verify accuracy and keyterms
  (realtime allows 50 terms of ≤20 characters).
- Untested leaders on Artificial Analysis: Microsoft MAI-Transcribe-2 and Muse Voice Transcribe.
- AssemblyAI keeps filler words ("uh"); fine for the brain, matters only if the text is shown verbatim.
