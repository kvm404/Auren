"""The brain: Claude Haiku 5.5 through a long-running Claude Code process (decision 0003).

    brain = ClaudeBrain()
    for event in brain.ask("open whatsapp on workspace 2"):
        ...   # ("tool", name, input) / ("result", name, text) / ("say", text) / ("done", info)

One `claude -p --input-format stream-json` process is kept running, so requests
don't pay Claude Code's start-up. It gets auren's own instructions (not Claude
Code's), only auren's tools (the MCP server in auren.server), and none of the
user's personal Claude settings (--setting-sources local, and a working folder outside $HOME). The process is
restarted when its conversation grows past MAX_CONTEXT tokens.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Iterator

from .. import safety
from ..config import BRAIN_DIR
from ..profile import Profile
from . import prompt

log = logging.getLogger("auren")
STATE_DIR = safety.STATE_DIR
MAX_CONTEXT = 40_000            # tokens; then start a fresh conversation
LIMIT_WORDS = ("usage limit", "rate limit", "limit reached", "out of usage", "quota")


def speakable(text: str) -> str:
    """Strip markdown the voice would read out literally."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: m.group(1) or m.group(2), text)
    text = re.sub(r"^\s*[-*•]\s+", "", text, flags=re.M)
    return text.replace("~/", "home folder ").strip()


class BrainError(RuntimeError):
    pass


class UsageLimit(BrainError):
    """Claude's plan limit is reached: time to switch to the backup brain."""


class ClaudeBrain:
    def __init__(self, model: str = "haiku", effort: str = "low", think: bool = True):
        self.model, self.effort, self.think = model, effort, think
        self.profile = Profile()
        self.language, self.speaker = "en", "female"      # reply language and the voice's gender (set by Brain)
        self.proc: subprocess.Popen | None = None
        self.context_tokens = 0
        self._stderr: list[str] = []

    # --- process --------------------------------------------------------------

    def _command(self) -> list[str]:
        mcp = {"mcpServers": {"auren": {"command": sys.executable, "args": ["-m", "auren.server"]}}}
        return ["claude", "-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                "--model", self.model, "--effort", self.effort,
                "--settings", json.dumps({"alwaysThinkingEnabled": self.think}),
                "--system-prompt", prompt.system_prompt(self.profile.static_context()),
                "--tools", "", "--mcp-config", json.dumps(mcp), "--strict-mcp-config",
                "--allowedTools", "mcp__auren", "--setting-sources", "local", "--no-session-persistence"]

    def start(self) -> None:
        self.stop()
        # Outside $HOME on purpose: Claude Code loads `.claude/CLAUDE.md` from every parent folder, and
        # ~/.claude/CLAUDE.md is the user's personal file, which must not reach the brain.
        workdir = BRAIN_DIR
        workdir.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "MCP_TOOL_TIMEOUT": "900000", "MCP_TIMEOUT": "30000"}
        self.proc = subprocess.Popen(self._command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True, bufsize=1, cwd=workdir, env=env)
        self._stderr = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self.context_tokens = 0

    def _drain_stderr(self) -> None:
        for line in self.proc.stderr:
            self._stderr.append(line)
            del self._stderr[:-50]

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.proc = None

    # --- talking ----------------------------------------------------------------

    def ask(self, transcript: str) -> Iterator[tuple]:
        if self.proc is None or self.proc.poll() is not None or self.context_tokens > MAX_CONTEXT:
            self.start()
        safety.next_turn()     # a new user message: confirmations from earlier turns become redeemable
        msg = prompt.user_message(transcript, self.profile.live_context(), self.language, self.speaker)
        sent = time.perf_counter()
        self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": msg}}) + "\n")
        self.proc.stdin.flush()
        tool_names: dict[str, str] = {}
        said: list[str] = []
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = ev.get("type")
            if kind == "assistant":
                u = ev["message"].get("usage") or {}
                if u:   # the latest call's input = the conversation's real size
                    self.context_tokens = u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0) \
                        + u.get("cache_creation_input_tokens", 0)
                for block in ev["message"].get("content", []):
                    if block.get("type") == "tool_use":
                        name = block["name"].removeprefix("mcp__auren__")
                        tool_names[block["id"]] = name
                        yield ("tool", name, block.get("input", {}))
                    elif block.get("type") == "text" and block.get("text", "").strip():
                        said.append(block["text"].strip())
            elif kind == "user":
                content = ev.get("message", {}).get("content", [])
                for block in content if isinstance(content, list) else []:
                    if block.get("type") == "tool_result":
                        text = block.get("content")
                        if isinstance(text, list):
                            text = " ".join(c.get("text", "") for c in text if isinstance(c, dict))
                        yield ("result", tool_names.get(block.get("tool_use_id"), "?"), str(text))
            elif kind == "result":
                if ev.get("is_error"):
                    err = str(ev.get("result") or ev.get("errors") or "")
                    if any(w in err.lower() for w in LIMIT_WORDS):
                        raise UsageLimit(err[:300])
                    raise BrainError(err[:300])
                text = speakable(said[-1] if said else (ev.get("result") or "").strip())
                yield ("say", text)
                yield ("done", {"seconds": round(time.perf_counter() - sent, 2),
                                "context_tokens": self.context_tokens, "turns": ev.get("num_turns")})
                return
        raise BrainError("the brain process stopped: " + "".join(self._stderr)[-500:])


