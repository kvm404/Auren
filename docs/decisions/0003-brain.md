# 0003: Claude Code is the primary brain, with Codex as backup

Date: 2026-10-10 · Status: accepted

Auren keeps a Claude Code process running between requests to avoid repeated startup costs and
preserve conversation context. Codex provides a separate backend when Claude reaches a usage limit.
Both use the same persistent tool runtime, so browser tabs, screenshots, and confirmations survive
a backend switch.

## Current configuration

| Backend | Default model | Process |
|---|---|---|
| Claude Code | `haiku`, low effort, thinking enabled | Persistent `claude -p` with stream-JSON input and output |
| Codex | `gpt-6-luna`, low effort | A new `codex exec` process per request, with the last four exchanges |

Models and effort are configurable. `auto` selects Claude with usage-limit fallback; `claude` and
`codex` restrict the main brain to one backend. The separate full-access send judge uses Claude even
when Codex is selected for the main brain.

Long coding and research tasks are delegated to agents in herdr when that integration is available.
Groq and other models were evaluated but are not runtime backends. A direct API-key backend is
future work, not an existing configuration switch.

## Conversation and tool ownership

- The main Claude process gets Auren's system prompt, its MCP adapter, and no built-in action tools.
- Its working folder is `$XDG_RUNTIME_DIR/auren/brain`, with `--setting-sources local`, to keep personal
  coding instructions out of the assistant's conversation in a normal desktop session.
- Claude starts a fresh conversation after 40,000 context tokens. A user-requested fresh conversation
  also clears Codex's recent exchanges and pending confirmations.
- `--no-session-persistence` for Claude and `--ephemeral` for Codex limit CLI conversation persistence;
  Auren maintains its own local history as described in [Privacy](../privacy.md).
- The caller counts user turns. Model adapters forward tool calls to the runtime rather than owning
  their own confirmations or desktop state.

## Fallback

In `auto` mode, a Claude usage limit hands the request to Codex only if no non-repeatable action has
run. After an action, Auren stops and explains the interruption rather than restarting the request
and risking duplicate actions. Claude is retried after 30 minutes.

Fallback is specific to usage-limit handling; arbitrary errors are reported. If Codex is unavailable
or also fails, Auren reports the failure. It does not silently choose a third provider.

Implementation: `src/auren/brain/`, `src/auren/tools.py`. See [Safety](../safety.md) for action checks.
