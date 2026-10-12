# Architecture

Auren is private for now, built by two developers. The goal: it works on any Omarchy machine without
code edits, so it can be released later. This page is the agreed design (2026-10-12, reviewed with
Codex) and the order it is built in.

## Scope

- **Target: Omarchy only** (Hyprland, Wayland, PipeWire, the `omarchy-*` commands, the Omarchy shell).
  Relying on Omarchy is a feature: Auren behaves like the desktop already does.
- **Kept as is:** the pipeline (daemon: ears → brain → voice), tools over MCP, the mascot and bar
  plugin reading the state file, Python + uv, Quickshell. The folder layout stays; files are
  restructured when they are touched.

## Rules

1. **No personal values in code.** Anything specific to a machine or a person is detected, or comes
   from the user's files in `~/.config/auren/` (config, routines, extra vocabulary, extra instructions).
   Code defaults are generic.
2. **Detect first; config overrides.** The browser comes from the system default, projects from common
   folders, the coding agent from Omarchy's default or what's installed.
3. **Optional integrations have three states.** *Enabled* (config: `auto`, `true`, `false`),
   *installed* (its command exists: its tools and prompt rules are offered to the brain) and *ready*
   (checked when a tool runs, so a browser that is closed at login isn't lost).
4. **The daemon owns tool state.** Browser tab, screenshot geometry and jobs live in one long-running
   tool runtime. The MCP server the brains start is a thin adapter that forwards calls to it, so Claude,
   Codex and a future API brain behave the same.
5. **Safety is per user message.** The turn is counted once, where the message comes in. A
   confirmation is tied to what it resolved to (a window address, a tab and page) and checked again
   before it runs. A backup brain only takes over if no action has run yet.
6. **`auren doctor` explains every failure** with the fix.

## Pieces

| Piece | Where | Notes |
|---|---|---|
| Daemon | `src/auren/daemon.py` | Socket commands, state file, one request at a time |
| Paths and config | `src/auren/config.py` | XDG paths; `~/.config/auren/config.toml` |
| Ears | `src/auren/ears.py` | AssemblyAI streaming (decision 0002) |
| Brain | `src/auren/brain/` | Claude Code (Haiku) with Codex as backup (decision 0003) |
| Voice | `src/auren/voice.py` | Supertonic 3, model kept in Auren's data folder |
| Tools | `src/auren/tools.py`, `src/auren/server.py` | Tool runtime (in the daemon) and the MCP adapter |
| Hands | `src/auren/hands/` | desktop, herdr, browser, screen, shell |
| Profile | `src/auren/profile/` | What the brain knows about the machine |
| Mascot | `mascot/` | Quickshell, its own service |
| Bar plugin | `omarchy-plugin/` | Omarchy shell plugin |
| Contract | [ipc.md](ipc.md) | State file, level file, socket commands, files |

## Install layout

`install.sh` puts everything under `~/.local/share/auren/` (`$XDG_DATA_HOME/auren`):

- `venv/`: the Python environment, from `uv.lock` (`uv sync --frozen`). `--dev` installs the checkout
  editable, so code changes need only a service restart.
- `mascot/`, `bar-plugin/`: the UI files (copied, or symlinked to the checkout with `--dev`).
- `bin/auren-ctl`: what key bindings, the mascot and the bar plugin call.
- `models/`: the voice model.

The systemd units point at these paths, so the checkout can live anywhere.

## Build order

Each step is one commit; Auren keeps working after every one.

| Step | What |
|---|---|
| 0 | CI (pytest and ruff), contributor rules, this page and [ipc.md](ipc.md) |
| 1 | Central paths, single-daemon lock, `auren doctor`, portable install, voice model in Auren's folder, text-only mode if the voice fails |
| 2 | `config.toml` (replaces `settings.json`, migrated automatically) and `auren config` |
| 3 | Tool runtime in the daemon, MCP adapter, per-message turns, bound confirmations, safe fallback, cancel stops the brain |
| 4 | Integration states, a profile that survives missing parts, a plain shell runner without herdr, detection (browser, apps, projects, agent) |
| 5 | Brain: only the selected brain starts; models from config |

## Before a public release

- **Brain:** Anthropic's Agent SDK docs say: "Unless previously approved, Anthropic does not allow third
  party developers to offer claude.ai login or rate limits for their products". A public default needs
  an API-key brain (Claude Haiku 5.5), with screenshots passed as images. Driving the user's own Claude
  Code or Codex login has to be decided, not assumed.
- **Mascots:** load extra mascots from `~/.config/auren/mascots/`. (Clawd, the Claude Code mascot, was
  removed: Anthropic's branding rules say products must not look like Claude Code. Auren is the default.)
- **Plugin id** `krvm.auren` → a neutral id (migrating the shell config).
- **Safety:** a careful mode for new installs (only read-only commands run without asking).
- **Privacy:** say what is sent to the model (profile, routines, pages, screenshots) and how long
  history is kept.
- **Voice:** Supertonic's repository is archived; decide whether to keep it.
- License, AUR package, docs split (user vs developer), history cleanup.