# Codex starts tool servers with a minimal environment; the tools need the desktop session's.
SESSION_VARS = ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
                "DISPLAY", "HOME", "PATH", "USER", "XDG_CONFIG_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME",
                "XDG_DATA_HOME", "XDG_CURRENT_DESKTOP", "XDG_SESSION_TYPE", "LANG", "OMARCHY_PATH", "SHELL", "TERMINAL",
                "BROWSER", "EDITOR")


def _session_env_toml() -> str:
    pairs = [f"{k}={json.dumps(os.environ[k])}" for k in SESSION_VARS if os.environ.get(k)]
    return "{" + ",".join(pairs) + "}"


class CodexBrain:
    """Backup brain (decision 0003): GPT-6.1 Sol at low effort through `codex exec`, with auren's tools.

    Each request is a separate `codex exec` run (Codex's own system prompt can't be replaced, so auren's
    instructions go in the message, with the last few exchanges for context). Slower than Claude
    (~10-15 s) but on a separate allowance.
    """

    def __init__(self, model: str = "gpt-6.1-sol", effort: str = "low"):
        self.model, self.effort = model, effort
        self.profile = Profile()
        self.language, self.speaker = "en", "female"
        self.history: list[tuple[str, str]] = []

    def stop(self) -> None:
        pass

    def ask(self, transcript: str) -> Iterator[tuple]:
        safety.next_turn()
        workdir = BRAIN_DIR
        workdir.mkdir(parents=True, exist_ok=True)
        last = workdir / "codex-last.txt"
        recent = "\n".join(f"User: {u}\nAuren: {a}" for u, a in self.history[-4:])
        message = "\n\n".join(x for x in (
            prompt.system_prompt(self.profile.static_context()),
            "Use only the auren tools to act; do not run shell commands yourself.",
            f"Recent conversation:\n{recent}" if recent else "",
            prompt.user_message(transcript, self.profile.live_context(), self.language, self.speaker)) if x)
        cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "-m", self.model, "-c", f"model_reasoning_effort={self.effort}", "-c", "project_doc_max_bytes=0",
               "-c", f'mcp_servers.auren.command="{sys.executable}"',
               "-c", 'mcp_servers.auren.args=["-m","auren.server"]',
               "-c", 'mcp_servers.auren.default_tools_approval_mode="approve"',
               "-c", "mcp_servers.auren.env=" + _session_env_toml(),
               "--json", "--output-last-message", str(last), "-"]
        sent = time.perf_counter()
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, cwd=workdir)
        proc.stdin.write(message)
        proc.stdin.close()
        for line in proc.stdout:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            item = ev.get("item") or {}
            if item.get("type") == "mcp_tool_call":
                if ev.get("type") == "item.started":
                    yield ("tool", item.get("tool"), item.get("arguments", {}))
                elif ev.get("type") == "item.completed":
                    res = item.get("result") or {}
                    text = " ".join(c.get("text", "") for c in res.get("content", []) if isinstance(c, dict)) \
                        if isinstance(res, dict) else str(res)
                    yield ("result", item.get("tool"), text or str(item.get("error") or ""))
            if ev.get("type") in ("error", "turn.failed"):
                err = json.dumps(ev)[:300]
                if any(w in err.lower() for w in LIMIT_WORDS):
                    raise UsageLimit(err)
        proc.wait()
        text = last.read_text().strip() if last.exists() else ""
        if proc.returncode != 0 or not text:
            raise BrainError("codex failed: " + proc.stderr.read()[-300:])
        text = speakable(text)
        self.history.append((transcript, text))
        yield ("say", text)
        yield ("done", {"seconds": round(time.perf_counter() - sent, 2), "context_tokens": 0, "turns": None,
                        "brain": "codex"})


