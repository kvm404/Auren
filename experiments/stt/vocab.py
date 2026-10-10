"""Build vocab.txt: the names and terms you say often, used to bias each provider.

  uv run vocab.py          # create vocab.txt (won't overwrite your edits)
  uv run vocab.py --force  # rebuild from scratch

Base terms come first (most distinctive first, because Whisper reads only ~224
tokens of its prompt), then your ~/Projects folder names and live herdr agents.
This is the "self-building vocabulary" idea in miniature.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from common import VOCAB

BASE = [
    # tools and agents
    "herdr", "Voisu", "Codex", "Claude Code", "Claude", "OpenCode", "Antigravity",
    "Supertonic", "Kyutai", "Pocket TTS", "Kokoro", "Groq", "Deepgram", "ElevenLabs",
    "AssemblyAI", "Whisper", "Haiku", "Opus", "Sonnet", "STT", "TTS", "LLM", "MCP",
    # desktop and system
    "Hyprland", "Omarchy", "Quickshell", "Wayland", "PipeWire", "systemctl", "systemd",
    "journalctl", "Ghostty", "Alacritty", "Brave", "Firefox", "Spotify",
    # dev words that get misheard
    "GitHub", "pull request", "README", "API key", "env file", "uv sync", "npm", "bun",
    "quick setup", "subagents", "F2", "M3",
]


# Folder names that are ordinary English: biasing toward them helps nothing and
# would inflate the "terms" score.
COMMON_WORDS = {"games", "archive", "designs", "test", "tests", "docs", "notes", "tmp"}


def project_names() -> list[str]:
    root = Path.home() / "Projects"
    names = []
    for p in sorted(root.iterdir()) if root.exists() else []:
        if p.is_dir() and not p.name.startswith(".") and len(p.name) <= 20:
            name = re.sub(r"[-_]+", " ", p.name)
            if name.lower() not in COMMON_WORDS:
                names.append(name)
    return names


def herdr_agents() -> list[str]:
    try:
        out = subprocess.run(["herdr", "agent", "list"], capture_output=True, text=True, timeout=5).stdout
        agents = json.loads(out)["result"]["agents"]
    except Exception:
        return []
    return sorted({a["name"] for a in agents if a.get("name")})


def main() -> None:
    if VOCAB.exists() and "--force" not in sys.argv:
        print(f"{VOCAB.name} exists ({len(VOCAB.read_text().splitlines())} lines); edit it or use --force")
        return
    seen, terms = set(), []
    for t in BASE + project_names() + herdr_agents():
        if t.lower() not in seen:
            seen.add(t.lower())
            terms.append(t)
    VOCAB.write_text("# Terms to bias speech recognition toward. One per line, most distinctive first.\n"
                     "# Deepgram uses the first 100; Whisper reads ~224 tokens of them.\n"
                     + "\n".join(terms) + "\n")
    print(f"wrote {len(terms)} terms to {VOCAB.name}")


if __name__ == "__main__":
    main()
