"""Brain backends: each takes a transcript and returns the JSON plan plus timings.

Subscription CLIs run one process per request (cold start included in wall_s).
model_s is the model's own time when the CLI reports it, so the CLI start-up
overhead is wall_s - model_s.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import httpx

import prompt

HERE = Path(__file__).parent
WORK = HERE / "work"
WORK.mkdir(exist_ok=True)
SCHEMA_FILE = WORK / "schema.json"
SCHEMA_FILE.write_text(prompt.schema_json())
EMPTY_DIR = Path(tempfile.mkdtemp(prefix="brain-"))   # no project files for agents to pick up


def _run(cmd: list[str], stdin: str | None = None, cwd: Path = EMPTY_DIR, timeout: int = 150,
         env: dict | None = None) -> tuple[str, str, float]:
    start = time.perf_counter()
    p = subprocess.run(cmd, input=stdin if stdin is not None else "", capture_output=True, text=True,
                       cwd=cwd, timeout=timeout, env={**os.environ, **(env or {})})
    wall = time.perf_counter() - start
    if p.returncode != 0:
        raise RuntimeError(f"exit {p.returncode}: {(p.stderr or p.stdout)[-400:]}")
    return p.stdout, p.stderr, wall


def parse_plan(text: str) -> dict:
    """Pull the JSON plan out of a reply that may wrap it in a code fence or prose."""
    decoder = json.JSONDecoder()
    for start in [m.start() for m in re.finditer(r"\{", text)]:
        try:
            obj, _ = decoder.raw_decode(text, start)   # handles braces inside strings
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "actions" in obj:
            return obj
    raise ValueError("no JSON plan in reply: " + text[:300])


# --- Claude Code (Pro subscription) ------------------------------------------

def claude_code(model: str, think: bool, effort: str = "low"):
    def run(transcript: str) -> dict:
        settings = json.dumps({"alwaysThinkingEnabled": think})
        out, _, wall = _run(["claude", "-p", prompt.user_message(transcript), "--model", model,
                             "--settings", settings, "--effort", effort,
                             "--system-prompt", prompt.system_prompt(), "--tools", "",
                             "--json-schema", prompt.schema_json(), "--output-format", "json",
                             "--no-session-persistence", "--strict-mcp-config",
                             "--setting-sources", "project"])
        d = json.loads(out)
        if d.get("is_error"):
            raise RuntimeError(str(d.get("result"))[:300])
        u = d["usage"]
        return {"plan": d.get("structured_output") or parse_plan(d["result"]), "wall_s": wall,
                "model_s": d["duration_api_ms"] / 1000, "first_token_s": (d.get("ttft_ms") or 0) / 1000 or None,
                "tokens_in": u["input_tokens"] + u["cache_read_input_tokens"] + u["cache_creation_input_tokens"],
                "tokens_out": u["output_tokens"],
                "thinking_tokens": u.get("output_tokens_details", {}).get("thinking_tokens")}
    return run


# --- Codex (ChatGPT plan) ------------------------------------------------------

def codex(model: str, effort: str = "low"):
    def run(transcript: str) -> dict:
        last = WORK / f"codex-last-{uuid.uuid4().hex}.json"   # unique: parallel runs must not share it
        out, _, wall = _run(["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
                             "-m", model, "-c", f"model_reasoning_effort={effort}",
                             "--output-schema", str(SCHEMA_FILE), "--output-last-message", str(last),
                             "--json", "-"], stdin=prompt.full_prompt(transcript))
        usage = {}
        for line in out.splitlines():
            if '"turn.completed"' in line:
                usage = json.loads(line).get("usage", {})
        reply = last.read_text()
        last.unlink()
        return {"plan": parse_plan(reply), "wall_s": wall, "model_s": None,
                "tokens_in": usage.get("input_tokens"), "tokens_out": usage.get("output_tokens"),
                "thinking_tokens": usage.get("reasoning_output_tokens")}
    return run


# --- Antigravity (Google account) ---------------------------------------------

def antigravity(model: str):
    def run(transcript: str) -> dict:
        out, _, wall = _run(["agy", "-p", prompt.full_prompt(transcript), "--model", model,
                             "--json-schema", str(SCHEMA_FILE), "--output-format", "json",
                             "--print-timeout", "140s"])
        d = json.loads(out)
        if d.get("status") != "SUCCESS":
            raise RuntimeError(str(d)[:300])
        return {"plan": d.get("structured_output") or parse_plan(d["response"]), "wall_s": wall,
                "model_s": d.get("duration_seconds"), "tokens_in": None, "tokens_out": None}
    return run


# --- Grok (grok.com account) -------------------------------------------------------

def grok(model: str):
    def run(transcript: str) -> dict:
        out, _, wall = _run(["grok", "-p", prompt.user_message(transcript), "--model", model,
                             "--system-prompt-override", prompt.system_prompt(), "--tools", "",
                             "--json-schema", prompt.schema_json(), "--output-format", "json",
                             "--disable-web-search"])
        d = json.loads(out)
        u = d.get("usage", {})
        return {"plan": d.get("structuredOutput") or parse_plan(d["text"]), "wall_s": wall, "model_s": None,
                "tokens_in": u.get("input_tokens"), "tokens_out": u.get("output_tokens"),
                "thinking_tokens": u.get("reasoning_tokens")}
    return run


# --- OpenCode free models (default agent only: the free tier rejects custom prompts) --

def opencode_free(model: str, variant: str | None = None):
    def run(transcript: str) -> dict:
        extra = ["--variant", variant] if variant else []
        out, _, wall = _run(["opencode", "run", "--model", f"opencode/{model}", *extra, "--format", "json",
                             prompt.full_prompt(transcript)], timeout=240)
        text, tokens, first, last = "", {}, None, None
        for line in out.splitlines():
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "error":
                raise RuntimeError(str(e.get("error"))[:300])
            part = e.get("part", {})
            if part.get("type") == "text":
                text += part.get("text", "")
            if part.get("type") == "step-finish":
                tokens = part.get("tokens", {})
        return {"plan": parse_plan(text), "wall_s": wall, "model_s": None,
                "tokens_in": tokens.get("input"), "tokens_out": tokens.get("output"),
                "thinking_tokens": tokens.get("reasoning")}
    return run


# --- Groq API (existing key, free tier) ------------------------------------------

def _groq_key() -> str:
    sys.path.insert(0, str(HERE.parent / "stt"))
    from common import api_key   # env, then experiments/stt/.env, then the Voisu keyring
    return api_key("groq")


def groq(model: str, reasoning: str | None = "none"):
    def run(transcript: str) -> dict:
        body = {"model": model, "temperature": 0, "stream": True,
                "stream_options": {"include_usage": True},
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": prompt.system_prompt()},
                             {"role": "user", "content": prompt.user_message(transcript)}]}
        if reasoning:
            body["reasoning_effort"] = reasoning
        for attempt in range(6):
            start = time.perf_counter()
            first = None
            text, usage = "", {}
            with httpx.stream("POST", "https://api.groq.com/openai/v1/chat/completions", json=body,
                              headers={"Authorization": f"Bearer {_groq_key()}"}, timeout=60) as r:
                if r.status_code == 429:
                    time.sleep(float(r.headers.get("retry-after", 20)) + 1)
                    continue
                r.raise_for_status()
                for line in r.iter_lines():
                    if not line.startswith("data: ") or line == "data: [DONE]":
                        continue
                    chunk = json.loads(line[6:])
                    for ch in chunk.get("choices", []):
                        piece = ch.get("delta", {}).get("content")
                        if piece:
                            first = first or time.perf_counter() - start
                            text += piece
                    usage = (chunk.get("x_groq") or {}).get("usage") or chunk.get("usage") or usage
            wall = time.perf_counter() - start
            return {"plan": parse_plan(text), "wall_s": wall, "model_s": wall, "first_token_s": first,
                    "tokens_in": usage.get("prompt_tokens"), "tokens_out": usage.get("completion_tokens")}
        raise RuntimeError("rate limited (429) six times")
    return run


# --- Claude API (direct, pay per token) ----------------------------------------------

def _anthropic_key() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return os.environ["ANTHROPIC_API_KEY"]
    env = HERE / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ANTHROPIC_API_KEY="):
                return line.split("=", 1)[1].strip()
    return None


def claude_api(model: str, thinking: dict, effort: str = "low"):
    def run(transcript: str) -> dict:
        import anthropic
        client = anthropic.Anthropic(api_key=_anthropic_key())
        start = time.perf_counter()
        first = None
        with client.messages.stream(
                model=model, max_tokens=2000, thinking=thinking,
                output_config={"effort": effort, "format": {"type": "json_schema", "schema": prompt.SCHEMA}},
                system=[{"type": "text", "text": prompt.system_prompt(), "cache_control": {"type": "ephemeral"}}],
                messages=[{"role": "user", "content": prompt.user_message(transcript)}]) as stream:
            for _ in stream.text_stream:
                first = first or time.perf_counter() - start
            msg = stream.get_final_message()
        wall = time.perf_counter() - start
        text = next(b.text for b in msg.content if b.type == "text")
        u = msg.usage
        return {"plan": json.loads(text), "wall_s": wall, "model_s": wall, "first_token_s": first,
                "tokens_in": u.input_tokens + (u.cache_read_input_tokens or 0) + (u.cache_creation_input_tokens or 0),
                "tokens_out": u.output_tokens}
    return run


BACKENDS = {
    "claude-haiku": (claude_code("haiku", think=False), "Claude Code, Haiku 5.5, thinking off"),
    "claude-haiku-think": (claude_code("haiku", think=True), "Claude Code, Haiku 5.5, adaptive thinking, effort low"),
    "claude-sonnet": (claude_code("sonnet", think=False), "Claude Code, Sonnet 5.5, thinking off, effort low"),
    "codex-terra": (codex("gpt-5.6-terra"), "Codex exec, GPT-5.6 Terra, effort low"),
    "codex-luna": (codex("gpt-6-luna"), "Codex exec, GPT-6 Luna, effort low"),
    "codex-sol": (codex("gpt-6.1-sol"), "Codex exec, GPT-6.1 Sol, effort low"),
    "codex-sol-medium": (codex("gpt-6.1-sol", "medium"), "Codex exec, GPT-6.1 Sol, effort medium"),
    "codex-terra-medium": (codex("gpt-5.6-terra", "medium"), "Codex exec, GPT-5.6 Terra, effort medium"),
    "agy-flash": (antigravity("gemini-3.8-flash-low"), "Antigravity, Gemini 3.8 Flash (Low)"),
    "grok-fast": (grok("grok-4.7-build-fast"), "Grok CLI, grok-4.7-build-fast"),
    "opencode-step5": (opencode_free("step-5-preview-free"), "OpenCode free, Step 5 Preview (zero retention)"),
    "opencode-muse-medium": (opencode_free("muse-spark-1.3-contributor-free", "medium"),
                             "OpenCode free, Muse Spark 1.3 (contributor: Meta may train on prompts), effort medium"),
    "opencode-muse-high": (opencode_free("muse-spark-1.3-contributor-free", "high"),
                           "OpenCode free, Muse Spark 1.3 (contributor: Meta may train on prompts), effort high"),
    "groq-qwen": (groq("qwen/qwen3.8-27b"), "Groq API free tier, Qwen3.8 27B, reasoning off"),
}

if _anthropic_key():   # added once the user has an API key (experiments/brain/.env)
    BACKENDS |= {
        "api-haiku": (claude_api("claude-haiku-5-5", {"type": "disabled"}), "Claude API, Haiku 5.5, thinking off"),
        "api-haiku-think": (claude_api("claude-haiku-5-5", {"type": "adaptive"}),
                            "Claude API, Haiku 5.5, adaptive thinking, effort low"),
        "api-sonnet": (claude_api("claude-sonnet-5-5", {"type": "between_tools"}),
                       "Claude API, Sonnet 5.5, thinking off (between_tools), effort low"),
    }
