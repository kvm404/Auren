# 0001: Assistant voice is local Phonon 31m

Date: 2026-10-09, revised 2026-11-19 · Status: accepted

Auren uses Gradium's Phonon 31m, through the `ptts` package, for local speech synthesis on the CPU.
Speech generation runs locally without sending reply text to another speech service. The phone app
speaks with the same model, so the PC and the phone sound alike.

The voices are four from Phonon's catalog, chosen for warmth: Everly (US female, the default),
Juniper (US female), Hugh (UK male) and Trevor (US male). They are one list, `VOICES` in
`src/auren/voice.py` (and `voiceChoices` in the bar plugin), so swapping one is a one-line change.
Choosing a mascot can select its voice: Auren speaks with Trevor, Veyra with Juniper. The defaults
are Everly, speed 1.0, and English.

## History

The first choice (2026-10-09) was Supertonic 3, selected over Pocket TTS and Kokoro for its CPU and
memory use and acceptable voice quality, with voice F2, 5 steps and speed 1.05. Phonon replaced it on
2026-11-19: it streams audio as it decodes (the first words play about 0.1 s after synthesis starts),
runs on the phone too, and its voices sound warmer. A Supertonic voice name left in `config.toml`
becomes Everly once, with a log line; `voice.steps` (Supertonic's quality) is gone.

## Consequences

- The model (about 100 MB) and the four voices must be downloaded during installation or with
  `auren voice-model`, into the Hugging Face cache at the revisions `ptts` pins. The daemon loads them
  from the cache only (`HF_HUB_OFFLINE`), so starting never waits on the network.
- Speech is generated sentence by sentence and each sentence is streamed in chunks, so the first words
  play while the rest is synthesized.
- Speed maps onto Phonon's `duration_delta` (2 × (1 − speed)). Phonon's pace barely changes past about
  0.85x and 1.25x, so speed is limited to 0.8–1.25.
- The daemon uses more memory than with Supertonic: about 760 MB once it has spoken (measured
  2026-11-19 on a 16-core desktop), against ~520 MB before.
- A failed voice-model load leaves text replies available.
- The runtime replies and synthesizes speech in English.
- `ptts` is pinned below its next minor version; model availability should be checked when preparing
  releases.

Implementation: `src/auren/voice.py`. User controls: [Talking to Auren](../daemon.md).
