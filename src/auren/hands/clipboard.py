"""The Wayland clipboard (wl-clipboard), borrowed for a moment and given back.

    saved = clipboard.save()
    clipboard.copy(b"hello")             # marked sensitive: clipboard history managers skip it
    ...                                  # Ctrl+V somewhere
    clipboard.restore(saved, ours=b"hello")

Used by type_text (pasting is the only way Chrome and Electron apps get every letter) and by
Excalidraw (drawings go in and out as clipboard JSON).
"""

from __future__ import annotations

import subprocess

TEXT = "text/plain;charset=utf-8"
_TEXT = (TEXT, "text/plain", "UTF8_STRING")


class ClipboardError(RuntimeError):
    pass


def types() -> list[str]:
    p = subprocess.run(["wl-paste", "--list-types"], capture_output=True, text=True, timeout=5)
    return p.stdout.split() if p.returncode == 0 else []


def save() -> tuple[str, bytes] | None:
    """What's on the clipboard now, in the one format worth putting back (text, else a PNG, else whatever it
    is, such as copied files). wl-copy serves one format, so a copy that offered several keeps only that one."""
    try:
        offered = types()
        kind = next((t for t in (*_TEXT, "image/png") if t in offered), next((t for t in offered if "/" in t), None))
        if kind is None:
            return None
        p = subprocess.run(["wl-paste", "--no-newline", "--type", kind], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return (kind, p.stdout) if p.returncode == 0 else None


def copy(data: bytes, kind: str | None = None, sensitive: bool = True) -> None:
    """Put data on the clipboard. sensitive: tell clipboard history managers not to keep it (older wl-copy
    doesn't know the flag: then without it)."""
    # wl-copy stays in the background to serve the clipboard: it must not hold our pipes open
    base = ["wl-copy", *(["--type", kind] if kind else [])]
    for cmd in ([*base, "--sensitive"], base) if sensitive else (base,):
        try:
            p = subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except (OSError, subprocess.SubprocessError) as e:
            raise ClipboardError(f"wl-copy failed: {e}") from None
        if p.returncode == 0:
            return
    raise ClipboardError("wl-copy failed")


def text(kind: str | None = None) -> str:
    try:
        p = subprocess.run(["wl-paste", "--no-newline", *(["--type", kind] if kind else [])], capture_output=True,
                           timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return p.stdout.decode("utf-8", "replace") if p.returncode == 0 else ""


def restore(saved: tuple[str, bytes] | None, ours: bytes | None = None) -> None:
    """Put back what save() returned (None: empty the clipboard). ours: the text copied meanwhile (as TEXT);
    if the clipboard holds something else now, the user copied it, and it stays."""
    try:
        if ours is not None and text(TEXT).encode() != ours:
            return
        if saved is None:
            subprocess.run(["wl-copy", "--clear"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        else:
            kind, data = saved
            copy(data, None if kind in _TEXT or kind.startswith("text/plain") else kind, sensitive=False)
    except (OSError, subprocess.SubprocessError, ClipboardError):
        pass
