# Auren

A personal voice assistant for an Omarchy (Hyprland) laptop. Press a key, say what you want, and
Auren does it on the computer and answers out loud: it opens apps and web apps, controls windows and
media, prompts coding agents running in herdr, browses in your real logged-in browser, and, when
nothing else reaches, looks at the screen and uses the mouse and keyboard. A small mascot floats over
the desktop and shows what it's doing, and a bar plugin gives quick controls.

<img src="docs/images/bar-panel.png" alt="Auren's panel in the Omarchy bar" width="340" align="right">

- **Talk:** Super+A to start, Super+A again to send. Super+Shift+A starts a fresh conversation.
  Pressing while Auren speaks interrupts it.
- **Ears:** AssemblyAI streaming speech-to-text, biased toward your own vocabulary (app names,
  projects, tools).
- **Brain:** Claude Haiku 5.5 through a Claude Code process that stays running, with Codex
  (GPT-6.1 Sol) as an automatic backup.
- **Hands:** about 40 tools over MCP, covering apps, windows, workspaces, media, volume, shell commands,
  herdr agents and jobs, the browser (Open Browser Use), screen control (look, click, drag, type) and
  saved routines.
- **Voice:** Supertonic 3, running locally on the CPU, voice F2.
- **Safety:** risky actions (buying, sending, deleting, `sudo` and the like) ask first. Text from
  screens and web pages is treated as untrusted.
- **Mascot:** Clawd floats over every workspace. It listens, thinks, walks while working, bounces to
  the voice and shows captions in a speech bubble. Drag it anywhere; it fades and falls asleep when
  idle.
- **Bar plugin:** a tiny Clawd in the Omarchy bar. Its panel has on/off switches, voice, brain,
  fresh start, stop and the last exchange.

<br clear="right">

## How it works

```
 Super+A ─► daemon ─► ears (AssemblyAI) ─► brain (Claude Code, Haiku 5.5) ─► voice (Supertonic)
              │                                 │ MCP
              │                                 ▼
              │                           hands: desktop · herdr · browser · screen · routines
              ▼
   $XDG_RUNTIME_DIR/auren/state.json + level ─► mascot (Quickshell) · bar plugin (Omarchy shell)
```

- `auren daemon` is a systemd user service. It listens on a Unix socket; `bin/auren-ctl` sends it
  commands such as `talk`, `talk-fresh`, `cancel`, `voice off` and `brain codex`.
- The brain gets a live snapshot with every request (time, windows, herdr agents) plus a cached
  profile of the laptop: apps, key bindings, aliases, projects and Omarchy commands.
- The mascot and the bar plugin only read the state file and the loudness level, so either can crash
  or restart without affecting the daemon.

## Requirements

- Omarchy (Hyprland, Wayland), PipeWire. Developed on an i3 with 8 GB RAM, so everything is light.
- `uv`, `quickshell` (`qs`), `socat`, `wtype`, `grim`, and write access to `/dev/uinput` for screen
  control.
- Claude Code (`claude`, logged in) for the brain; Codex (`codex`) for the backup brain.
- Optional: `herdr` for coding agents, Open Browser Use (`obu` plus its browser extension) for the
  browser tools.
- An AssemblyAI API key.

## Install

```bash
git clone git@github.com:kvm404/Auren.git ~/Projects/auren
cd ~/Projects/auren
./install.sh --check     # what's missing
./install.sh             # Python env, services, bar plugin, secrets file
```

Then:

1. Put your key in `~/.config/auren/secrets.env` (`ASSEMBLYAI_API_KEY=…`).
2. Add the two key bindings and the layer rule that `install.sh` prints to your Hyprland config.
3. Restart the services: `systemctl --user restart auren auren-mascot`.

## Using it

| | |
|---|---|
| Super+A | Start talking; press again to send |
| Super+Shift+A | The same, in a fresh conversation (or say "start fresh") |
| Click the mascot | Talk (same as Super+A); while it speaks, stop it |
| Drag / scroll / right-click the mascot | Move / resize / poke |
| Bar icon | Open the panel; right-click for a fresh conversation |
| `auren chat` | Type requests in a terminal and see the tool calls |
| `bin/auren-ctl status` | Daemon state; also `voice on\|off`, `voice-name F2\|M3\|F1\|M1`, `brain auto\|claude\|codex`, `forget`, `quit` |

Files:

| Path | What |
|---|---|
| `~/.config/auren/secrets.env` | API keys (chmod 600) |
| `~/.config/auren/settings.json` | Voice on/off, voice name, brain mode (written by the panel and `auren-ctl`) |
| `~/.config/auren/mascot.json` | Mascot preferences: `idleOpacity`, `fadeAfter`, `sleepAfter`, `bubble`, `sleepAnimation` |
| `~/.config/auren/routines/*.md` | Your saved workflows, given to the brain |
| `~/.local/state/auren/` | History, daemon log, herdr jobs, mascot position |

## Project layout

```
src/auren/         daemon, ears, voice, brain/, hands/, server (MCP tools), safety, profile/
mascot/            the floating mascot (Quickshell); mascots/clawd/ is the character
omarchy-plugin/    the bar plugin (krvm.auren)
systemd/           unit templates (install.sh fills in the path)
bin/auren-ctl      send a command to the daemon
docs/              how each piece works, decisions, research
experiments/       the TTS, STT and brain comparisons behind the decisions
tests/             uv run pytest
```

## Docs

- [Roadmap](docs/roadmap.md): pieces, status, what's next.
- Decisions: [voice](docs/decisions/0001-assistant-voice.md),
  [speech-to-text](docs/decisions/0002-speech-to-text.md), [brain](docs/decisions/0003-brain.md).
- How it works: [daemon](docs/daemon.md), [hands](docs/hands.md), [safety](docs/safety.md),
  [system profile](docs/system-profile.md), [mascot](docs/mascot.md), [bar plugin](docs/bar-plugin.md).

## Credits

- Clawd is the Claude Code mascot by Anthropic, drawn here for personal use. Auren's own mascots,
  Veyra and Auren, are planned.
- [Supertonic](https://github.com/supertone-inc/supertonic) for the voice, AssemblyAI for speech
  recognition, Open Browser Use for the browser, Quickshell and Omarchy for the desktop side.

Personal project, not licensed for reuse.
