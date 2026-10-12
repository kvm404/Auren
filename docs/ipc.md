# Daemon contract

How the daemon, `auren-ctl`, the mascot and the bar plugin talk. The UIs only read files and send
socket commands, so any of them can crash or restart without affecting the others. Change this page
together with the code.

## Runtime files (`$XDG_RUNTIME_DIR/auren/`)

| File | Writer | Content |
|---|---|---|
| `auren.sock` | daemon | Command socket (one line in, one line out; mode 600) |
| `state.json` | daemon | Current state, below (written whole, then renamed) |
| `level` | daemon | Loudness 0..1 of the mic or the voice, ~20 times a second |

### `state.json`

Always present:

| Field | Meaning |
|---|---|
| `state` | `starting`, `idle`, `listening`, `thinking`, `working`, `speaking`, `error` or `off` |
| `detail` | Text for the state: the live transcript, what Auren is doing, the sentence being spoken, an error |
| `since` | Unix time the state was written |
| `pid` | The daemon's process id (UIs check `/proc/<pid>` to tell a crashed daemon from a running one) |
| `brain` | Who answers right now: `claude` or `codex` |
| `mode` | Brain mode: `auto`, `claude` or `codex` |
| `effort` | Claude's effort |
| `voice` | Spoken replies on |
| `language` | Reply language: `en` or `hi` |
| `voiceName` | Supertonic voice (`F2`, `M3`, …) |
| `voices` | Voices available |
| `tts` | `{speed, steps, volume, pause}` |

Sometimes present:

| Field | When |
|---|---|
| `transcript` | thinking/working: what the user said |
| `tool`, `mouse` | working: the tool being run; `mouse` true for screen control |
| `fresh` | listening: a fresh conversation was asked for |
| `reply`, `silent` | speaking: the whole reply; `silent` when the voice is off (bubble only) |
| `awaiting`, `question`, `until` | idle: a yes/no question is open until `until` |

## Socket commands

Sent with `auren-ctl <command>`; the reply is one line.

| Command | Does |
|---|---|
| `talk` / `talk-fresh` | Start listening; again to send (fresh: a new conversation) |
| `start` / `stop` | Push to talk (press, release) |
| `cancel` | Stop listening or speaking |
| `ask <text>` | A typed request through the same pipeline |
| `say <text>` | Speak a sentence |
| `forget` | Fresh conversation |
| `voice on\|off\|toggle` | Spoken replies |
| `voice-name <name> [sample]` | Voice (says the sample) |
| `tts speed X steps N volume X pause X` / `tts reset` | Voice tuning |
| `brain auto\|claude\|codex` | Brain mode |
| `effort low\|medium\|high\|xhigh\|max` | Claude's effort (fresh conversation) |
| `language en\|hi` | Reply language |
| `status` | JSON: state, brain, mode, effort, language, voice |
| `quit` | Stop the daemon |

## Files

| Path | Writer | Content |
|---|---|---|
| `~/.config/auren/settings.json` | daemon | Voice, brain, effort, language, voice tuning |
| `~/.config/auren/mascot.json` | bar plugin (read by the mascot) | Mascot preferences |
| `~/.config/auren/secrets.env` | user | API keys (mode 600) |
| `~/.config/auren/routines/*.md` | user | Saved workflows for the brain |
| `~/.local/state/auren/mascot.json` | mascot | Its place and size |
| `~/.local/state/auren/history.jsonl` | daemon | Every exchange (read by the bar plugin) |
| `~/.local/state/auren/daemon.log` | daemon | Log |
| `~/.local/state/auren/herdr-jobs.json` | tools | herdr tabs Auren created |
| `~/.local/state/auren/confirmations.json`, `turn` | tools, daemon | Pending confirmations and the turn counter |
| `~/.cache/auren/profile-static.json` | profile | Cached machine facts |
