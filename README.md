# Auren

A voice assistant for the Omarchy desktop. Press **Super+A**, say what you want, and press it again.
Auren acts on your computer and answers out loud. A small mascot shows when it is listening,
thinking, working, or waiting for your answer.

<img src="docs/images/bar-panel.png" alt="Auren's controls in the Omarchy bar" width="340" align="right">

Ask it to open an app on another workspace, control your music, download something in your signed-in
browser, draw a diagram in Excalidraw, or give a coding agent a task in herdr. When an app has no
suitable command or browser interface, Auren can look at the screen and use the mouse and keyboard.

Auren is an early project built for **Omarchy, Hyprland, Wayland, and PipeWire**.
Other Linux desktops are not supported yet.

- **Speech recognition:** AssemblyAI Universal-3.5 Pro streaming, with your own vocabulary.
- **Brain:** Claude Code, using `haiku` by default. Codex, using `gpt-6-luna`, can take over when
  Claude hits a usage limit. Models and effort are configurable.
- **Voice:** Supertonic 3 runs locally on the CPU. Replies are in English.
- **Desktop:** apps, windows, workspaces, media, volume, reminders, screenshots, and shell jobs.
- **Optional integrations:** herdr for coding agents, Open Browser Use for browser tasks, and
  `/dev/uinput` access for mouse control and Excalidraw drawings.
- **Mascots:** Auren, a hover-bot, and Veyra, a fox spirit. Both have captions, voice reactions,
  drag-to-move, sleep animations, and confirmation buttons.

<br clear="right">

![Auren and Veyra in each mood](docs/images/mascots.png)

## Install

You need an Omarchy desktop, `git`, `uv`, Quickshell (`qs`), PipeWire's `pw-record` and `pw-cat`,
`wtype`, `wl-clipboard`, and `grim`. At least one brain CLI, **Claude Code or Codex**, must be installed
and signed in. You also need an **AssemblyAI API key** for voice input.

### Install with an agent

Paste this prompt into a coding agent running on your Omarchy machine:

```text
Install and set up Auren from https://github.com/kvm404/Auren on this machine.

Read the repository's README.md and docs/install.md first, along with any local
agent instructions for Omarchy configuration. Use the latest published release,
excluding drafts and prereleases. Check out its exact tag. If no release is
available, tell me rather than installing main. Use a normal install, not --dev.

Check dependencies and install what's missing. Detect my installed Claude Code
and Codex CLIs, help me complete sign-in if needed, and configure the brain to use
an available one. Run ./install.sh and help me add my AssemblyAI API key directly
to the local secrets.env file with mode 600. Never ask me to paste the key into
this conversation or print it in command output.

Back up config files before editing them. Add the talk and fresh-conversation
bindings without overwriting existing shortcuts; ask me to choose alternatives
if they conflict. Keep the default ask approval level. Offer herdr, browser, and
mouse-control integrations as optional setup choices, and leave unavailable
integrations disabled or on auto.

Run auren doctor, resolve setup problems, and restart Auren when idle. Tell me
what changed, anything I still need to do, and how to try my first voice request.
```

### Manual installation

