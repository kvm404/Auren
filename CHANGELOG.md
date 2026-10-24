# Changelog

## 0.1.0

First release of Auren, a voice assistant for the Omarchy desktop.

### Included

- Press-to-start, press-to-send voice input with AssemblyAI streaming and custom vocabulary.
- Claude Code conversations with Codex fallback on usage limits, configurable models and effort,
  cancellation, and fresh conversations.
- Local English speech using Supertonic 3, with voice, speed, quality, volume, and pause controls.
- Desktop app and workspace control, media, screenshots, reminders, and background shell jobs.
- Optional herdr coding agents, browser tools through Open Browser Use, screen control, and whole
  drawings and diagrams in Excalidraw.
- Auren and Veyra mascots with live captions, speech reactions, dragging, resizing, and sleep states.
- An Omarchy bar panel for conversation controls, mascots, voice, brain, vocabulary, and updates.
- Configurable approval levels, target-bound confirmations, batch yes/no answers, and a separate
  check for explicitly requested chat sends at full access.
- Portable user installation, editable development installs, `auren doctor`, and release-only
  updates with rollback after installation failure.
- MIT license, installation and privacy documentation, and contributor instructions.

### Limitations

- Omarchy is the supported desktop. Other Linux environments have not been validated.
- Speech recognition needs an AssemblyAI account and network access. The brain uses a signed-in
  Claude Code or Codex CLI; a direct API-key model backend is not implemented.
- Replies and speech synthesis are English-only. Speech recognition can still return other scripts.
- Mouse control needs user access to `/dev/uinput`. Standalone browser web apps use screen control.
- Safety classification is not a sandbox. Screenshots and tool output can contain sensitive data;
  local history and logs have no automatic expiry.
- Dictation mode, alternate speech-recognition backends, and background-job completion alerts are
  not implemented yet.
