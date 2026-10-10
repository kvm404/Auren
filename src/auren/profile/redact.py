"""Remove secrets from anything the profile shows the brain.

The profile reads shell config, aliases and command lines, which can hold API
keys, passwords and credentials in URLs. Every string that leaves the profile
goes through `redact`.
"""

from __future__ import annotations

import re

MASK = "[redacted]"

# NAME=value where the name says it's secret (export FOO_API_KEY=..., PASSWORD=...).
_SECRET_ASSIGNMENT = re.compile(
    r"\b([A-Za-z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS|CREDENTIALS?)[A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|\S+)",
    re.I,
)
# scheme://user:password@host
_URL_CREDENTIALS = re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@", re.I)
# Well-known token shapes.
_TOKEN_SHAPES = re.compile(
    r"\b(?:sk-[A-Za-z0-9_-]{16,}|sk-ant-[A-Za-z0-9_-]{16,}|tvly-[A-Za-z0-9_-]{16,}|gsk_[A-Za-z0-9]{16,}"
    r"|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abp]-[A-Za-z0-9-]{16,}|AIza[0-9A-Za-z_-]{30,})"
)
# --password value / --token value
_SECRET_FLAG = re.compile(r"(--?(?:password|passwd|token|api-key|apikey|secret)[= ])(\S+)", re.I)


def redact(text: str) -> str:
    text = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}={MASK}", text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{MASK}@", text)
    text = _SECRET_FLAG.sub(lambda m: f"{m.group(1)}{MASK}", text)
    return _TOKEN_SHAPES.sub(MASK, text)


def redact_all(value):
    """Redact every string inside nested dicts and lists."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, list):
        return [redact_all(v) for v in value]
    if isinstance(value, dict):
        return {k: redact_all(v) for k, v in value.items()}
    return value
