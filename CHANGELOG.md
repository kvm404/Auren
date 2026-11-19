# Changelog

## Unreleased

### Added

- A phone remote: an Android app (`android/`) that talks to Auren on your PC over the same Wi-Fi or
  Tailscale. Hold to talk, hands-free or typed requests; see what Auren is doing and its replies (read
  aloud on the phone if you like); answer its questions and stop it; get the links, text, images and
  files it sends with the new `send_to_phone` tool; share from any app to Auren, the PC's clipboard or
  the PC's browser; a Quick Settings tile. Pair with `auren phone pair` (a QR code), list with
  `auren phone devices`, unpair with `auren phone revoke`. One turn at a time across the desk and the
  phone; phone replies aren't spoken on the PC. Replies are spoken on the phone itself with the same
  Phonon voices. The server listens only on the Tailscale address and private LAN addresses, over TLS
  pinned to the PC's own certificate; `[phone]` in `config.toml` turns it off, and `auren doctor`
  checks it, the firewall included. Omarchy only for now. See [docs/phone.md](docs/phone.md).

### Changed

- The voice is now Gradium's Phonon 31m (through `ptts`) instead of Supertonic 3: four voices, Everly
  (the default), Juniper, Hugh and Trevor, picked from the bar panel, `auren-ctl voice-name` or the
  phone (`voice.set`). Speech streams in chunks, so the first words play sooner. Speed now runs 0.8–1.25
  (default 1.0) and the quality setting is gone. A Supertonic voice in `config.toml` becomes Everly once
  (logged), `voice.steps` is dropped and an out-of-range speed is clamped. Auren now speaks with Trevor
  and Veyra with Juniper when the voice follows the mascot.
- Updating downloads the new voice (about 100 MB) by itself: `auren update` and `auren setup` fetch it,
  and after a package update (`yay`) Auren fetches it the first time it starts, showing replies as text
  until it's ready. The old Supertonic model in `~/.local/share/auren/models/` (about 380 MB) isn't used
  any more and can be deleted.

## 0.1.6

### Added

- Auren runs on Apple Silicon Macs with macOS 14 or newer, as a preview. `./install-macos.sh` installs
  it with two launch agents (the daemon and the mascot), Control+Option+A as the talk key
  (`keys.talk`), the robot in a floating window and its controls in the menu bar. Screen control,
  windows, apps, the clipboard, Spotify, the browser tools and coding agents (in herdr, or a new
  terminal window without it) use macOS's own interfaces. See [docs/macos.md](docs/macos.md).
- On the Mac, Auren is an app: the installer builds Auren.app in `~/Applications`, with the robot as
  its icon, and Auren runs through it. Settings lists the permissions under Auren, not a Python in a
  hidden folder. Opening the app (or **Set up Auren…** in the menu bar) shows a setup window that
  asks for the microphone, Accessibility and Screen Recording, takes the AssemblyAI key and checks
  for Claude Code or Codex, one button each.
- Auren is an Arch package: `yay -S auren`, then `auren`. The package installs the program in `/usr`
  (with its own Python environment) and the services for every user; the first `auren` runs
  `auren setup`, which does the per-user part: talk keys, bar plugin, AssemblyAI key, voice model,
  browser tools, starting Auren. Its questions use gum, like Omarchy's own scripts, and come first.
- `auren setup` runs again safely, takes flags for every question (`--keys`, `--browser`, `--brain`,
  `--yes`), and moves an `install.sh` install to the package, keeping settings, keys and the voice
  model. `auren setup --remove` undoes it (`--purge` also deletes settings and history).
- Updating a package install from the bar opens a terminal that runs `yay -S --needed auren` between
  stopping Auren and starting it again. An `install.sh` install still updates itself, and says once
  afterwards that the package exists.
- Installed with `./install.sh` before? Nothing changes until you switch: run `yay -S auren`, then
  `/usr/bin/auren setup` once. It replaces the old copy and keeps your settings, keys, voice model
  and history. On a Mac, Auren keeps installing and updating with `install-macos.sh`.

### Changed

