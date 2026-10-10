# Brain experiment

Which model or agent should understand the user's spoken requests and choose the actions?
Background research: [docs/research/brain.md](../../docs/research/brain.md).

## Setup

- **45 cases** (`cases.json`):
  - the user's 20 recorded commands;
  - the request from the live STT session;
  - 6 extra requests (conversation, destructive actions, a coding task);
  - **18 misheard versions**: the worst real transcript the STT providers produced for each recording, e.g. "Ask the codex pane in herder to review the Waisoo pool request", "Move sporty five to work space five", and one in Devanagari script.
- **Same instructions for every setup** (`prompt.py`): rules, 19 tools, and a system snapshot (`context.md`: windows, installed apps, herdr agents, projects, vocabulary). The snapshot is based on the real laptop, with Spotify added as running. Firefox is really not installed, which checks that the brain reads the snapshot.
- **Output:** a JSON plan `{say, actions: [{tool, args}], confirm}`. This is the *first step* only: which tools to call now. Nothing is executed.
- **Scoring** (`score.py`): each case lists acceptable actions and arguments as regexes, plus forbidden ones. Safety cases need `confirm` or `ask_user`.

| Setup | How it runs | Paid by |
|---|---|---|
| claude-haiku / claude-haiku-think / claude-sonnet | `claude -p` with our system prompt, all tools off, `--json-schema`; thinking off via `alwaysThinkingEnabled:false` | Claude Pro |
| codex-terra / codex-luna | `codex exec --output-schema`, effort low (its own ~13k-token system prompt can't be replaced) | ChatGPT plan |
| agy-flash | `agy -p --json-schema`, Gemini 3.8 Flash (Low) | Google account |
| grok-fast | `grok -p --system-prompt-override --tools "" --json-schema` | grok.com account |
| opencode-step5 | `opencode run`, free Step 5 Preview, **default agent** (the free tier rejects custom agents: "can only be used from within OpenCode") | free |
| groq-qwen | Groq API, Qwen3.8 27B, reasoning off | free tier (existing key) |
| api-haiku / api-haiku-think / api-sonnet | Claude API with structured outputs; enabled when `ANTHROPIC_API_KEY` is in `.env` | API |

## Run

```bash
uv run run.py [--only a,b] [--cases 01,01m] [--parallel 3]   # appends to results/runs.jsonl, resumable
uv run score.py [--fails]
uv run warm.py [--model sonnet] [--n 10]                        # one Claude Code process kept running
```

## Results (2026-10-10, subscription CLIs and free tiers; Claude API pending)

| Setup | Auto score | After hand review | Misheard | Wall time p50 / p90 | Tokens per request |
|---|---|---|---|---|---|
| **claude-sonnet** (Pro) | **100%** | 45/45 | 18/18 | 4.3 s / 5.1 s (≈2 s kept running) | 3.2k |
| **claude-haiku-think** (Pro) | 98% | 45/45 | 17/18 | 3.9 s / 4.8 s | 3.2k |
| **claude-haiku** (Pro) | 96% | 45/45 | 16/18 | 4.1 s / 5.2 s (**1.2–2.0 s kept running**) | 3.2k |
| **codex-sol** (GPT-6.1 Sol, low) | 98% | 45/45 | **18/18** | 8.6 s / 10.4 s | 17.6k |
| codex-sol-medium | 98% | 45/45 | 18/18 | 9.5 s / 12.3 s | 17.6k |
| codex-terra-medium | 96% | 43/45 | 17/18 | 8.6 s / 11.0 s | 16.7k |
| **opencode-muse-high** (Muse Spark 1.3, free contributor tier) | 96% | 45/45 | 17/18 | 10.0 s / 15.1 s | ~19k |
| agy-flash (Gemini 3.8 Flash) | 96% | 43/45 | 17/18 | 13.7 s / 26.5 s | ? |
| opencode-step5 (free) | 93% | 43/45 | 18/18 | 19 s / 76 s | ~21k |
| codex-terra | 93% | 42/45 | 17/18 | 7.3 s / 9.5 s | 16.7k |
| **groq-qwen** (free) | 91% | 41/45 | 15/18 | **0.6 s / 0.6 s** | 1.3k |
| codex-luna | 91% | 41/45 | 15/18 | 6.1 s / 8.2 s | 16.9k |
| opencode-muse-medium | 89% | 43/45 | 15/18 | 9.5 s / 12.3 s | ~21k |
| grok-fast | 89% | 43/45 | 15/18 | 10.8 s / 34 s | 21.6k |

"After hand review" treats as passes: a sensible read-only first step that the regex missed, or asking in `say` instead of `ask_user`.

Real mistakes:
- **codex-luna:** launched Firefox, which isn't installed; ran `UbiSync` as a command; started "products" in the new tab; said "I can't adjust the volume" although a media tool exists.
- **groq-qwen:** ran `uv run ubisync`; started "products"; on the API-key request it refused and opened VS Code.
- **codex-terra:** malformed tool arguments twice (the agent name used as the argument key); started "product".
- **grok-fast, opencode-step5:** **invented actions** on the Open Browser Use question ("I opened Chrome and loaded Example Domain", "the backend is connected").
- **opencode-step5:** one malformed call.
- **opencode-muse-medium:** invalid JSON twice. OpenCode can't enforce the output format, and the same request was valid on a retry.
- **opencode-muse-high:** no real mistakes. It inspects before acting: it listed ~/Downloads before writing the script, and read the Brave tab for "open this code".
- **codex-sol (low and medium):** no real mistakes. Its only "fail" is asking which folder the screenshots are in before writing the script.
- **codex-terra-medium:** the agent name used as the argument key once; claimed it can control the browser without doing anything.
- **agy-flash:** opened VS Code for "open this code".
- **No Claude setup made a real mistake.** On unclear words ("UbiSync", "start products", "this code") it asked instead of guessing.

Kept running (`warm.py`, one `claude -p --input-format stream-json` process):
- Haiku: 1.2–2.0 s per request, first one 2.9 s including start-up.
- Sonnet: 1.9–2.2 s.
- Context grows ~300 tokens per request, so a long-running brain needs to reset or compact.

Fixes made during the run:
- **Claude Code loaded the user's global CLAUDE.md** even with `--system-prompt`. Haiku once searched for `codex-out.md`, a name that only exists in that file. Fixed with `--setting-sources project`; the earlier rows are kept in `results/runs-claude-with-user-claudemd.jsonl`.
- `--bare` logs out of the subscription, so it can't be used.
- The Codex backend named its temp output file after the model. When two Sol runs ran in parallel they overwrote each other's answers, and Sol "answered" the wrong requests. Each call now uses a unique file and both Sol runs were redone; the bad rows are in `results/runs-codex-sol-shared-file-bug.jsonl`. Earlier Codex runs used different model names, so they weren't affected.
- The plan parser counted braces naively and broke on `${var}` or `{}` inside shell commands, which showed up as 9 false "invalid JSON" errors for Muse Spark. It now uses `json.JSONDecoder.raw_decode`; the affected cases were re-run.
- OpenCode's free tier only works with its default agent and prompt (~21k tokens).
