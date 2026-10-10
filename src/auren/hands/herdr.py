"""herdr tools: find, read, prompt and start coding agents; run commands in panes.

Layout rules (see docs/hands.md):
- auren keeps its own herdr workspace, labelled "auren", created on demand in
  the background (--no-focus). If the user closes it, the next job recreates it.
- Every job auren starts gets its own tab there, named after the job; an agent
  in it gets the same name.
- The user's own workspaces are never changed. Their agents are found live
  (by name, kind, project folder or workspace/tab label) and only prompted or
  read.
- auren only closes tabs it created, tracked in ~/.local/state/auren/herdr-jobs.json.
- An agent waiting at an approval question is reported, never answered
  automatically.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

WORKSPACE_LABEL = "auren"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "auren" / "herdr-jobs.json"

# Spoken names -> herdr agent kinds (`herdr agent start --kind`).
KINDS = {
    "claude": "claude", "claude code": "claude", "cloud code": "claude", "claude-code": "claude",
    "codex": "codex", "opencode": "opencode", "open code": "opencode",
    "antigravity": "agy", "agy": "agy", "gemini": "gemini", "grok": "grok", "muse": "muse",
    "cursor": "cursor", "copilot": "copilot", "kimi": "kimi", "pi": "pi", "amp": "amp", "qwen": "qwen",
}
DEFAULT_KIND = "codex"   # heavy jobs go to Codex by default; Claude's allowance is the scarce one

# Footer lines agent TUIs draw under the reply (Codex, Claude Code, OpenCode).
_FOOTER = re.compile(
    r"^\s*(›\s|>\s*$|─|━|╭|╰|│\s*$|\?\s+for shortcuts|←|⚠\s+\d+h limit|Worked for |"
    r".*\b(Context \d+% used|esc to interrupt|for shortcuts|bypass permissions|accept edits)\b)")


class HerdrError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code, self.message = code, message


def herdr(*args: str, timeout: float = 30, raw: bool = False):
    """Run a herdr CLI command; return parsed JSON `result` (or raw text)."""
    try:
        p = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise HerdrError("not_installed", "herdr is not installed") from None
    except subprocess.TimeoutExpired:
        raise HerdrError("timeout", f"herdr {' '.join(args[:2])} took longer than {timeout:.0f}s") from None
    if p.returncode != 0:
        try:
            err = json.loads(p.stderr).get("error", {})
            raise HerdrError(err.get("code", "error"), err.get("message", p.stderr.strip()))
        except (json.JSONDecodeError, AttributeError):
            msg = (p.stderr or p.stdout).strip()
            if "connect" in msg.lower() or "socket" in msg.lower():
                raise HerdrError("not_running", "herdr isn't running (start it with: omarchy launch terminal herdr)") from None
            raise HerdrError("error", msg[:300]) from None
    if raw:
        return p.stdout
    if not p.stdout.strip():          # some commands (pane run) print nothing on success
        return {}
    return json.loads(p.stdout).get("result", {})


# --- live state -------------------------------------------------------------------

@dataclass
class Agent:
    name: str | None
    kind: str
    status: str
    pane_id: str
    cwd: str
    workspace: str
    tab: str
    focused: bool = False

    def describe(self) -> str:
        where = self.cwd.replace(str(Path.home()), "~", 1)
        label = f"`{self.name}`" if self.name else self.pane_id
        return f"{label} ({self.kind}, {self.status}) in {where} [workspace “{self.workspace}”, tab “{self.tab}”]"


def agents() -> list[Agent]:
    ws = {w["workspace_id"]: w.get("label") or w["workspace_id"] for w in herdr("workspace", "list")["workspaces"]}
    tabs = {t["tab_id"]: t.get("label") or t["tab_id"] for t in herdr("tab", "list")["tabs"]}
    return [Agent(name=a.get("name"), kind=a["agent"], status=a.get("agent_status", "unknown"),
                  pane_id=a["pane_id"], cwd=a.get("foreground_cwd") or a.get("cwd", ""),
                  workspace=ws.get(a["workspace_id"], a["workspace_id"]), tab=tabs.get(a["tab_id"], a["tab_id"]),
                  focused=a.get("focused", False))
            for a in herdr("agent", "list")["agents"]]


def resolve(target: str, hint: str = "", live: list[Agent] | None = None) -> Agent:
    """Find one agent from what the user said: a name, pane id, kind ("codex",
    "claude code") and/or a hint (project folder, workspace or tab label)."""
    live = agents() if live is None else live
    t = target.strip().lower()
    for a in live:
        if t and (t == (a.name or "") or t == a.pane_id.lower()):
            return a
    kind = KINDS.get(t, t if t in KINDS.values() else None)
    pool = [a for a in live if kind is None or a.kind == kind]
    if hint:
        h = hint.lower()
        narrowed = [a for a in pool if h in a.cwd.lower() or h in a.workspace.lower() or h in a.tab.lower()
                    or h in (a.name or "")]
        pool = narrowed or pool
    if len(pool) == 1:
        return pool[0]
    if not pool:
        running = "; ".join(a.describe() for a in live) or "none"
        raise HerdrError("agent_not_found", f"no {target or 'matching'} agent is running. Running agents: {running}")
    raise HerdrError("ambiguous", "which one? " + "; ".join(a.describe() for a in pool))


def screen(pane_or_agent: str, lines: int = 120) -> str:
    return herdr("agent" if not re.fullmatch(r"w\w+:p\w+", pane_or_agent) else "pane", "read", pane_or_agent,
                 "--source", "recent-unwrapped", "--lines", str(lines), raw=True)


def reply_after(text: str, prompt: str) -> str:
    """The agent's answer to `prompt`: what follows the last echo of the prompt,
    without the TUI footer. Falls back to the screen's tail."""
    lines = text.splitlines()
    key = " ".join(prompt.split())[:40]
    start = 0
    for i in range(len(lines) - 1, -1, -1):
        if key and key in " ".join(lines[i].split()):
            start = i + 1
            break
    else:
        lines = lines[-40:]
    body = [l for l in lines[start:] if not _FOOTER.match(l)]
    while body and not body[-1].strip():
        body.pop()
    while body and not body[0].strip():
        body.pop(0)
    return "\n".join(l.rstrip() for l in body).strip()


