"""Shared helpers: paths, API keys, text normalization, and scoring."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

HERE = Path(__file__).parent
RECORDINGS = HERE / "recordings"
RESULTS = HERE / "results"
REFS = RECORDINGS / "refs.json"        # {"01": "exact words said", ...}
VOCAB = HERE / "vocab.txt"

_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "deepgram": "DEEPGRAM_API_KEY",
    "elevenlabs": "ELEVENLABS_API_KEY",
    "assemblyai": "ASSEMBLYAI_API_KEY",
}


def _dotenv() -> dict[str, str]:
    path = HERE / ".env"
    if not path.exists():
        return {}
    pairs = (line.split("=", 1) for line in path.read_text().splitlines()
             if "=" in line and not line.lstrip().startswith("#"))
    return {k.strip(): v.strip().strip("'\"") for k, v in pairs}


def api_key(service: str) -> str | None:
    """Env var, then experiments/stt/.env, then Voisu's keyring entry."""
    name = _KEY_ENV[service]
    if value := os.environ.get(name) or _dotenv().get(name):
        return value
    try:
        out = subprocess.run(["secret-tool", "lookup", "voisu-provider", service],
                             capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out or None


def load_refs() -> dict[str, str]:
    return json.loads(REFS.read_text()) if REFS.exists() else {}


def load_vocab() -> list[str]:
    if not VOCAB.exists():
        return []
    return [line.strip() for line in VOCAB.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


# --- normalization -----------------------------------------------------------

_NUMBERS = {w: str(i) for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen "
    "fourteen fifteen sixteen seventeen eighteen nineteen twenty".split())}
_NUMBERS.update({"thirty": "30", "forty": "40", "fifty": "50", "hundred": "100"})


_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBERS) + r")\b", re.IGNORECASE)
_term_patterns: list[tuple[re.Pattern, str]] | None = None


def _vocab_patterns() -> list[tuple[re.Pattern, str]]:
    """Each vocab term matched with any spacing/case, mapped to one canonical token.

    "GitHub", "git hub" and "github" all become "github"; "Open Code" becomes "opencode".
    """
    global _term_patterns
    if _term_patterns is None:
        _term_patterns = []
        for term in sorted(load_vocab(), key=len, reverse=True):
            parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", term)
            if len(parts) < 2:
                continue
            body = r"[\s\-_]*".join(re.escape(p.lower()) for p in parts)
            pattern = re.compile(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", re.IGNORECASE)
            _term_patterns.append((pattern, "".join(p.lower() for p in parts)))
    return _term_patterns


def words(text: str) -> list[str]:
    """Normalize text into comparable words.

    Number words become digits ("workspace two" = "workspace 2"), vocabulary terms
    become one token regardless of spacing ("Open Code" = "OpenCode"), other
    camelCase is split ("CodexPane" = "Codex pane"), punctuation and case are
    dropped, and letters split from digits ("F2" = "F 2").
    """
    t = _NUMBER_WORD.sub(lambda m: _NUMBERS[m.group(0).lower()], text)
    for pattern, canonical in _vocab_patterns():
        t = pattern.sub(canonical, t)
    t = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", t)
    t = t.lower().replace("-", " ").replace("_", " ").replace("/", " ")
    t = re.sub(r"(?<=[a-z])(?=\d)|(?<=\d)(?=[a-z])", " ", t)
    return re.findall(r"[a-z0-9']+", t)


def squash(text: str) -> str:
    """Words joined with no spaces, for space-insensitive term matching."""
    return "".join(words(text))


# --- scoring -----------------------------------------------------------------

def edit_distance(ref: list[str], hyp: list[str]) -> int:
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h)))
        prev = cur
    return prev[-1]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    """(word errors, reference word count)."""
    r, h = words(ref), words(hyp)
    return edit_distance(r, h), len(r)


def terms_in(text: str, vocab: list[str]) -> list[str]:
    """Vocabulary terms present in text, space- and case-insensitive."""
    s = squash(text)
    return [t for t in vocab if squash(t) and squash(t) in s]


def term_hits(ref: str, hyp: str, vocab: list[str]) -> tuple[int, int, list[str]]:
    """(terms recognized, terms in reference, missed terms)."""
    expected = terms_in(ref, vocab)
    got = squash(hyp)
    missed = [t for t in expected if squash(t) not in got]
    return len(expected) - len(missed), len(expected), missed