Install the first release:

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/kvm404/Auren.git auren
cd auren
./install.sh
```

The installer copies Auren into `~/.local/share/auren/`, downloads the voice model, installs systemd
user services, and enables the Omarchy bar plugin. It prints key bindings for you to add and runs
`auren doctor`. The checkout can move or be removed after a normal install.

1. Add `ASSEMBLYAI_API_KEY=your-key` to `~/.config/auren/secrets.env`. The installer creates this file
   with mode `600`.
2. Add the printed bindings to `~/.config/hypr/bindings.lua`, resolving any existing bindings on those keys.
3. If you only use Codex, select it with `auren config set brain.mode codex`.
4. Run `systemctl --user restart auren auren-mascot`, then `auren doctor`.

For the development checkout and editable installs, see [Contributing](CONTRIBUTING.md).
The [installation guide](docs/install.md) covers optional integrations, diagnostics, and removal.

## Using it

| Control | Action |
|---|---|
| Super+A | Start listening; press again to send. While thinking or working, stop. While speaking, interrupt and start listening |
| Super+Shift+A | Start listening in a fresh conversation. While thinking or working, stop |
| Click the mascot | Start listening or send; while thinking, working, or speaking, stop |
| Drag / scroll / right-click the mascot | Move / resize / poke |
| Bar icon | Open Home, Mascot, Voice, Brain, Vocabulary, or Updates. Right-click to start a fresh conversation |
| `auren-ctl ask "what time is it"` | Send a typed request to the daemon |
| `auren chat` | Have a separate terminal conversation and inspect tool calls |
| `auren-ctl cancel` | Stop the current request |
| `auren-ctl forget` | Clear the current conversation, keeping settings and saved routines |
| `auren doctor` | Check dependencies and show fixes |

Use the panel to choose a mascot, adjust the voice, select the brain and its effort, manage vocabulary,
and install updates. Say "start fresh" to reset the conversation by voice.

## Settings and routines

Settings live in `~/.config/auren/config.toml`. The panel and CLI preserve comments when changing them.

```bash
auren config                          # show settings and defaults
auren config set brain.mode codex
auren config set voice.speed 1.1
auren config set integrations.browser false
auren vocab add "Your Project"
```

Put personal instructions in `~/.config/auren/instructions.md` and repeatable workflows in
`~/.config/auren/routines/*.md`. Auren detects installed apps, the default browser, coding agents,
and common project folders. Use `auren config` to override detection.

## Permissions and privacy

The default approval level, `ask`, asks before actions classified as risky. `task` lets one approval
cover further actions of the same kind in that task. `free` allows file and window actions without
asking. Publishing, root/system changes, secrets, purchases, disk wipes, and changes to Auren's own
controls still ask at every level.

At `free`, a separate Claude judge can allow a private or group chat message you explicitly requested,
without asking again. If it cannot verify the request, it asks. This judge also needs Claude Code;
without it, those sends ask for confirmation. These checks reduce mistakes; **they are not a sandbox**.
Read [Safety](docs/safety.md) before choosing a more permissive level.

Microphone audio goes to AssemblyAI while listening. Your transcript, machine context, tool results,
and any requested screenshots can go to the selected model provider. Speech synthesis stays local.
Conversation history and logs are stored locally without automatic expiry. "Forget" clears the model
conversation, not those files. See [Privacy](docs/privacy.md) for the data flow and removal instructions.

## Updates

The panel's Updates page and `auren update` install the **latest published GitHub release**.
Commits on `main`, tags without releases, drafts, and prereleases do not trigger updates.

```bash
auren update                 # check, review changes, ask, and install
auren update check           # check only
auren update status          # last result
```

An update waits until Auren is idle, restarts it, and restores the previous version if installation
fails. A changed mascot or bar plugin also restarts the Omarchy shell. A development install can
fast-forward a clean `main` checkout to the release commit; it never downgrades a checkout already
ahead of that release. See [Updates and releases](docs/releases.md).

## How it works

```text
Super+A → daemon → AssemblyAI → Claude Code / Codex → local Supertonic → speech
             │                         │ MCP
             │                         ▼
             │                  persistent tool runtime
             │                  desktop · browser · screen · herdr · shell
             ▼
       state.json + level → Quickshell mascot · Omarchy bar plugin
```

The daemon owns tool state and confirmations across model calls. The mascot and panel read its state
files and send socket commands, so they can restart independently.

| Path | Contents |
|---|---|
| `src/auren/` | Daemon, speech, brain, tool runtime, safety, settings, updates, and system profile |
| `src/auren/hands/` | Desktop, browser, screen, clipboard, Excalidraw, herdr, and shell implementations |
| `mascot/` | Quickshell overlay and both mascot characters |
| `omarchy-plugin/` | Bar widget and controls |
| `tests/` | Python tests and Qt interaction tests |
| `experiments/` | Historical speech and model comparisons, separate from the runtime |

## Contributing and documentation

Start with [Contributing](CONTRIBUTING.md) and the [documentation index](docs/README.md).
[The roadmap](docs/roadmap.md) lists what works and what is planned.
[The changelog](CHANGELOG.md) describes releases.

Bug reports should include your Omarchy version, `auren doctor` output, and steps to reproduce.
Remove personal paths, transcripts, and credentials before sharing logs.

## License and credits

Auren's code and mascots are [MIT licensed](LICENSE). Dependencies, models, and services have their own
licenses and terms.

Built with [Omarchy](https://omarchy.org/), [Quickshell](https://quickshell.org/),
[Supertonic](https://github.com/supertone-inc/supertonic), AssemblyAI, and Open Browser Use.
