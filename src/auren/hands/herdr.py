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
- Giving an agent a task (start_agent, prompt_agent with show=True) switches
  herdr's view to it and brings herdr's window up, so the user sees it work.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import time
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..config import STATE_DIR
from . import desktop

WORKSPACE_LABEL = "auren"
STATE = STATE_DIR / "herdr-jobs.json"

# Spoken names -> herdr agent kinds (`herdr agent start --kind`).
KINDS = {
    "claude": "claude", "claude code": "claude", "cloud code": "claude", "claude-code": "claude",
    "codex": "codex", "opencode": "opencode", "open code": "opencode",
    "antigravity": "agy", "agy": "agy", "gemini": "gemini", "grok": "grok", "muse": "muse",
    "cursor": "cursor", "copilot": "copilot", "kimi": "kimi", "pi": "pi", "amp": "amp", "qwen": "qwen",
}

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


def _show(*focus: str) -> str:
    """Put a pane in front of the user: herdr's view switches to it (focus is the herdr command, like
    "tab", "focus", tab_id) and herdr's window comes up from the scratchpad, or opens if herdr isn't in
    a window. Returns what the user now sees, for the brain to report truthfully. Best effort: the
    task goes ahead even if this fails."""
    try:
        opened = None if desktop.herdr_is_open() else desktop.open_herdr()
        herdr(*focus)
        if not desktop.show_herdr(opened):
            return "not shown: herdr's window couldn't be found or opened; the agent runs in the background"
        return "opened herdr on it" if opened else "herdr is showing it"
    except Exception as e:  # noqa: BLE001 - showing is a nicety; never let it stop the task
        return f"not shown ({str(e)[:80]}); the agent runs in the background"


def _wait_for_shell(pane_id: str, timeout: float = 10) -> None:
    """A new pane's shell needs a moment before it prints a prompt."""
    end = time.time() + timeout
    while time.time() < end:
        if screen(pane_id, 5).strip():
            return
        time.sleep(0.2)


# --- launching an agent: model, effort, folder trust ------------------------------------

# Effort words as spoken -> each CLI's levels.
_EFFORT_WORDS = {"min": "minimal", "minimum": "minimal", "minimal": "minimal", "low": "low", "med": "medium",
                 "medium": "medium", "normal": "medium", "high": "high", "x high": "xhigh", "x-high": "xhigh",
                 "xhigh": "xhigh", "extra high": "xhigh", "very high": "xhigh", "max": "max", "maximum": "max",
                 "highest": "max"}
EFFORTS = {"claude": ("low", "medium", "high", "xhigh", "max"),
           "codex": ("minimal", "low", "medium", "high", "xhigh")}
_CLAUDE_FAMILIES = ("opus", "sonnet", "haiku", "fable")


def _effort(kind: str, effort: str) -> str:
    said = " ".join(effort.lower().replace("effort", "").split())
    level = _EFFORT_WORDS.get(said, said)
    levels = EFFORTS[kind]
    if kind == "claude" and level == "minimal":
        level = "low"
    if kind == "codex" and level == "max":
        level = "xhigh"             # Codex's highest
    if level not in levels:
        raise HerdrError("bad_effort", f"{kind} takes effort {', '.join(levels)}, not {effort!r}")
    return level


def claude_model(model: str) -> str:
    """A Claude model as said -> what `claude --model` takes: "Opus 5.5" -> claude-opus-5-5, "sonnet" ->
    sonnet (the latest), a full id as is."""
    m = model.strip().lower()
    if m.startswith("claude-") or "/" in m:
        return m.replace(" ", "")
    words = re.sub(r"\bclaude\b", " ", m).replace("point", ".").strip()
    hit = re.fullmatch(rf"({'|'.join(_CLAUDE_FAMILIES)})\s*-?\s*(\d+)(?:\s*[.\- ]\s*(\d+))?(\[1m\])?", words)
    if hit:
        family, major, minor, ctx = hit.groups()
        return f"claude-{family}-{major}" + (f"-{minor}" if minor else "") + (ctx or "")
    return re.sub(r"\s+", "-", words)


def codex_model(model: str) -> str:
    """A Codex model as said -> its slug: "GPT 6.1 Sol" -> gpt-6.1-sol."""
    m = " ".join(model.strip().lower().replace(" point ", ".").split())
    m = re.sub(r"\bgpt\s*-?\s*(?=\d)", "gpt-", m)
    return re.sub(r"\s*-?\s+", "-", m)


