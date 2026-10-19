"""The brain: Claude Haiku 5.5 through a long-running Claude Code process (decision 0003).

    brain = Brain(runtime)              # runtime: the tool runtime (auren.tools), already serving
    runtime.new_turn(brain.session)     # the caller counts each user message once
    for event in brain.ask("open whatsapp on workspace 2"):
        ...   # ("tool", name, input) / ("result", name, text) / ("say", text) / ("done", info)

One `claude -p --input-format stream-json` process is kept running, so requests
don't pay Claude Code's start-up. It gets auren's own instructions (not Claude
Code's), only auren's tools (the MCP adapter in auren.server, which forwards to the
runtime), and none of the user's personal Claude settings (--setting-sources local,
and a working folder outside $HOME). The process is restarted when its conversation
grows past MAX_CONTEXT tokens. `interrupt()` stops an answer in progress and keeps the
conversation (Claude Code's own interrupt), so "no, do this instead" still has the context. In codex mode the Claude
process isn't started at all (`warm()`).
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

from ..config import BRAIN_DIR
from ..profile import Profile
from . import prompt

log = logging.getLogger("auren")
MAX_CONTEXT = 40_000            # tokens; then start a fresh conversation
INTERRUPT_GRACE = 5.0           # seconds for Claude Code to stop by itself before its process is killed
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


class Interrupted(BrainError):
    """The user cancelled the answer in progress."""


def _mcp_env(runtime, session: str) -> dict:
    return {"AUREN_TOOLS": str(runtime.socket), "AUREN_SESSION": session}


class ClaudeBrain:
    def __init__(self, runtime, session: str = "daemon", model: str = "haiku", effort: str = "low", think: bool = True):
        self.runtime, self.session = runtime, session
        self.model, self.effort, self.think = model, effort, think
        self._interrupted = self._asking = self._sent = False
        self._asks = 0                   # counts requests, so a late stop or kill can't hit the next one
        # Guards the request's state (_asks, _asking, _sent, _interrupted) and writes to Claude's stdin, so
        # a stop is either in time to keep the message from being sent, or sent after it and for it only.
        self._lock = threading.Lock()
        self.profile = Profile()
        self.proc: subprocess.Popen | None = None
        self.context_tokens = 0
        self._stderr: list[str] = []

    # --- process --------------------------------------------------------------

    def _command(self) -> list[str]:
        mcp = {"mcpServers": {"auren": {"command": sys.executable, "args": ["-m", "auren.server"],
                                        "env": _mcp_env(self.runtime, self.session)}}}
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

    def interrupt(self) -> None:
        """Stop the answer in progress. Claude Code is asked to stop (the stream then ends with a result
        and the conversation goes on); if it hasn't within INTERRUPT_GRACE seconds, the process is killed,
        and the next request starts a fresh one."""
        ask = self._asks                 # the request in progress now: a later one isn't stopped by this
        if not self._lock.acquire(timeout=INTERRUPT_GRACE):      # a write is stuck: Claude isn't reading
            proc = self.proc
            if self._asking and self._asks == ask:
                self._interrupted = True
                if proc and proc.poll() is None:
                    proc.kill()
            return
        try:
            if not self._asking or self._asks != ask:
                return                   # nothing in progress (keep the warm process), or a newer request
            self._interrupted = True
            if not self._sent:
                return                   # the message isn't sent yet, and now it won't be
            proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            # Armed before writing: if the write blocks, the kill undoes it.
            timer = threading.Timer(INTERRUPT_GRACE, self._kill_if_asking, (proc, ask))
            timer.daemon = True
            timer.start()
            try:
                proc.stdin.write(json.dumps({"type": "control_request", "request_id": f"stop-{ask}",
                                             "request": {"subtype": "interrupt"}}) + "\n")
                proc.stdin.flush()
            except (OSError, ValueError):
                proc.kill()
        finally:
            self._lock.release()

    def _kill_if_asking(self, proc: subprocess.Popen, ask: int) -> None:
        got = self._lock.acquire(timeout=1)    # not got: a write is stuck (the check is then unlocked)
        try:
            if self._asking and self._asks == ask:                # still on the request that was stopped
                if proc.poll() is None:
                    log.warning("claude didn't stop within %ss: killing it", INTERRUPT_GRACE)
                    proc.kill()
        finally:
            if got:
                self._lock.release()

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
        with self._lock:
            self._asks += 1
            self._interrupted, self._asking, self._sent = False, True, False
        try:
            # A cancel that came before _asking was set found nothing to interrupt: it shows here instead.
            if _cancelled(self.runtime, self.session):
                raise Interrupted("cancelled")
            yield from self._ask(transcript)
        finally:
            with self._lock:
                self._asking = False

    def _ask(self, transcript: str) -> Iterator[tuple]:
        msg = prompt.user_message(transcript, self.profile.live_context(), pending=self.runtime.summary(self.session),
                                  level=self.runtime.level)
        sent = time.perf_counter()
        with self._lock:
            if self._interrupted or _cancelled(self.runtime, self.session):    # stopped while preparing
                raise Interrupted("cancelled")
            try:
                self.proc.stdin.write(json.dumps({"type": "user", "message": {"role": "user", "content": msg}})
                                      + "\n")
                self.proc.stdin.flush()
            except (OSError, ValueError) as e:     # killed by a stop while the write was stuck, or died
                self.proc = None
                if self._interrupted:
                    raise Interrupted("cancelled") from e
                raise BrainError("the brain process stopped: " + "".join(self._stderr)[-500:]) from e
            self._sent = True
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
                if self._interrupted:            # stopped: the process and the conversation stay
                    raise Interrupted("cancelled")
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
        if self._interrupted:
            self.proc = None
            raise Interrupted("cancelled")
        raise BrainError("the brain process stopped: " + "".join(self._stderr)[-500:])


def _cancelled(runtime, session: str) -> bool:
    return runtime is not None and runtime.cancelled(session)


def _env_toml(env: dict) -> str:
    return "{" + ",".join(f"{k}={json.dumps(v)}" for k, v in env.items()) + "}"


class CodexBrain:
    """Backup brain (decision 0003): GPT-6.1 Sol at low effort through `codex exec`, with auren's tools.

    Each request is a separate `codex exec` run (Codex's own system prompt can't be replaced, so auren's
    instructions go in the message, with the last few exchanges for context). Slower than Claude
    (~10-15 s) but on a separate allowance.
    """

    def __init__(self, runtime, session: str = "daemon", model: str = "gpt-6.1-sol", effort: str = "low"):
        self.runtime, self.session = runtime, session
        self.model, self.effort = model, effort
        self.profile = Profile()
        self.history: list[tuple[str, str]] = []
        self.proc: subprocess.Popen | None = None
        self._interrupted = False

    def interrupt(self) -> None:
        self._interrupted = True
        if self.proc and self.proc.poll() is None:
            self.proc.kill()

    def stop(self) -> None:
        self.interrupt()

    def ask(self, transcript: str) -> Iterator[tuple]:
        self._interrupted = False
        workdir = BRAIN_DIR
        workdir.mkdir(parents=True, exist_ok=True)
        last = workdir / "codex-last.txt"
        recent = "\n".join(f"User: {u}\nAuren: {a}" for u, a in self.history[-4:])
        message = "\n\n".join(x for x in (
            prompt.system_prompt(self.profile.static_context()),
            "Use only the auren tools to act; do not run shell commands yourself.",
            f"Recent conversation:\n{recent}" if recent else "",
            prompt.user_message(transcript, self.profile.live_context(), pending=self.runtime.summary(self.session),
                                level=self.runtime.level)) if x)
        cmd = ["codex", "exec", "--skip-git-repo-check", "--ephemeral", "-s", "read-only",
               "-m", self.model, "-c", f"model_reasoning_effort={self.effort}", "-c", "project_doc_max_bytes=0",
               "-c", f'mcp_servers.auren.command="{sys.executable}"',
               "-c", 'mcp_servers.auren.args=["-m","auren.server"]',
               "-c", 'mcp_servers.auren.default_tools_approval_mode="approve"',
               "-c", "mcp_servers.auren.env=" + _env_toml(_mcp_env(self.runtime, self.session)),
               "--json", "--output-last-message", str(last), "-"]
        sent = time.perf_counter()
        self.proc = proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            text=True, cwd=workdir)
        if _cancelled(self.runtime, self.session):      # cancelled before self.proc was there to kill
            proc.kill()
            proc.wait()
            raise Interrupted("cancelled")
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
        if self._interrupted:
            raise Interrupted("cancelled")
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
    CODEX_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")

    def __init__(self, runtime, session: str = "daemon", mode: str = "auto", effort: str = "low",
                 claude_model: str = "haiku", codex_model: str = "gpt-6.1-sol", codex_effort: str = "low",
                 think: bool = True):
        self.runtime, self.session = runtime, session
        self.primary = ClaudeBrain(runtime, session, model=claude_model, think=think,
                                   effort=effort if effort in self.EFFORTS else "low")
        self.backup = CodexBrain(runtime, session, model=codex_model,
                                 effort=codex_effort if codex_effort in self.CODEX_EFFORTS else "low")
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
                if self.runtime.acted(self.session):
                    # Codex would start the request over and could do something twice.
                    log.warning("usage limit after acting; not handing over: %s", e)
                    yield ("say", "Claude's usage limit was reached partway through, after I'd already started. "
                                  "I stopped there; ask again and Codex will take it from here.")
                    return
                yield ("switch", f"Claude's limit is reached ({str(e)[:120]}); using Codex for now")
        yield from self.backup.ask(transcript)

    def warm(self) -> None:
        """Match the Claude process to the mode: start it now if it will answer (so the first answer isn't
        slow), stop it in codex mode (it would only hold memory). Call it between answers."""
        if self.mode == "codex":
            self.primary.stop()
        elif self.primary.proc is None or self.primary.proc.poll() is not None:
            self.primary.start()

    def interrupt(self) -> None:
        """Stop the answer in progress (whichever brain is giving it)."""
        self.primary.interrupt()
        self.backup.interrupt()

    def stop(self) -> None:
        self.primary.stop()
        self.backup.stop()

    def set_claude(self, effort: str | None = None, model: str | None = None) -> None:
        """Claude's effort and model are fixed when its process starts: restart it, which starts a fresh
        conversation."""
        if effort is not None and effort not in self.EFFORTS:
            raise ValueError(f"effort must be one of {', '.join(self.EFFORTS)}")
        self.primary.effort = effort or self.primary.effort
        self.primary.model = model or self.primary.model
        self.forget()

    def set_codex(self, model: str, effort: str) -> None:
        """Codex starts a process per request, so this applies from the next one."""
        if effort not in self.CODEX_EFFORTS:
            raise ValueError(f"Codex effort must be one of {', '.join(self.CODEX_EFFORTS)}")
        self.backup.model, self.backup.effort = model, effort

    def forget(self) -> None:
        """Start a fresh conversation: a new Claude process (warmed up now unless the mode is codex)
        and no Codex history."""
        self.primary.stop()
        self.warm()
        self.backup.history.clear()
