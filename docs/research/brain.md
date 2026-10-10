# Brain research (2026-10-10)

Research before the brain experiment. Nothing is decided yet. This picks what to test.

Sources:
- Artificial Analysis (AA), read in the user's browser: LLM leaderboard; AutomationBench-AA; "Time to First Answer Token" chart.
- Claude API docs (bundled skill).
- The user's own Groq and OpenCode accounts.
- Web search (third-party, marked as such).

## What layer 1 needs

Layer 1 is the model that handles every spoken request.

- **First word fast.** Voice-agent guides budget about 0.65–1 s for the model's first token. They all say to keep reasoning off for the live turn: thinking pushes first-token time from ~1 s to 8–300 s. We use push-to-talk on a desktop and the mascot shows "thinking", so ~1.5 s is acceptable. 10 s is not.
- **Reliable tool calls.** It must pick the right tool with the right arguments, and repair misheard words using our vocabulary.
- **Cheap at ~100 requests a day.**

## Findings

### 1. Thinking on means too slow; the question is how good each model is with thinking off

Time to first answer token, from AA (seconds, includes thinking):

| Model and mode | First answer token |
|---|---|
| Gemini 2.5 Flash-Lite / Flash (non-reasoning, older) | 0.3 / 0.4 |
| Claude Haiku 4.5 (non-reasoning, older) | 0.5 |
| GPT-6 Luna (non-reasoning) · GPT-5.6 Terra (non-reasoning) | 0.8 · 0.8 |
| **Claude Sonnet 5.5 (low)** | **0.9** |
| **DeepSeek V4.1 Flash (non-reasoning)** | **1.1** |
| GPT-5.6 Terra (low) · GPT-6 Luna (low) | 1.5 · 3.2 |
| Qwen3.8 27B (non-reasoning) | 3.8 (Groq hosts it faster) |
| DeepSeek V4.1 Flash (max) | 10.1 |
| **Claude Haiku 5.5: low · medium · max** | **10.4 · 14.4 · 306** |
| Gemini 3.8 Flash (high) · MiMo-V2.6-Flash · GLM-5.3-Flash | 27 · 35 · 38 |

**About Haiku 5.5:**
- AA only measured it with thinking on.
- The API allows `thinking: {type: "disabled"}` at effort high or below. At low effort with adaptive thinking, it "can skip thinking entirely on simpler requests".
- So its real voice-mode speed is **unmeasured**. We must test it.

### 2. Tool use: AutomationBench-AA

AutomationBench-AA is AA's current tool-use benchmark: long workflows across simulated Gmail, Slack, Jira and similar apps, with company policies to follow. τ²-bench and IFBench are now "legacy" on AA, and the newest models aren't scored on them.

| Model (mode) | Score | Note |
|---|---|---|
| DeepSeek V4.1 Flash (max) | 68.9% | slow mode |
| MiMo-V2.6-Flash | 64.1% | slow; free on OpenCode |
| GLM-5.3-Flash | 60.4% | slow as measured |
| Gemini 3.8 Flash (high) | 59.9% | slow |
| **Claude Sonnet 5.5 (low)** | **49.4%** | fast (0.9 s) |
| **DeepSeek V4.1 Flash (non-reasoning)** | **47.8%** | fast (1.1 s) |
| GPT-6 Luna (medium) | 40.5% | |
| Gemini 3.8 Flash (low) | 36.5% | latency not measured |
| Claude Haiku 5.5 (max / medium / low) | 35.4% / 28.6% / 22.9% | marked preliminary (*) by AA; thinking-off not measured |
| Qwen3.8 27B (low) | 30.1% | on Groq, free |
| GPT-5.6 Terra (low) | 29.1% | |
| Gemini 3.5 Flash-Lite | 25.0% | |
| GPT-5.6 Terra / Qwen3.8 27B (non-reasoning) | 12.5% / 12.4% | |
| GPT-6 Luna (low / non-reasoning) | 12.0% / 9.1% | |
| gpt-oss-120b (high) | 0.2% | Groq's other free model |

**How to read this:**
- AutomationBench is much harder than most voice commands, which usually need a single tool call ("open Firefox on workspace 2").
- Small gaps at the top won't matter for us.
- But scores under ~15% suggest unreliable tool calling.

**Speed and tool use together:**
- The only models that are both fast and above ~45% are **Claude Sonnet 5.5 (low)** and **DeepSeek V4.1 Flash (non-reasoning)**.
- Haiku 5.5 is the biggest unknown: it's cheap and has computer use built in, but its fast mode is untested.

### 3. Price and estimated monthly cost

Assumptions:
- 100 requests a day, about 2 model calls each (one tool round trip).
- About 4k tokens of context per call, ~80% of it served from the prompt cache.
- About 150 output tokens per call.
- That's roughly 24M input and 0.9M output tokens a month.

