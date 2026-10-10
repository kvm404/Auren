# Daemon: talking to Auren

**Press Super+A, speak, press Super+A again** (Super+Shift+A starts a fresh conversation the same way). Auren listens with the ears, thinks with the brain (and its tools), and answers with the voice.

| Part | What | Code |
|---|---|---|
| Keys | In `~/.config/hypr/bindings.lua`: Super+A runs `auren-ctl talk` (start listening; again: stop and send), Super+Shift+A runs `auren-ctl talk-fresh` (the same, but the brain restarts with an empty conversation while you speak; it replaced the user's ChatGPT desktop binding). The user chose press-to-start/press-to-stop over holding (2026-10-10). Listening stops by itself after 2 minutes (`MAX_LISTEN`), since the speech service bills per second | `bin/auren-ctl` (socat to the socket) |
| Ears | AssemblyAI streaming, Universal-3.5 Pro, push-to-talk settings from decision 0002. The connection is open only while the key is held. The text is returned as soon as the final turn arrives after release (~0.3 s) | `src/auren/ears.py` |
| Vocabulary | 100 terms: core names (herdr, Voisu, Codex, Claude Code, Omarchy…), then Chrome bookmark names (MLP, Tools in Data Science…), web apps, recent projects, apps | `ears.vocabulary()`, `src/auren/data/vocab.txt` |
| Brain | `Brain`: Claude Haiku through Claude Code kept warm; Codex backup on a usage limit. One request at a time; later ones wait | `src/auren/brain/` |
| Voice | Supertonic 3, F2, 5 steps. Sentence by sentence into `pw-cat`, so the first sentence plays while the rest is generated | `src/auren/voice.py` |
| Interrupting | Pressing Super+A while Auren is speaking stops the voice and starts listening | |
| Voice choice | `auren-ctl voice-name F2` (also from the bar panel: F2, M3, F1, M1, the voices picked in decision 0001); saved in `~/.config/auren/settings.json`, says a short sample | `Voice.set_voice()` |
| Feedback | The mascot ([mascot.md](mascot.md)). When it isn't running, the Omarchy OSD shows "Listening…", "Thinking…", "Didn't catch that" | `daemon.osd()` |
| State | `$XDG_RUNTIME_DIR/auren/state.json`: `{state: starting, idle, listening, thinking, working, speaking, error or off; detail (live transcript, activity phrase or the sentence being spoken); since; pid; brain; transcript, tool, mouse, reply, awaiting + question + until}`, for the mascot and the bar plugin. `$XDG_RUNTIME_DIR/auren/level`: mic/voice loudness 0..1 | `src/auren/live.py` |
| Logs | `~/.local/state/auren/daemon.log`; every exchange in `~/.local/state/auren/history.jsonl` (transcript, reply, tools, timings, which brain) | |

## Fresh conversation

The brain keeps one conversation going, and starts a fresh one by itself past ~40k tokens. Saying just
"start fresh", "new conversation", "forget everything" (also "forget all that", "clear your memory",
"reset the conversation") or running `auren-ctl forget` starts a fresh one now. It doesn't go to the
brain: the daemon matches the phrase (`daemon.FORGET`), restarts the Claude process (warm, ~1 s) and
clears the Codex backup's recent exchanges. History, routines and settings stay. Tested: a word taught
before "start fresh" was unknown after it.

## Running it

It runs as a systemd user service that starts with the graphical session, like Voisu:

```bash
systemctl --user status auren          # is it running
journalctl --user -u auren -f          # watch it
systemctl --user restart auren         # after code changes
systemctl --user disable --now auren   # turn it off
```

The unit file is `systemd/auren.service`, linked into `~/.config/systemd/user/`.

Control it from a terminal:

```bash
bin/auren-ctl status
bin/auren-ctl ask "what time is it"    # typed request, spoken reply
bin/auren-ctl say "hello"
bin/auren-ctl cancel                    # stop listening or speaking
bin/auren-ctl forget                    # fresh conversation (same as saying "start fresh")
```

## Measured (2026-10-10)

- **Start-up:** 0.9–1.0 s (voice model plus brain process).
- **Typed request to reply text:** 1.7–1.9 s for questions the live snapshot answers; ~3–5 s with tool calls.
- **Spoken reply:** the first sentence starts ~0.3 s after the text is ready.
- **Memory:** the daemon uses ~520 MB (mostly the Supertonic models), plus the Claude Code process.

## Not yet

- The bar plugin (piece 8).
- Telling the user when a background job (a Codex agent in herdr) finishes.
- A wake word; dictation mode.
