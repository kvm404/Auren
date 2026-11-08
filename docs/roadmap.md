# Roadmap

Auren targets voice-driven tasks on an Omarchy desktop. The first release is v0.1.0; see the
[changelog](../CHANGELOG.md) for its scope and limitations.

## Working today

- AssemblyAI streaming speech recognition with detected and user-managed vocabulary.
- Claude Code as the primary brain, Codex fallback on a usage limit before an action runs, and
  configurable brain modes, models, and effort.
- Local English speech with Supertonic 3 and adjustable voice settings.
- Desktop, browser, screen, shell, herdr-agent, and Excalidraw tools.
- Approval levels, bound confirmations, batch answers, cancellation, and the full-access send judge.
- Press-to-start and press-to-send controls, fresh conversations, and typed terminal conversations.
- Auren and Veyra mascots and a six-page Omarchy bar panel.
- XDG settings, detection of optional integrations, diagnostics, and portable installation.
- Published-release updates, service state preservation, and installation rollback.

## Planned

These are ideas for future work, not promises for a particular release:

- Dictation into the focused app using the same speech recognizer.
- Direct API-key model backends and a review of supported authentication options for wider distribution.
- Alternate speech-recognition backends and better handling of non-English input.
- Completion alerts for background agent jobs.
- User-installed mascots without edits to the shipped QML.
- History retention controls and a more restrictive read-only approval mode.
- Browser control in standalone web-app windows.
- Packaged distribution and an uninstall command.

## Provider decisions

The decisions for [voice](decisions/0001-assistant-voice.md),
[speech recognition](decisions/0002-speech-to-text.md), and [the brain](decisions/0003-brain.md)
explain the selected providers. Alternate speech-recognition providers and a third brain backend
are not wired into the runtime.
