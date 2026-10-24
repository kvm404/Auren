# Privacy

Auren controls your signed-in desktop session. The microphone service and selected model provider
receive data as described below. Auren has no separate telemetry or analytics service.

## What leaves the laptop

| Destination | Data |
|---|---|
| AssemblyAI | Microphone audio while listening, plus up to 100 vocabulary terms drawn from built-ins, your custom words, bookmarks, app names, and project names |
| Claude Code or Codex's model provider | Your transcript or typed request, conversation context, a compact machine profile, personal instructions, routines, and results of tools the model calls |
| Selected model provider, when screen tools are used | Requested screenshots, including visible app and browser content |
| Claude Code's model provider, for the full-access send judge | The last six conversation lines, the proposed message and destination, and a screenshot for a screen-based send |
| GitHub | Release checks and source downloads; authentication uses `GH_TOKEN`, `GITHUB_TOKEN`, or an existing `gh` login when available |
| Voice model host | The Supertonic model download during setup or `auren voice-model` |

Browser actions use your real logged-in browser through Open Browser Use. Normal site requests,
downloads, and actions therefore use that browser's session. Coding tasks handed to herdr agents
follow those agents' own provider and permission settings.

Speech synthesis runs locally. Audio is streamed from PipeWire to AssemblyAI; the main runtime does
not save microphone recordings.

The machine profile can contain your username, hostname, project paths, installed applications,
aliases, key bindings, open-window titles, and coding-agent context. Common secret patterns are
redacted from profile output. Redaction is not a guarantee that arbitrary tool output, screenshots,
page content, or personal instructions contain no sensitive information.

Provider retention and training policies depend on your accounts and their terms. Auren does not
set a provider-side retention policy.

## Local storage and removal

Paths follow the XDG base directories. These defaults apply unless you override them:

| Path | Stored data |
|---|---|
| `~/.config/auren/` | API key file, settings, vocabulary, instructions, routines, and UI preferences |
| `~/.local/state/auren/history.jsonl` | Transcripts, replies, tool names, timings, and brain used |
| `~/.local/state/auren/daemon.log` | Activity, failures, questions, and diagnostic details |
| `~/.local/state/auren/jobs/` | Shell job output |
| `~/.local/state/auren/herdr-jobs.json` | Jobs Auren created in herdr |
| `~/.local/state/auren/update.log` | The most recent update's installation output |
| `~/.cache/auren/profile-static.json` | Cached machine profile |
| `~/.cache/auren/source/` | The updater's source checkout |
| `$XDG_RUNTIME_DIR/auren/` | Current state, captions, confirmations in process memory, sockets, and brain working files |

History and daemon/job logs have **no automatic expiry or rotation**. The panel reads recent history
to show the last exchange and daily counts. `auren-ctl forget` resets model conversation and pending
confirmations; it does not delete history, logs, routines, settings, or text already typed into an app.

To remove Auren's retained conversation and machine data, stop Auren and its mascot first, then delete
`~/.local/state/auren/` and `~/.cache/auren/`. Removing `~/.config/auren/` also deletes your API key,
routines, and settings. Stop any active update before removing its cache or runtime files. Delete the
runtime folder only after Auren, its mascot, and any updater have stopped.

Claude requests use `--no-session-persistence`; Codex requests use `--ephemeral`. These flags do not
remove Auren's own history files or control provider-side retention. Browser history, downloads,
clipboard-manager history, herdr sessions, and logs kept by other software are outside these folders.