# --- jobs auren owns ---------------------------------------------------------------

@dataclass
class Job:
    name: str
    tab_id: str
    pane_id: str
    cwd: str
    kind: str | None = None          # agent kind, or None for a plain command
    task: str = ""
    started: float = field(default_factory=time.time)


def _load_jobs() -> dict[str, Job]:
    if not STATE.exists():
        return {}
    return {k: Job(**v) for k, v in json.loads(STATE.read_text()).items()}


def _save_jobs(jobs: dict[str, Job]) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({k: asdict(v) for k, v in jobs.items()}, indent=1))


def jobs() -> list[Job]:
    """auren's jobs whose tab still exists (closed tabs are forgotten)."""
    all_jobs = _load_jobs()
    try:
        live_tabs = {t["tab_id"] for t in herdr("tab", "list")["tabs"]}
    except HerdrError:
        return list(all_jobs.values())
    alive = {k: j for k, j in all_jobs.items() if j.tab_id in live_tabs}
    if alive.keys() != all_jobs.keys():
        _save_jobs(alive)
    return list(alive.values())


def _slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:24] or "job"
    if not base[0].isalpha():
        base = "j-" + base
    name, n = base, 2
    while name in taken:
        name, n = f"{base[:28]}-{n}", n + 1
    return name


_FILLER = {"a", "an", "the", "to", "of", "in", "into", "for", "and", "on", "with", "please", "write", "make",
           "create", "file", "this", "that", "my", "me", "short", "small", "new", "it", "is", "about",
           "read", "summarize", "summary", "use", "exactly", "line", "lines", "one", "two", "three", "can", "you", "ask"}