| Model | Input / output per M tokens (cache read) | ≈ per month |
|---|---|---|
| Claude Haiku 5.5 | $0.10 / $0.50 ($0.01) | **~$1–2**. Its tokenizer counts ~30% more tokens than older Claude models |
| GPT-6 Luna | $0.10 / $0.50 ($0.01) | ~$1–2, but needs an OpenAI API key (separate from the ChatGPT plan) |
| GLM-5.3-Flash | $0.15 / $0.50 ($0.03) | ~$2 |
| DeepSeek V4.1 Flash | $0.30 / $1.20 ($0.006); about half price off-peak | **~$3** |
| Gemini 3.8 Flash | $0.75 / $3.75 until end of 2026, then $1.50 / $7.50 | ~$8, later ~$17. A free tier may exist (unverified) |
| Claude Sonnet 5.5 | $2 / $10 ($0.20) | **~$20+**. Too much as the default; fine for occasional escalation |
| Groq: Qwen3.8 27B, gpt-oss | free | $0, but your account allows **8,000 tokens/min and 1,000 requests/day**: about 2 requests a minute at our context size |
| OpenCode Zen free models | free "for a limited time" | $0; see privacy below |

**OpenCode Zen** is pay-as-you-go. One key covers Haiku 5.5, DeepSeek V4.1 Flash, GLM-5.3-Flash, GPT-6 Luna and Gemini at the prices above. That's handy for testing many models with one key.

**Privacy on Zen's free models:**
- MiMo-V2.6-Flash, Ling and Big Pickle: "collected data may be used to improve the model".
- Nemotron: "do not submit personal or confidential data".
- Step 5 Preview, Space Bunny and LongCat: the provider "follows a zero-retention policy".

The assistant will see the screen, terminal and files, so data-training models are a poor fit for anything personal.

**DeepSeek's own API** runs on servers in China. Routing it through OpenCode Zen is the alternative to compare.

### 4. Subscription agents (layer 2, and the "no API key" option)

| Agent | Long-running mode | Limits | Fit |
|---|---|---|---|
| Claude Code (Pro) | `claude -p --input-format stream-json --output-format stream-json` (one process, JSON in and out) | One pool shared with claude.ai and your coding. Estimates: ~45–90 prompts per 5-hour window, plus a weekly cap (third-party figures) | Layer 2. As layer 1 it would eat your coding limits |
| Codex (ChatGPT plan) | `codex app-server` (JSON-RPC over stdio). It can **read your remaining rate limits**, so Auren could tell you how much is left | 5-hour and weekly limits shared with cloud tasks; numbers vary by source | Layer 2 |
| OpenCode | `opencode serve` (HTTP server with an OpenAPI spec at `/doc`) | Free models (above) or Zen pay-as-you-go | Layer 2, and a possible free layer 1 |
| Antigravity (`agy` 1.3.1 installed) | `agy -p`, `--headless --approve …` | Reports of `agy -p` hanging when not run in a terminal (older versions); free tier cut to ~20 agent requests a day (July 2026, third-party) | Optional layer-2 helper, not the brain |

All four can stay running, so there's no startup cost per request. They still run a full coding agent per request, though, and per-request delay through them is unmeasured.

### 5. Other things learned

- **Claude Haiku 5.5 and Sonnet 5.5 include `computer_toolset` and `browser_toolset`.** These matter for piece 4 (hands): the same model that understands you could also control the screen when needed.
- **Groq lost most of its chat models.** Only Qwen3.8 27B and gpt-oss-120b/20b remain on your account. The free Groq brain is therefore weaker than expected.
- **Voice pattern used by others:** speak a quick acknowledgment ("on it") immediately, then do slow work in the background. This hides layer-2 delays.

## What to test (proposed)

Same real requests for every setup: ~25 transcripts from the STT recordings and live sessions, plus the user's typical commands. Mock tools. For each, score the tool chosen, its arguments, recovery from misheard words, first-token time (median and p95) and cost.

| # | Setup | Why | Needs |
|---|---|---|---|
| 1 | **Claude Haiku 5.5**, thinking off, and adaptive at low effort | Cheapest strong candidate; fast mode unmeasured | Anthropic API key |
| 2 | **DeepSeek V4.1 Flash** (non-reasoning) | Best fast tool use per dollar on AA | Zen balance or a DeepSeek key |
| 3 | **Claude Sonnet 5.5** (low, `between_tools`) | Quality ceiling and escalation model | Anthropic API key |
| 4 | GLM-5.3-Flash, thinking off | Cheap, strong with thinking; fast mode unmeasured | Zen |
| 5 | Qwen3.8 27B on Groq | Free baseline | Groq key (have it) |
| 6 | Step 5 Preview free (zero retention) via `opencode serve` | Free, no new key, and tests the "server" route | Nothing |
| 7 | Claude Code kept running in JSON mode, with Haiku, on Pro | The "no API key" option, measured | Nothing (uses Pro) |

Likely architecture after the test: a fast model with thinking off handles every request, plus an `escalate` tool. Harder requests go to Sonnet 5.5 or a thinking-on call, and big jobs go to Claude Code or Codex in herdr (layer 2).
