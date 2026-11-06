# 0002: Speech recognition uses AssemblyAI streaming

Date: 2026-10-10 · Status: accepted

Auren uses AssemblyAI streaming with `speech_model=universal-3-5-pro` and a custom vocabulary.
The original evaluation favored this setup for recognizing application and project names and
returning the final transcript promptly after input ends. Streaming avoids a separate upload of
an entire recording after the user finishes speaking.

## Connection behavior

- PipeWire supplies 16 kHz, mono, signed 16-bit PCM audio.
- The connection uses `wss://streaming.assemblyai.com/v3/ws` with the API key in the authorization header.
- Both turn-silence parameters are set to 10,000 ms so pauses do not split a request.
- Sending the request ends microphone capture and sends `ForceEndpoint` and `Terminate`.
- Up to 100 vocabulary terms are sent through `keyterms_prompt` in the connection URL.
- Microphone reads are regrouped into messages of 50 to 1,000 ms; the final short message is padded.
- The connection is open only during a listening session. The daemon limits each session to two minutes,
  and ends it sooner when no new words arrive: 12 s by default, or the auto-send delay
  ([daemon.md](../daemon.md)). That timer is the daemon's own; the turn-silence parameters above stay as they are.

## Consequences

Speech input requires an AssemblyAI key and network access. The request includes `language_codes=["en"]`,
but the selected model has returned non-English scripts in testing. English-only replies are enforced
by the brain and voice, not guaranteed by speech recognition.

ElevenLabs, Deepgram, and Groq were evaluated as alternatives. There is no speech-recognition fallback
wired into the runtime. Provider pricing and limits depend on the account and are not fixed by Auren.

Implementation: `src/auren/ears.py`. Data handling: [Privacy](../privacy.md).
