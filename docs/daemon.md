# Daemon: talking to Auren

**Press Super+A, speak, press Super+A again** (Super+Shift+A starts a fresh conversation the same way). Auren listens with the ears, thinks with the brain (and its tools), and answers with the voice.

| Part | What | Code |
|---|---|---|
| Keys | In `~/.config/hypr/bindings.lua`: Super+A runs `auren-ctl talk` (start listening; again: stop and send), Super+Shift+A runs `auren-ctl talk-fresh` (the same, but the brain restarts with an empty conversation while you speak; it replaced the user's ChatGPT desktop binding). The user chose press-to-start/press-to-stop over holding (2026-10-10). Listening stops by itself after 2 minutes (`MAX_LISTEN`), since the speech service bills per second | `auren-ctl` (`src/auren/ctl.py`) |
| Ears | AssemblyAI streaming, Universal-3.5 Pro, push-to-talk settings from decision 0002. The connection is open only while the key is held. The text is returned as soon as the final turn arrives after release (~0.3 s) | `src/auren/ears.py` |
| Vocabulary | 100 terms: core names (herdr, Voisu, Codex, Claude Code, Omarchy…), then Chrome bookmark names (MLP, Tools in Data Science…), web apps, recent projects, apps | `ears.vocabulary()`, `src/auren/data/vocab.txt` |
| Your own words | `auren vocab add <word>` / `remove <word>` / `list` (also the bar panel's Vocabulary page) keep them in `~/.config/auren/vocab.txt`, ahead of the built-in list. A word the built-in list or yours already has is refused (case and spacing ignored), and so is one past the limit: 100 less the number of built-in words (`vocab.capacity()`, so it follows the built-in list), shown in the panel as "N of M". The daemon is told to `reload`, so a new word is used from the next press, with no restart | `src/auren/vocab.py`, `Daemon._reload()` |
| Brain | `Brain`: Claude Haiku through Claude Code kept warm; Codex backup on a usage limit. One request at a time; later ones wait. Only the brain the mode uses runs: in `codex` mode no Claude process is started, and switching modes starts or stops it between answers | `src/auren/brain/`, `Brain.warm()` |
| Models | `brain.claude_model` (Claude Code's `--model`, default `haiku`), `brain.codex_model` (default `gpt-6-luna`) and `brain.codex_effort` (default `low`) in `config.toml`. A new Claude model restarts its process (fresh conversation); Codex's apply from the next request. `auren chat --model` overrides Claude's for that chat | `Brain.set_claude()`, `Brain.set_codex()` |
| Voice | Supertonic 3, F2, 5 steps, speed 1.05. Sentence by sentence into `pw-cat`, so the first sentence plays while the rest is generated | `src/auren/voice.py` |
| Interrupting | Pressing Super+A while Auren is speaking stops the voice and starts listening. While it thinks or works, Super+A (or Super+Shift+A) stops it instead, keeping the conversation ([safety.md](safety.md#stopping)) | `Daemon.command()` |
| Voice choice | `auren-ctl voice-name F2 [sample]` (also from the bar panel: F2, M3, F1, M1, the voices picked in decision 0001); saved in `~/.config/auren/config.toml`, says the sample (or "Hi, this is voice F2") when idle. Picking a mascot in the panel uses this to switch to its voice and say hello | `Voice.set_voice()` |
| Claude's effort | `auren-ctl effort low\|medium\|high\|xhigh\|max` (also the panel's Brain page). Claude Code's `--effort` is fixed when its process starts, so this restarts the brain (warm, ~1 s), which starts a fresh conversation; it waits for an answer in progress. Saved in `config.toml`, shown in `state.json` as `effort`. Low is decision 0003's default | `Brain.set_claude()`, `Daemon._set_claude()` |
| Approval level | `auren-ctl safety ask\|task\|free` (also the panel's Brain page, or `safety.level` in `config.toml`): how much it asks before risky actions, see [safety.md](safety.md#approval-levels). Takes effect at once and ends what an earlier yes covered; shown in `state.json` as `safety` | `Daemon.command()`, `Runtime.set_level()` |
| Answering | A plain yes or no right after it asked is read by the daemon itself and runs what was asked without the brain; anything more goes to the brain. `auren-ctl yes\|no` (and the bubble's buttons) answer whatever the bubble shows ([safety.md](safety.md#who-reads-the-answer)) | `Daemon.respond()`, `Daemon._answer()` |
| Voice tuning | `auren-ctl tts speed 1.1 steps 5 volume 0.8 pause 0.2` (any of them, in any order) or `tts reset`. Ranges: speed 0.7–1.6, steps 2–12 (quality; 8 costs ~45% more CPU than 5), volume 0.1–1 (scales the samples, so it's relative to the system volume), pause 0–1 s of silence between sentences. Saved in `config.toml`, shown in `state.json` as `tts`; the panel's Voice page sets them | `voice.tuning()`, `Voice.tune()`, `Daemon._tts()` |
| Feedback | The mascot ([mascot.md](mascot.md)). Omarchy's OSD shows short statuses with an icon each. Each action (clicking, dragging, typing, opening an app...; not quick reads, screenshots, or what shows its own feedback such as volume) always shows there, even with the mascot's bubble up. The other statuses (listening, thinking, didn't catch that, stopped) show only when the mascot isn't on screen (not running, hidden, or its bubble off). Replies (with the voice off) and errors come as notifications, to read in full. Auren closes its own OSD note before a screenshot, so the brain doesn't see it | `src/auren/notice.py`, `live.notice_kind()` |
| State | `$XDG_RUNTIME_DIR/auren/state.json`: `{state: starting, idle, listening, thinking, working, speaking, error or off; detail (live transcript, activity phrase or the sentence being spoken); since; pid; brain; mode; voice, voiceName, voices, tts; transcript, tool, mouse, reply, awaiting + question + until}`, for the mascot and the bar plugin. `$XDG_RUNTIME_DIR/auren/level`: mic/voice loudness 0..1 | `src/auren/live.py` |
| Logs | `~/.local/state/auren/daemon.log`; every exchange in `~/.local/state/auren/history.jsonl` (transcript, reply, tools, timings, which brain) | |

## Fresh conversation

The brain keeps one conversation going, and starts a fresh one by itself past ~40k tokens. Saying just
"start fresh", "new conversation", "forget everything" (also "forget all that", "clear your memory",
"reset the conversation") or running `auren-ctl forget` starts a fresh one now. It doesn't go to the
brain: the daemon matches the phrase (`daemon.FORGET`), restarts the Claude process (warm, ~1 s; not in codex mode) and
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
auren-ctl status
auren-ctl ask "what time is it"    # typed request, spoken reply
auren-ctl say "hello"
auren-ctl cancel                    # stop listening, thinking, working or speaking
auren-ctl forget                    # fresh conversation (same as saying "start fresh")
auren-ctl tts speed 1.1 pause 0.2   # how the voice speaks; tts reset for the defaults
auren-ctl effort high               # Claude thinks harder (and slower); starts a fresh conversation
auren-ctl safety task               # ask once per task; ask | task | free
auren-ctl yes                       # answer what it is asking (no: drop it)
```

## Measured (2026-10-10)

- **Start-up:** 0.9–1.0 s (voice model plus brain process).
- **Typed request to reply text:** 1.7–1.9 s for questions the live snapshot answers; ~3–5 s with tool calls.
- **Spoken reply:** the first sentence starts ~0.3 s after the text is ready.
- **Memory:** the daemon uses ~520 MB (mostly the Supertonic models), plus the Claude Code process.

## Not yet

- Telling the user when a background job (a Codex agent in herdr) finishes.
- A wake word; dictation mode.