- **Full access now means full access.** A new level, `never`, shown as Full access, asks about
  nothing: sending, publishing, root, packages, secrets, buying and wiping just run. The old Full
  access (`free`: windows and files run, sending and the floor still ask) is now called Auto.
  `auren-ctl safety never` or the panel's Brain page sets it; existing configs keep what they had.
- `install.sh` is for working on Auren from a checkout. It installs the program, then runs
  `auren setup`, so both ways share one setup (its flags are the same). The bar plugin runs
  `~/.local/share/auren/bin/auren`, which both create.
- Auren's services no longer skip starting when the session's display isn't there yet at login
  (systemd never retries a skipped condition): they wait for it and restart once it's there.

### Fixed

- "Start Claude Code with Opus 5.5 on high effort" starts it with that model and effort. `start_agent`
  takes `model` and `effort`, as said ("opus 5.5" becomes `claude-opus-5-5`, "extra high" becomes
  `xhigh`), and passes them to Claude Code (`--model`, `--effort`) or Codex (`--model`,
  `model_reasoning_effort`). Before, they were dropped.
- A new agent no longer waits at the folder-trust question. Auren marks the folder trusted in Claude
  Code's or Codex's own config before starting it, and answers yes if the question shows anyway.
  Claude Code now highlights "No, exit" first, so Enter alone would have quit it.
- With the brain on auto and only Codex installed, Codex answers. Before, Auren kept trying to start
  Claude Code, and every request ended without a reply. A brain that isn't installed is now said out
  loud as an error.
- Two Codex turns at once (`auren ask` while the daemon answers) no longer swap replies: each run
  writes its reply to a file of its own.
- The brain's working folder must belong to you and be closed to others; one an older Auren left open
  is closed up.
- Risky shortcuts are recognized in any modifier order and under any modifier name (`shift+ctrl+q`
  asks, like `ctrl+shift+q`). Shortcuts that delete for good or empty the Trash count as wiping, and
  logging out as a system action, so they ask at every level but Full access.

## 0.1.5

### Added

- The installer asks which keys start Auren (Super+A, or Super+Alt+A to keep Omarchy's Select all)
  and adds them to `~/.config/hypr/bindings.lua`, after saving a copy as `bindings.lua.bak.<time>`.
  It doesn't ask again once Auren is bound, or during `auren update`. `--keys=super`, `--keys=alt`
  and `--no-keys` answer without asking.

## 0.1.4

### Added

- Auren sets up its browser tools itself. `auren browser setup` (or `./install.sh --browser`, or just
  asking Auren) downloads Open Browser Use, registers it with your browser and opens its extension's
  page for you to add. The installer asks about it with its other questions. `auren browser` shows
  which browsers and profiles are connected, and `auren browser use` pins one.

### Changed

- The browser tools work in any Chromium browser and profile where Open Browser Use is set up, with
  no `browser.sockets` setting needed. Auren tells each Open Browser Use connection's browser and
  profile apart, and sends every command to the right one. Bookmarks and downloads come from that
  same profile.
- On Omarchy, the default browser is the one Omarchy opens links in. Auren used to fall back to the
  first browser installed, because `xdg-settings` names Omarchy's launcher script.
- When the browser tools aren't set up, they're still offered, so Auren can say what's missing and
  offer to set them up. With `integrations.browser = false` they stay hidden.
- The bar plugin's ID is now `kvm404.auren` (it was `krvm.auren`). Installing or updating moves the
  widget over in the same place on your bar. If you call the panel from a script or key binding,
  use `omarchy-shell kvm404.auren toggle`.

## 0.1.3

### Added

- Forgetting to press Super+A a second time no longer leaves Auren listening. After 3 s without new
  words, the mascot's bubble (or the OSD) says "Press Super+A to send"; after 12 s it sends by itself.
  Saying nothing at all stops listening after 12 s.
- Auto-send: turn it on in the panel's Voice page (or `auren-ctl autosend on`, `listen.autosend` in
  `config.toml`) and Auren sends as soon as you stop talking, after 5 s by default (2, 3, 5 or 8 s in
  the panel, 1 to 15 in the config). Quiet is counted from the transcript, so background noise doesn't
  hold a turn open. Off by default.

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
