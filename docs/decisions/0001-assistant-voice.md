# 0001: Assistant voice is local Supertonic 3

Date: 2026-10-09 · Status: accepted

Auren uses Supertonic 3 for local speech synthesis. It was selected over Pocket TTS and Kokoro in
the original evaluation for its CPU and memory use and acceptable voice quality. Speech generation
runs locally without sending reply text to another speech service.

The defaults are voice F2, 5 steps, speed 1.05, and English. The panel also offers M3, F1, and M1;
voice selection and tuning are configurable. Choosing a mascot can select its matching voice.

## Consequences

- The voice model must be downloaded during installation or with `auren voice-model`.
- Speech is generated sentence by sentence. The first sentence plays while later sentences are
  synthesized; long opening sentences delay the first audio.
- A failed voice-model load leaves text replies available.
- The runtime replies and synthesizes speech in English. Earlier Hindi and Hinglish trials are not
  part of the current language support.
- The model loader uses pinned download revisions and an archive fallback. Model availability
  should be checked when preparing releases.

Implementation: `src/auren/voice.py`. User controls: [Talking to Auren](../daemon.md).