class Brain:
    """Which brain answers, by mode:
    - auto: Claude first; on a usage limit switch to Codex, and retry Claude after RETRY_AFTER seconds.
    - claude: only Claude (a usage limit is reported, no switch).
    - codex: only Codex (saves the Claude allowance)."""

    RETRY_AFTER = 30 * 60
    MODES = ("auto", "claude", "codex")
    EFFORTS = ("low", "medium", "high", "xhigh", "max")      # Claude Code's --effort; low is decision 0003's

    def __init__(self, model: str = "haiku", think: bool = True, mode: str = "auto", effort: str = "low"):
        self.primary = ClaudeBrain(model=model, think=think, effort=effort if effort in self.EFFORTS else "low")
        self.backup = CodexBrain()
        self.limited_since: float | None = None
        self.mode = mode if mode in self.MODES else "auto"

    @property
    def using_backup(self) -> bool:
        if self.mode != "auto":
            return self.mode == "codex"
        return self.limited_since is not None and time.time() - self.limited_since < self.RETRY_AFTER

    def ask(self, transcript: str) -> Iterator[tuple]:
        if self.mode == "claude":
            try:
                yield from self.primary.ask(transcript)
            except UsageLimit as e:
                yield ("say", "Claude's usage limit is reached, and the brain is set to Claude only. "
                              "Switch it to auto or Codex in the bar menu.")
                log.warning("usage limit in claude-only mode: %s", e)
            return
        if not self.using_backup:
            try:
                yield from self.primary.ask(transcript)
                self.limited_since = None
                return
            except UsageLimit as e:
                self.limited_since = time.time()
                yield ("switch", f"Claude's limit is reached ({str(e)[:120]}); using Codex for now")
        yield from self.backup.ask(transcript)

    def stop(self) -> None:
        self.primary.stop()
        self.backup.stop()

    def set_language(self, language: str, speaker: str = "female") -> None:
        """Reply language (en, hi) and the voice's gender; both brains use them from the next request on."""
        if language not in prompt.LANGUAGES:
            raise ValueError(f"language must be one of {', '.join(prompt.LANGUAGES)}")
        for b in (self.primary, self.backup):
            b.language, b.speaker = language, speaker

    @property
    def language(self) -> str:
        return self.primary.language

    def set_effort(self, effort: str) -> None:
        """Claude's effort is fixed when its process starts: restart it (warm), which starts a fresh conversation."""
        if effort not in self.EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(self.EFFORTS)}")
        self.primary.effort = effort
        self.forget()

    def forget(self) -> None:
        """Start a fresh conversation: a new Claude process (warmed up now, so the next answer isn't slow)
        and no Codex history."""
        self.primary.stop()
        self.primary.start()
        self.backup.history.clear()
