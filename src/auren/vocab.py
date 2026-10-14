"""The user's own boost words: names speech recognition should expect, kept in ~/.config/auren/vocab.txt.

    auren vocab list                 # your words and the built-in ones
    auren vocab add <word>           # refused when the built-in list or your list has it already
    auren vocab remove <word>
    (--json: one line {ok, message, mine, defaults, limit}, for the bar panel; limit is how many words of
    your own fit: 100 less the built-in ones)

A running daemon is told to reload, so a new word is used from the next press; no restart.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import sys

from . import ears
from .config import DATA

LIMIT = 100                  # what AssemblyAI takes
MIN_LEN, MAX_LEN = 2, 40     # what ears.vocabulary() keeps


def _key(word: str) -> str:
    return " ".join(word.split()).casefold()


@contextlib.contextmanager
def _locked():
    """One edit at a time, even from two processes (the panel and a terminal)."""
    ears.USER_VOCAB.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ears.USER_VOCAB.parent, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _write(text: str) -> None:
    """Replace the file in one step, so the daemon never reads half of it."""
    tmp = ears.USER_VOCAB.with_name(ears.USER_VOCAB.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, ears.USER_VOCAB)


def _unique(words: list[str]) -> list[str]:
    """The words that count: each once (case and spacing ignored), and only those the ears keep."""
    seen, out = set(), []
    for w in words:
        w = " ".join(w.split())
        if MIN_LEN <= len(w) <= MAX_LEN and _key(w) not in seen:
            seen.add(_key(w))
            out.append(w)
    return out


def defaults() -> list[str]:
    return _unique(ears.read_terms(DATA / "vocab.txt"))


def capacity() -> int:
    """How many words of your own fit: what the recognizer takes, less the built-in words, so it follows the
    built-in list when that grows or shrinks."""
    return max(0, LIMIT - len(defaults()))


def mine() -> list[str]:
    return ears.read_terms(ears.USER_VOCAB)


def add(word: str) -> tuple[bool, str]:
    """(added, what to tell the user)."""
    word = " ".join(word.split())
    if not word:
        return False, "Type a word first."
    if word.startswith("#"):
        return False, "A word can't start with #."
    if not MIN_LEN <= len(word) <= MAX_LEN:
        return False, f"A word needs {MIN_LEN} to {MAX_LEN} letters."
    built_in, own = defaults(), mine()
    if _key(word) in {_key(w) for w in built_in}:
        return False, f"\"{word}\" is already in the built-in list."
    if _key(word) in {_key(w) for w in own}:
        return False, f"\"{word}\" is already in your list."
    if len(_unique(own)) >= capacity():
        return False, f"Your list is full ({capacity()} words); remove one first."
    text = ears.USER_VOCAB.read_text(errors="replace") if ears.USER_VOCAB.exists() else ""
    _write(text + ("" if not text or text.endswith("\n") else "\n") + word + "\n")
    return True, f"Added \"{word}\"."


def remove(word: str) -> tuple[bool, str]:
    word = " ".join(word.split())
    try:
        lines = ears.USER_VOCAB.read_text(errors="replace").splitlines()
    except FileNotFoundError:
        lines = []
    keep = [ln for ln in lines if ln.startswith("#") or _key(ln) != _key(word)]
    if len(keep) == len(lines):
        return False, f"\"{word}\" isn't in your list."
    _write("".join(ln + "\n" for ln in keep))
    return True, f"Removed \"{word}\"."


def _tell_daemon() -> str:
    """Nothing when Auren has the new list or isn't running (it reads the list when it starts); else what to
    tell the user."""
    from .ctl import send

    try:
        reply = send("reload", timeout=5)
    except (FileNotFoundError, ConnectionRefusedError):
        return ""
    except OSError:
        reply = "error"
    return " Auren didn't confirm it; restart Auren to be sure." if reply.startswith("error") else ""


def main(args: list[str]) -> int:
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    cmd, word = (args[0] if args else "list"), " ".join(args[1:])
    if cmd not in ("list", "add", "remove") or (cmd == "list") != (not word):
        print("usage: auren vocab list | add <word> | remove <word>   (--json for the panel)", file=sys.stderr)
        return 2
    ok, message = True, ""
    if cmd != "list":
        try:
            with _locked():
                ok, message = (add if cmd == "add" else remove)(word)
        except OSError as e:
            ok, message = False, f"Couldn't save your words: {e.strerror or e}."
        if ok:
            message += _tell_daemon()
    if as_json:
        print(json.dumps({"ok": ok, "message": message, "mine": mine(), "defaults": defaults(), "limit": capacity()}))
    elif cmd == "list":
        print(f"yours ({len(_unique(mine()))} of {capacity()}):", ", ".join(mine()) or "(none yet)")
        print("built in:", ", ".join(defaults()))
    else:
        print(message)
    return 0 if ok else 1
