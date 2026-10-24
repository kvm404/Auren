# 0003: Claude Code is the primary brain, with Codex as backup

Date: 2026-10-10 · Status: accepted

Current runtime: Claude Code uses the configurable `haiku` alias; Codex is the only wired backup.
Groq is experimental and is not used by the daemon.

Update 2026-10-21: the backup is now **GPT-6 Luna** (`gpt-6-luna`), effort low. Sol stays available
through `brain.codex_model` in `config.toml`. The comparison below is from the original decision.

## Decision

| Order | Brain | Runs through | Paid by | Used when |
|---|---|---|---|---|
| **1. Main** | **Claude Haiku 5.5**, thinking on; effort low by default, raised for harder requests | Claude Code kept running (`claude -p --input-format stream-json`) | Claude Pro | Normally |
| **2. Backup** | **GPT-6.1 Sol**, effort low | Codex (`codex exec` now; `codex app-server` later) | ChatGPT plan (mostly unused) | Claude's 5-hour or weekly limit is reached |
| **3. Last resort** | **Qwen3.8 27B**, reasoning off; asks before any action that changes the system | Groq API (existing key) | Free tier: 8k tokens/min, 1,000 requests/day | Both plans exhausted |

- **No Anthropic API key for now.** It would give the same Haiku quality, about 1–2 s faster. Revisit if Pro limits are hit often, or if a feature is needed that only the API offers.
- **The brain has one interface with switchable backends,** so moving to the API later is a configuration change.
- **Heavy work is not done by the brain.** Long, many-step jobs go to the coding agents in herdr (layer 2). The brain decides, starts them and reports back.

### Required settings for Claude Code as the brain

- `--system-prompt <ours>`, `--tools ""` (later: only Auren's own MCP tools), `--json-schema` or MCP tools, `--no-session-persistence`, `--strict-mcp-config`.
- **`--setting-sources local`, and a working folder outside `$HOME`** (`$XDG_RUNTIME_DIR/auren-brain`). Otherwise Claude Code loads the user's personal `~/.claude/CLAUDE.md` even when the system prompt is replaced, and the brain follows the user's own coding rules. It does that as user settings, and also as a project file: it looks for `.claude/CLAUDE.md` in every parent folder, and `$HOME` is a parent of anything under it. The brain test ran from `/tmp`, so it wasn't affected.
- `--bare` can't be used: it logs out of the subscription.
- Thinking: `--settings '{"alwaysThinkingEnabled": true}'` with `--effort low`; `false` turns it off. Environment variables such as `MAX_THINKING_TOKENS=0` don't work.
- A kept-running process grows by ~300 tokens per request, so it needs a periodic reset or compaction.

### Switching

1. On a usage-limit error from Claude Code, switch to Codex and say so ("Claude's limit is reached, using Codex for now").
2. If Codex is also out, switch to Groq in careful mode.
3. Return to Haiku when the limit resets.
4. Codex's app-server can report remaining usage, so the switch can happen before a request fails.

## Why

Tested on 45 of the user's own requests (`experiments/brain/`):
- the 20 recorded commands, the live-session request and 6 extras;
- 18 versions with the worst real speech-to-text mistakes.

Same instructions, tools and system snapshot for every setup. Every failure was read by hand.

| Setup | Correct | Misheard | Time per request |
|---|---|---|---|
| Claude Sonnet 5.5 (Claude Code) | 45/45 | 18/18 | 4.3 s |
| **GPT-6.1 Sol, low (Codex)** | **45/45** | **18/18** | 8.6 s |
| **Claude Haiku 5.5, thinking on (Claude Code)** | **45/45** | 17/18 | 3.9 s (≈1.5 s kept running) |
| Muse Spark 1.3, high (OpenCode free) | 45/45 | 17/18 | 10 s |
| Claude Haiku 5.5, thinking off | 45/45 | 16/18 | 4.1 s |
| Gemini 3.8 Flash (Antigravity) | 43/45 | 17/18 | 14 s |
| GPT-5.6 Terra, low / medium (Codex) | 42–43/45 | 17/18 | 7–9 s |
| Grok 4.7 build-fast | 43/45 | 15/18 | 11 s, invented actions |
| **Qwen3.8 27B (Groq)** | **41/45** | 15/18 | **0.6 s**, guesses on misheard words |
| GPT-6 Luna, low (Codex) | 41/45 | 15/18 | 6 s, ignored the system snapshot |

- **Speed matters less than correctness.** The user accepts a 4–5 s reply.
- **Haiku is the main brain** because it matches the best accuracy while using very little of the Pro allowance: about $0.001 per request at API prices.
- **Sonnet is slightly better on garbled speech** but costs about 20× more of the allowance.
- **Sol is the first backup:** equally accurate, a separate allowance, and Codex enforces the output format.
- **Groq is free and independent of both plans,** but careless enough that it must confirm before changing anything.

## Not chosen

| Option | Reason |
|---|---|
| Muse Spark 1.3 | Free tier lets Meta train on the prompts (the brain sees the screen and files); "free for a limited time"; ~10 s |
| Grok | Invented actions ("I opened Chrome and loaded Example Domain") |
| GPT-6 Luna | Launched Firefox, which isn't installed; claimed it couldn't change the volume |
| Gemini via Antigravity | 14–27 s; small free allowance |
| OpenCode free models in general | Only usable through OpenCode's default agent (~20k-token prompt); no enforced output format |

## Open items

- How much of the Pro allowance a day of real use takes: measure once the brain is running.
- Brain test with real tool calls (MCP) and multi-step follow-ups. This test covered only the first step.
- Optional: the same 45 cases on the Claude API if a key is ever bought.