def _task_name(task: str) -> str:
    """A short job name from a task: 'Write a short poem about Omarchy into…' -> 'poem-omarchy'."""
    words = [w for w in re.findall(r"[a-z0-9]+", task.lower()) if w not in _FILLER and not w.isdigit()]
    return "-".join(words[:2])


def _auren_workspace(cwd: str) -> str:
    for w in herdr("workspace", "list")["workspaces"]:
        if w.get("label") == WORKSPACE_LABEL:
            return w["workspace_id"]
    return herdr("workspace", "create", "--label", WORKSPACE_LABEL, "--cwd", cwd, "--no-focus")["workspace"]["workspace_id"]


def _new_tab(label: str, cwd: str) -> tuple[str, str]:
    ws = _auren_workspace(cwd)
    r = herdr("tab", "create", "--workspace", ws, "--label", label, "--cwd", cwd, "--no-focus")
    return r["tab"]["tab_id"], r["root_pane"]["pane_id"]


def _wait_for_shell(pane_id: str, timeout: float = 10) -> None:
    """A new pane's shell needs a moment before it prints a prompt."""
    end = time.time() + timeout
    while time.time() < end:
        if screen(pane_id, 5).strip():
            return
        time.sleep(0.2)


# --- tools ---------------------------------------------------------------------------

def list_agents() -> str:
    live = agents()
    mine = {j.pane_id for j in jobs()}
    if not live:
        return "No coding agents are running in herdr."
    return "\n".join(("[auren job] " if a.pane_id in mine else "") + a.describe() for a in live)


def read_agent(agent: str, hint: str = "", lines: int = 80) -> str:
    a = resolve(agent, hint)
    text = screen(a.pane_id, lines)
    return f"{a.describe()}\n---\n" + "\n".join(text.splitlines()[-lines:])


def prompt_agent(agent: str, text: str, hint: str = "", wait: bool = True, timeout_s: int = 600) -> dict:
    """Send a prompt to a running agent. With wait, return its reply once it settles."""
    a = resolve(agent, hint)
    if a.status == "blocked":
        return {"agent": a.describe(), "status": "blocked",
                "note": "the agent is waiting at an approval or question; ask the user",
                "screen": "\n".join(screen(a.pane_id, 40).splitlines()[-25:])}
    args = ["agent", "prompt", a.pane_id, text]
    if wait:
        args += ["--wait", "--timeout", str(timeout_s * 1000)]
    try:
        r = herdr(*args, timeout=timeout_s + 30)
    except HerdrError as e:
        if e.code in ("timeout", "agent_prompt_stalled"):
            return {"agent": a.describe(), "status": e.code, "note": "the prompt may have been delivered; do not resend blindly",
                    "screen": "\n".join(screen(a.pane_id, 40).splitlines()[-25:])}
        raise
    status = r.get("agent", {}).get("agent_status", "submitted")
    out = {"agent": a.describe(), "status": status}
    if wait:
        full = screen(a.pane_id, 200)
        out["reply"] = reply_after(full, text) if status in ("idle", "done") else ""
        if status == "blocked":
            out["note"] = "the agent stopped at an approval or question; ask the user"
            out["screen"] = "\n".join(full.splitlines()[-25:])
    return out


