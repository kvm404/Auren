# Architecture

Auren is a voice assistant for Omarchy. It should work on an Omarchy machine without personal code
edits. This page describes the current component boundaries and contributor rules. The original
design was agreed on 2026-10-12; release preparation is tracked through pull requests.

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
- `install.json`: what was installed (version, commit, branch, the repo it came from, `install` or `dev`),
  written last, so it only names an install that finished. `auren update` compares it with GitHub.

The systemd units point at these paths, so the checkout can live anywhere.

## Updates

`auren update` (`src/auren/update.py`, and the panel's Updates page) resolves GitHub's latest published
non-prerelease release, fetches that exact version tag, and installs its commit. Unreleased `main`
commits and standalone tags are ignored. It works from its own clone in `~/.cache/auren/source`,
except for a `--dev` install, whose clean `main` checkout can be fast-forwarded to the release.
An installed commit already ahead of a release is never downgraded. Release tags are fetched without
force, so a rewritten tag is refused. See [releases.md](releases.md) for authentication and publishing.

The update runs in a transient systemd unit (`systemd-run --user`, given the session's
PATH, XDG folders and git sign-in: SSH agent, `GH_TOKEN`), so stopping Auren or restarting the bar doesn't
stop it: wait until Auren is idle, stop `auren` and `auren-mascot` (if they won't stop, nothing changes),
run the new `install.sh` (with `AUREN_UPDATE=1`, so it leaves the services alone), then restart the ones
that were running and leave the others stopped. The panel's power buttons wait while it runs.

It never changes what it can't put back: before touching anything it makes sure the installed commit is
in its clone (fetching it from the checkout it came from if GitHub doesn't have it), and a failed install
(or one past 20 minutes, which stops everything it started) puts that commit back and installs it again;
a `--dev` checkout goes back with `git reset --keep`. One check or update at a time (`update.lock`); a
check doesn't replace the progress of a running update. If `omarchy-plugin/` or `mascot/` changed (the panel
imports the mascot's QML), `omarchy restart shell` loads the new panel. Progress goes to `$XDG_RUNTIME_DIR/auren/update.json`, the install's output to
`~/.local/state/auren/update.log`. `install.json` notes whether the checkout had uncommitted changes, and
the panel says an update replaces them.

## Further work

The original portability work is implemented: central paths, daemon locking, diagnostics, TOML
settings, the persistent tool runtime, optional integration detection, and model selection.
See [the roadmap](roadmap.md) for planned work and [the release guide](releases.md) for versioning.

The current brain adapters drive signed-in CLI processes. A direct API-key backend, external mascot
loading, a neutral plugin ID, stricter read-only permissions, and retention controls remain future
work. [Privacy](privacy.md) describes current data handling; [Safety](safety.md) documents the limits
of the action checks.
