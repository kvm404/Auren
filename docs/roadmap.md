# Roadmap

A voice-driven personal assistant for this Omarchy/Hyprland laptop: talk to it, it answers by voice,
and it can do tasks on the computer, including prompting coding agents in herdr panes. A mascot
overlay shows what it's doing.

## Pieces

| # | Piece | Status |
|---|---|---|
| 1 | **Voice (TTS):** Supertonic 3, voice F2, local | ✅ decided, [0001](decisions/0001-assistant-voice.md) |
| 2 | **Ears (STT):** AssemblyAI streaming 3.5 Pro + vocabulary; ElevenLabs backup | ✅ decided, [0002](decisions/0002-speech-to-text.md) |
| 3 | **Brain:** Claude Haiku 5.5 via Claude Code (Pro); GPT-6.1 Sol via Codex as backup (wired, automatic switch); Groq Qwen as last resort (not wired) | ✅ [0003](decisions/0003-brain.md) |
| 4 | **Hands (tools):** system profile, herdr, desktop and system, browser (Open Browser Use), routines ([hands.md](hands.md)); connected to the brain via MCP (`uv run auren chat`). Screen control (see the screen, mouse, keyboard) as a fallback | ✅ first version |
| 5 | **Safety:** approval levels for risky actions; screen and web text treated as untrusted | 🔨 basics done ([safety.md](safety.md)) |
| 6 | **Daemon:** press to talk (Super+A; Super+Shift+A for a fresh conversation), ears → brain → voice, interrupting; systemd user service ([daemon.md](daemon.md)) | ✅ first version |
| 7 | **Mascot overlay:** a character floating over the desktop: drag anywhere, hold to talk, scroll to resize; fades when idle, sleeps; live transcript, activity, captions and yes/no questions in a speech bubble; bounces to the mic and voice. Its own Quickshell instance, systemd user service ([mascot.md](mascot.md)). More mascots later | ✅ first version |
| 8 | **Bar plugin:** the mascot in pixels in the Omarchy bar showing Auren's state; panel with the live mascot (hold to talk), last exchange, switches (Auren, voice replies, mascot, sleep animation), brain (auto/Claude/Codex), actions (fresh, stop, restart, log) ([bar-plugin.md](bar-plugin.md)) | ✅ first version |
| 9 | **Dictation mode** (⏭ next): same ears, but the text is typed into the focused app (`wtype`) instead of going to the brain. Replaces a separate "new Voisu" app | planned |
| 10 | **More mascots:** Veyra (a lavender fox spirit) and Auren (a cobalt hover-bot), on a shared `MascotBase`; the first mascot, Clawd (the Claude Code mascot), was later removed and Auren became the default; picked on the bar panel's Mascot page, with a matching voice (Auren M3) ([mascot.md](mascot.md)). The panel became four pages, with voice tuning (speed, quality, volume, pause) | ✅ first version |

## Brain: what we know so far

- Layered so that expensive models only do expensive work:
  - **Layer 0:** direct commands ("volume up", "workspace 3") handled without an LLM.
  - **Layer 1:** a fast, cheap model with tool calling for most requests.
  - **Layer 2:** heavy work delegated to the user's existing agents (Claude Code, Codex, OpenCode)
    in herdr panes via `herdr agent prompt <name> "..." --wait`.
- The user is on Claude **Pro** (small limits, shared with daily coding), and also has Codex, OpenCode
  (free models) and the Antigravity CLI.
- Layer 1 also turns imperfect transcripts into intent, so it should get the vocabulary and system
  context (herdr panes, focused window, projects).
- Research sources: Artificial Analysis first, then a test on real requests, as with TTS and STT.
- Research done 2026-10-10: [research/brain.md](research/brain.md) (shortlist and test plan).

## Later

- Lecture-notes summary routine (the user will describe the lecture site and note format).
- Wire the ElevenLabs STT backup and the Groq last-resort brain.
- Alerts when a long agent job finishes; a spoken "stop" for running actions.
- Our own copy of Open Browser Use, so web-app windows can be read like browser tabs.
- Maybe move the mascot into the Omarchy shell process (saves ~78 MB).

## Notes

- Keys (the user's choice): **Super+A** press to start talking, press again to send; **Super+Shift+A**
  the same in a fresh conversation. No push-to-talk.
- The project was called parth until 2026-10-10, when it was renamed Auren.