def start_agent(kind: str = DEFAULT_KIND, cwd: str = "~", name: str = "", task: str = "",
                wait: bool = False, timeout_s: int = 1800, agent_args: list[str] | None = None) -> dict:
    """Start a new agent in its own tab of the auren workspace, optionally with a first task."""
    kind = KINDS.get(kind.strip().lower(), kind.strip().lower())
    cwd = str(Path(os.path.expanduser(cwd)).resolve())
    if not Path(cwd).is_dir():
        raise HerdrError("bad_cwd", f"folder does not exist: {cwd}")
    taken = {a.name for a in agents() if a.name} | {j.name for j in jobs()}
    name = _slug(name or _task_name(task) or Path(cwd).name or kind, taken)
    tab_id, pane_id = _new_tab(name, cwd)
    all_jobs = _load_jobs()
    all_jobs[name] = Job(name=name, tab_id=tab_id, pane_id=pane_id, cwd=cwd, kind=kind, task=task)
    _save_jobs(all_jobs)
    _wait_for_shell(pane_id)
    herdr("agent", "start", name, "--kind", kind, "--pane", pane_id, "--timeout", "60000",
          *(["--", *agent_args] if agent_args else []), timeout=90)
    out = {"job": name, "kind": kind, "cwd": cwd, "tab": tab_id, "pane": pane_id, "status": "started"}
    if task:
        out.update(prompt_agent(name, task, wait=wait, timeout_s=timeout_s))
    return out


def run_command(command: str, cwd: str = "~", name: str = "", wait: bool = False, timeout_s: int = 600,
                keep: bool = False) -> dict:
    """Run a shell command in its own tab of the auren workspace. A command that finishes while we
    wait has its tab closed again (its output is in the result) unless keep=True."""
    cwd = str(Path(os.path.expanduser(cwd)).resolve())
    taken = {j.name for j in jobs()}
    name = _slug(name or command.split()[0], taken)
    tab_id, pane_id = _new_tab(name, cwd)
    all_jobs = _load_jobs()
    all_jobs[name] = Job(name=name, tab_id=tab_id, pane_id=pane_id, cwd=cwd, task=command)
    _save_jobs(all_jobs)
    _wait_for_shell(pane_id)
    # The marker is split in the typed text, so only the command's own output matches it.
    herdr("pane", "run", pane_id, f'{command}; echo "AUREN""_EXIT=$?"')
    out = {"job": name, "cwd": cwd, "pane": pane_id, "status": "running"}
    if wait:
        out.update(command_result(name, timeout_s))
        if out["status"] == "finished" and not keep:
            close_job(name)
            out["tab"] = "closed (finished)"
    return out


def command_result(job: str, timeout_s: int = 0) -> dict:
    """Exit code and output of a run_command job (waits up to timeout_s for it to finish)."""
    j = next((x for x in jobs() if x.name == job), None)
    if j is None:
        raise HerdrError("job_not_found", f"no auren job called {job}")
    if timeout_s:
        try:
            herdr("pane", "wait-output", j.pane_id, "--regex", r"AUREN_EXIT=\d+", "--timeout", str(timeout_s * 1000),
                  timeout=timeout_s + 10)
        except HerdrError as e:
            if e.code != "timeout":
                raise
    text = screen(j.pane_id, 200)
    m = list(re.finditer(r"AUREN_EXIT=(\d+)", text))
    if not m:
        return {"status": "running", "output_tail": "\n".join(text.splitlines()[-20:])}
    before = text[:m[-1].start()].splitlines()
    return {"status": "finished", "exit_code": int(m[-1].group(1)), "output_tail": "\n".join(before[-30:])}


def close_job(job: str) -> str:
    """Close a tab auren created. Refuses anything else."""
    all_jobs = _load_jobs()
    j = all_jobs.get(job)
    if j is None:
        raise HerdrError("not_ours", f"{job} is not a auren job; auren only closes tabs it created")
    try:
        herdr("tab", "close", j.tab_id)
    except HerdrError as e:
        if e.code not in ("tab_not_found", "not_found"):
            raise
    del all_jobs[job]
    _save_jobs(all_jobs)
    return f"closed {job}"


def focus(agent: str, hint: str = "") -> str:
    """Switch herdr's view to an agent's pane (the herdr window itself is raised by the desktop tools)."""
    a = resolve(agent, hint)
    herdr("agent", "focus", a.pane_id)
    return f"focused {a.describe()}"