def agent_flags(kind: str, model: str = "", effort: str = "") -> tuple[list[str], dict]:
    """Command-line flags that start `kind` with that model and effort, and what they set."""
    model, effort = model.strip(), effort.strip()
    args: list[str] = []
    chosen: dict = {}
    if kind == "claude":
        if model:
            chosen["model"] = claude_model(model)
            args += ["--model", chosen["model"]]
        if effort:
            chosen["effort"] = _effort(kind, effort)
            args += ["--effort", chosen["effort"]]
    elif kind == "codex":
        if model:
            chosen["model"] = codex_model(model)
            args += ["--model", chosen["model"]]
        if effort:
            chosen["effort"] = _effort(kind, effort)
            args += ["-c", f"model_reasoning_effort={chosen['effort']}"]
    else:
        if effort:
            raise HerdrError("no_effort", f"Auren can't set the effort of {kind}; only Claude Code's and Codex's")
        if model:
            if kind not in ("gemini", "agy"):      # OpenCode's TUI has no --model, so it would fail to start
                raise HerdrError("no_model", f"Auren can't pick the model of {kind} when starting it; only Claude "
                                             "Code's, Codex's, Gemini's and Antigravity's (agy)")
            chosen["model"] = model.replace(" ", "-")
            args += ["--model", chosen["model"]]
    return args, chosen


def _claude_config() -> Path:
    d = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(d) / ".claude.json" if d else Path.home() / ".claude.json"


def _codex_config() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "config.toml"


