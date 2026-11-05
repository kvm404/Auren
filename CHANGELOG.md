# Changelog

## 0.1.2

### Changed

- Ready for Omarchy 4.5, which binds Super+A to Select all. Hyprland runs every binding on a key, so
  Auren's Super+A would also select everything in the focused window. The lines `./install.sh`
  prints now unbind Select all (Ctrl+A still works) and the ChatGPT web app on Super+Shift+A before
  binding Auren's keys, and offer Super+Alt+A and Super+Ctrl+Alt+A for anyone who wants to keep them.
  On an existing install, add `hl.unbind("SUPER + A")` above Auren's line in
  `~/.config/hypr/bindings.lua`.

### Added

- `auren doctor` shows which keys run Auren, and warns when one of them also runs another binding.

## 0.1.1

### Added

- `spotify_play`: "play X in Spotify" opens the search through Spotify's D-Bus interface, clicks the
  Top result's play button once, and reports what is actually playing. If a different track started,
  Auren says so instead of claiming it played what you asked for. It needs mouse control.

### Changed

- Claude keeps the same conversation until 80,000 context tokens (was 40,000) before starting fresh.
- Ctrl+digit shortcuts (Ctrl+2 for the second browser tab) are pressed as physical keys, so Chromium
  browsers pick the right tab. Without `/dev/uinput` access they fall back to `wtype`.
- The README explains that Auren uses your own signed-in Claude Code or Codex, and why Claude Haiku
  is the default brain, with measured response times.

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
