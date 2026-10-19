# Daemon contract

How the daemon, `auren-ctl`, the mascot and the bar plugin talk. The UIs only read files and send
socket commands, so any of them can crash or restart without affecting the others. Change this page
together with the code.

## Runtime files (`$XDG_RUNTIME_DIR/auren/`)

Without `XDG_RUNTIME_DIR`, a private `/tmp/auren-runtime-<uid>/auren/` is used. All paths come from
`src/auren/config.py` and follow `XDG_CONFIG_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and
`XDG_DATA_HOME`; the UIs compute the same paths.

| File | Writer | Content |
|---|---|---|
| `auren.sock` | daemon | Command socket (one line in, one line out; mode 600) |
| `state.json` | daemon | Current state, below (written whole, then renamed) |
| `level` | daemon | Loudness 0..1 of the mic or the voice, ~20 times a second |
| `tools.sock` | daemon | The tool runtime, for the brains' MCP adapter: one JSON line in (`session`, `tool`, `args`), one out (`content`). `auren chat` serves its own `tools-<pid>.sock` |
| `daemon.lock` | daemon | Held (flock) while a daemon runs, so a second one exits; holds its pid |
| `brain/` | brain | The brain's working folder (outside `$HOME` on purpose) |

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
| `voiceName` | Supertonic voice (`F2`, `M3`, …) |
| `voices` | Voices available |
| `tts` | `{speed, steps, volume, pause}` |
| `safety` | Approval level: `ask`, `task` or `free` ([safety.md](safety.md#approval-levels)) |

Sometimes present:

| Field | When |
|---|---|
| `transcript` | thinking/working: what the user said |
| `tool`, `mouse` | working: the tool being run; `mouse` true for screen control |
| `fresh` | listening: a fresh conversation was asked for |
| `reply`, `silent` | speaking: the whole reply; `silent` when the voice is off (bubble only) |
| `awaiting`, `question`, `until` | idle: yes/no question(s) are open until `until`. `question` is the text of every question one answer covers (several: a first line "N things, one answer:", then one line each starting with `• `; at most 6) |

## Socket commands

Sent with `auren-ctl <command>` (`~/.local/share/auren/bin/auren-ctl`, also linked into `~/.local/bin`);
the reply is one line. It exits 1 when the daemon isn't running.

| Command | Does |
|---|---|
| `talk` / `talk-fresh` | Start listening; again to send (fresh: a new conversation) |
| `start` / `stop` | Push to talk (press, release) |
| `cancel` | Stop listening, the answer in progress (brain and tools) or speaking |
| `ask <text>` | A typed request through the same pipeline |
| `say <text>` | Speak a sentence |
| `forget` | Fresh conversation |
| `voice on\|off\|toggle` | Spoken replies |
| `voice-name <name> [sample]` | Voice (says the sample) |
| `tts speed X steps N volume X pause X` / `tts reset` | Voice tuning |
| `brain auto\|claude\|codex` | Brain mode |
| `effort low\|medium\|high\|xhigh\|max` | Claude's effort (fresh conversation) |
| `safety ask\|task\|free` | How much Auren asks before risky actions (saved in `config.toml`) |
| `yes` / `no` | Answer the question(s) it is waiting on (the bubble's and the panel's buttons send these; bind them to keys if you like). The reply says how many were approved or dropped, or that nothing is waiting |
| `status` | JSON: state, brain, mode, effort, voice, safety |
| `reload` | Re-read `config.toml` and the vocabulary and apply what changed (`auren config` and `auren vocab` send it) |
| `quit` | Stop the daemon |

## Files

| Path | Writer | Content |
|---|---|---|
| `~/.config/auren/config.toml` | user, `auren config`, daemon | Settings: `[voice]` (on, name, tuning), `[brain]` (mode, effort), `[safety]` (level). The daemon rewrites only the values it changes, keeping comments. Replaces `settings.json`, which is moved in once and kept as `settings.json.bak` |
| `~/.config/auren/mascot.json` | bar plugin (read by the mascot) | Mascot preferences |
| `~/.config/auren/secrets.env` | user | API keys (mode 600) |
| `~/.config/auren/routines/*.md` | user | Saved workflows for the brain |
| `~/.config/auren/instructions.md` | user | Extra instructions for the brain (after the laptop facts) |
| `~/.config/auren/vocab.txt` | user | Extra words for speech recognition, one per line, most important first (`auren vocab`, the panel's Vocabulary page) |
| `~/.local/state/auren/mascot.json` | mascot | Its place and size |
| `~/.local/state/auren/history.jsonl` | daemon | Every exchange (read by the bar plugin) |
| `~/.local/state/auren/daemon.log` | daemon | Log |
| `~/.local/state/auren/herdr-jobs.json` | tools | herdr tabs Auren created |
| `~/.local/state/auren/jobs/*.log` | tools | Output of commands run without herdr |
| `~/.cache/auren/profile-static.json` | profile | Cached machine facts |
| `~/.local/share/auren/models/supertonic-3/` | `auren voice-model` | The voice model |
| `~/.local/share/auren/{venv,mascot,bar-plugin,bin}/` | `install.sh` | The installed program |
| `~/.local/share/auren/install.json` | `install.sh` | What's installed: version, commit, branch, remote, source, mode |
| `~/.cache/auren/source/` | `auren update` | Auren's own clone of the repo, what updates install from |
| `$XDG_RUNTIME_DIR/auren/update.json` | `auren update` | The last check or update: `state` (checking, current, available, updating, done, failed, error, unknown), `step`, `message`, `latest`, `commits`, `count`, `checked_at` (read by the bar plugin) |
| `~/.local/state/auren/update.log` | `auren update` | The last update's steps and install output |
| `~/.config/auren/panel.json` | bar plugin | The panel's own settings (`notifyUpdates`) |

## English-only replies

Auren always replies and synthesizes speech in English. The language selector, CLI flags
`--language` and `--male`, socket command `language`, and state/status field `language` have been
removed. Loading or saving settings removes the old top-level `language` key from `config.toml`,
keeping the other settings and their comments. Legacy `settings.json` migration ignores that key
and keeps the original file as `settings.json.bak`.