def _write_atomic(path: Path, text: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
        if path.exists():
            os.chmod(tmp, path.stat().st_mode & 0o777)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def trust_folder(kind: str, cwd: str) -> bool:
    """Mark `cwd` trusted in the agent's own config, the way answering "Yes, I trust this folder" does,
    so it starts without asking. Only Claude Code and Codex ask. True if something was written."""
    if kind == "claude":
        path = _claude_config()
        try:
            data = json.loads(path.read_text()) if path.exists() else {}
        except (OSError, json.JSONDecodeError):
            return False                        # never overwrite a config we can't read
        project = data.setdefault("projects", {}).setdefault(cwd, {})
        if project.get("hasTrustDialogAccepted"):
            return False
        project["hasTrustDialogAccepted"] = True
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(path, json.dumps(data, indent=2, ensure_ascii=False))
        return True
    if kind == "codex":
        path = _codex_config()
        try:
            text = path.read_text() if path.exists() else ""
            projects = tomllib.loads(text).get("projects", {})
        except (OSError, tomllib.TOMLDecodeError):
            return False
        if cwd in projects:                     # trusted already, or the user said no: theirs to keep
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if not text or text.endswith("\n\n") else ("\n" if text.endswith("\n") else "\n\n")
        _write_atomic(path, f'{text}{sep}[projects.{json.dumps(cwd)}]\ntrust_level = "trusted"\n')
        return True
    return False


# The folder-trust question Claude Code and Codex ask on their first start in a folder.
_TRUST_PROMPT = re.compile(r"trust (this|the) (folder|files|contents|directory)|Do you trust|Quick safety check", re.I)
_OPTION = re.compile(r"^\s*([❯›>])?\s*(?:\d+\.\s*)?(Yes|No)\b", re.I)


def _trust_keys(text: str) -> list[str] | None:
    """The keys that answer yes to a trust question on screen: arrows from the highlighted option to the
    "Yes" one, then Enter. [] if there's no question; None if it can't be read (no guessing: Enter on
    the wrong option quits the agent, and Claude Code highlights "No, exit" first)."""
    lines = text.splitlines()[-30:]
    if not _TRUST_PROMPT.search("\n".join(lines)):
        return []
    options = [m for m in map(_OPTION.match, lines) if m]
    cursor = next((i for i, m in enumerate(options) if m.group(1)), None)
    yes = next((i for i, m in enumerate(options) if m.group(2).lower() == "yes"), None)
    if cursor is None or yes is None:
        return None
    step = "down" if yes > cursor else "up"
    return [step] * abs(yes - cursor) + ["enter"]


def _accept_trust_prompt(pane_id: str) -> bool:
    """If the agent is asking whether to trust its folder, answer yes. True if it was asked."""
    for _ in range(2):
        try:
            keys = _trust_keys(screen(pane_id, 40))
        except HerdrError:
            return False
        if keys == []:
            return False
        if keys is None:
            raise HerdrError("trust_prompt", "the agent is asking whether to trust its folder and Auren couldn't "
                                             "read the choices; the user has to answer it in herdr")
        herdr("pane", "send-keys", pane_id, *keys)
        time.sleep(1.5)
        try:
            if _trust_keys(screen(pane_id, 40)) == []:
                return True
        except HerdrError:
            return True
    raise HerdrError("trust_prompt", "the agent still asks whether to trust its folder; the user has to answer it in herdr")


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


def prompt_agent(agent: str, text: str, hint: str = "", wait: bool = True, timeout_s: int = 600,
                 show: bool = False) -> dict:
    """Send a prompt to a running agent. With wait, return its reply once it settles. show: switch the
    user's view to the agent first."""
    a = resolve(agent, hint)
    if a.status == "blocked":
        return {"agent": a.describe(), "status": "blocked",
                "note": "the agent is waiting at an approval or question; ask the user",
                "screen": "\n".join(screen(a.pane_id, 40).splitlines()[-25:])}
    view = _show("agent", "focus", a.pane_id) if show else ""
    args = ["agent", "prompt", a.pane_id, text]
    if wait:
        args += ["--wait", "--timeout", str(timeout_s * 1000)]
    try:
        r = herdr(*args, timeout=timeout_s + 30)
    except HerdrError as e:
        if e.code in ("timeout", "agent_prompt_stalled"):
            return {"agent": a.describe(), "status": e.code, "note": "the prompt may have been delivered; do not resend blindly",
                    "screen": "\n".join(screen(a.pane_id, 40).splitlines()[-25:])} | ({"view": view} if view else {})
        raise
    status = r.get("agent", {}).get("agent_status", "submitted")
    out = {"agent": a.describe(), "status": status} | ({"view": view} if view else {})
    if wait:
        full = screen(a.pane_id, 200)
        out["reply"] = reply_after(full, text) if status in ("idle", "done") else ""
        if status == "blocked":
            out["note"] = "the agent stopped at an approval or question; ask the user"
            out["screen"] = "\n".join(full.splitlines()[-25:])
    return out


def start_agent(kind: str = "", cwd: str = "~", name: str = "", task: str = "",
                wait: bool = False, timeout_s: int = 1800, agent_args: list[str] | None = None,
                show: bool = False, model: str = "", effort: str = "") -> dict:
    """Start a new agent in its own tab of the auren workspace, optionally with a first task. show:
    switch the user's view to the new tab once the agent runs (not before: keys the user is still
    typing would land in the tab's shell). kind: empty for the user's default agent. model, effort:
    as the user said them ("opus 5.5", "high"); empty for the agent's own default. The folder is
    trusted first, so the agent doesn't stop to ask."""
    if not kind.strip():
        from ..integrations import default_agent
        kind = default_agent()
    kind = KINDS.get(kind.strip().lower(), kind.strip().lower())
    cwd = str(Path(os.path.expanduser(cwd)).resolve())
    if not Path(cwd).is_dir():
        raise HerdrError("bad_cwd", f"folder does not exist: {cwd}")
    flags, chosen = agent_flags(kind, model, effort)       # a model or effort it can't take fails before a tab opens
    taken = {a.name for a in agents() if a.name} | {j.name for j in jobs()}
    name = _slug(name or _task_name(task) or Path(cwd).name or kind, taken)
    try:
        trust_folder(kind, cwd)
    except OSError:
        pass                                   # it asks instead; answered below
    tab_id, pane_id = _new_tab(name, cwd)
    all_jobs = _load_jobs()
    all_jobs[name] = Job(name=name, tab_id=tab_id, pane_id=pane_id, cwd=cwd, kind=kind, task=task)
    _save_jobs(all_jobs)
    _wait_for_shell(pane_id)
    args = [*flags, *(agent_args or [])]
    try:
        herdr("agent", "start", name, "--kind", kind, "--pane", pane_id, "--timeout", "60000",
              *(["--", *args] if args else []), timeout=90)
    except HerdrError:
        if not _accept_trust_prompt(pane_id):  # stuck at the trust question: answer it and go on
            raise
    _accept_trust_prompt(pane_id)
    view = _show("tab", "focus", tab_id) if show else ""
    out = {"job": name, "kind": kind, "cwd": cwd, "tab": tab_id, "pane": pane_id, "status": "started", **chosen}
    if view:
        out["view"] = view
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
    """Switch herdr's view to an agent's pane and bring herdr's window up (opening it if needed)."""
    a = resolve(agent, hint)
    return f"{_show('agent', 'focus', a.pane_id)}: {a.describe()}"
