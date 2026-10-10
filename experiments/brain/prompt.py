"""The brain's instructions, tools and output format, shared by every backend.

Every backend gets the same text and must answer with the same JSON plan, so the
comparison is about the model, not about each CLI's own tool system. Tool
arguments are name/value string pairs because strict JSON-schema modes (Codex,
Gemini) don't allow free-form objects.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).parent

TOOLS = {
    "launch_app": "Start an app. args: app, workspace (optional).",
    "focus_window": "Bring a window to the front. args: window.",
    "move_window": "Move a window to a workspace. args: window, workspace.",
    "switch_workspace": "Go to a workspace. args: workspace.",
    "close_window": "Close a window. args: window.",
    "screenshot": "Take a screenshot. args: target (screen | window | region), save_to (folder).",
    "open_url": "Open a URL. args: url, browser (optional).",
    "browser_task": "Do a multi-step task in the user's real, logged-in browser "
                    "(read pages, click, check accounts). args: goal, browser.",
    "web_search": "Search the web and summarize results. args: query.",
    "herdr_list": "List the coding agents and panes in herdr. No args.",
    "herdr_read": "Read what an agent's pane is showing. args: agent.",
    "herdr_prompt": "Send a prompt to an existing herdr agent. args: agent, text, wait (yes | no).",
    "herdr_new_tab": "Create a herdr tab, optionally running a command. args: name, command (optional), cwd (optional).",
    "delegate": "Start a new coding agent for a big task (coding, research). args: agent (claude | codex | opencode), task, project.",
    "shell": "Run a shell command. args: command, cwd (optional), notify (yes | no: tell the user when it finishes).",
    "media": "Control music/video. args: action (play | pause | next | previous | volume_up | volume_down | mute).",
    "system_info": "Read battery, memory, CPU, disk, network or time. args: what.",
    "assistant_setting": "Change this assistant's own settings. args: name (voice | speed), value.",
    "ask_user": "Ask the user a short question when the request is ambiguous. args: question.",
}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["say", "actions", "confirm"],
    "properties": {
        "say": {"type": "string"},
        "confirm": {"type": "boolean"},
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["tool", "args"],
                "properties": {
                    "tool": {"type": "string", "enum": list(TOOLS)},
                    "args": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["name", "value"],
                            "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
                        },
                    },
                },
            },
        },
    },
}

RULES = """You are Parth, a voice assistant running on the user's Linux laptop. The user speaks; you get a speech-to-text transcript and decide what to do.

Rules:
- The transcript may contain misheard words. Map them to the closest name in the system snapshot or vocabulary (for example "herder" -> herdr, "Cloud Code" -> Claude Code).
- Answer only with JSON: {"say": "...", "actions": [{"tool": "...", "args": [{"name": "...", "value": "..."}]}], "confirm": false}.
- "say" is one short spoken sentence: what you are doing, or the answer. No markdown.
- "actions" are the tool calls to run now, in order. Use [] when nothing needs doing.
- If you need information before you can finish (an agent's output, a file, system state), call the tool that reads it; you will see the result in the next turn.
- When the user names an existing herdr agent (Codex, Claude Code, OpenCode), prompt that agent with herdr_prompt. Use delegate only for new big tasks.
- Set "confirm": true when an action deletes data, restarts or stops services, exposes secrets such as API keys, sends messages, or spends money. The user will be asked before running it.
- If the request is ambiguous, use ask_user instead of guessing.
- Only use apps that are installed; if one isn't, say so and suggest an installed alternative.
- Do not run any commands or tools of your own. Only output the JSON plan."""


def tools_text() -> str:
    return "\n".join(f"- {name}: {desc}" for name, desc in TOOLS.items())


def system_prompt() -> str:
    context = (HERE / "context.md").read_text()
    return f"{RULES}\n\nTools:\n{tools_text()}\n\n{context}"


def user_message(transcript: str) -> str:
    return f"Transcript: {transcript}"


def full_prompt(transcript: str) -> str:
    """For CLIs whose own system prompt can't be replaced: everything in one message."""
    return f"{system_prompt()}\n\n---\n\n{user_message(transcript)}"


def schema_json() -> str:
    return json.dumps(SCHEMA)
