"""auren on the command line.

  auren daemon                # the voice daemon (the auren systemd service runs this)
  auren doctor                # what works on this machine, and how to fix what doesn't
  auren config                # settings (~/.config/auren/config.toml): show, get, set, edit
  auren voice-model           # download the voice model (install.sh runs this)
  auren wake-model            # download the wake-word model for hands-free (install.sh runs this)
  auren wake-test             # say "Auren" and see whether hands-free hears it (nothing is sent anywhere)
  auren update                # check for a newer version, and install it if you say so
  auren chat                  # type requests, see tool calls and the reply
  auren ask "open whatsapp"   # one request
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .brain import Brain, BrainError, CodexBrain, UsageLimit

DIM, BOLD, RED, END = "\033[2m", "\033[1m", "\033[31m", "\033[0m"


def _short(value, n: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def handle(brain, text: str) -> None:
    try:
        for ev in brain.ask(text):
            if ev[0] == "tool":
                print(f"{DIM}  → {ev[1]}({_short(ev[2], 120)}){END}")
            elif ev[0] == "result":
                print(f"{DIM}    ← {_short(ev[2])}{END}")
            elif ev[0] == "switch":
                print(f"{RED}  {ev[1]}{END}")
            elif ev[0] == "say":
                print(f"{BOLD}Auren: {ev[1]}{END}")
            elif ev[0] == "done":
                info = ev[1]
                print(f"{DIM}  ({info['seconds']}s" + (f", context {info['context_tokens']} tokens" if info.get('context_tokens') else "")
                      + (f", {info['brain']}" if info.get('brain') else "") + f"){END}")
    except UsageLimit as e:
        print(f"{RED}usage limit reached on the backup brain too ({e}).{END}")
    except BrainError as e:
        print(f"{RED}brain error: {e}{END}")


def main() -> None:
    p = argparse.ArgumentParser(prog="auren", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat")
    sub.add_parser("doctor")
    sub.add_parser("config").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("voice-model")
    sub.add_parser("wake-model")
    wt = sub.add_parser("wake-test")
    wt.add_argument("--sensitivity", choices=("low", "medium", "high"), help="default: handsfree.sensitivity")
    wt.add_argument("--seconds", type=float, default=60)
    sub.add_parser("vocab").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("update").add_argument("args", nargs=argparse.REMAINDER)
    dmn = sub.add_parser("daemon")
    dmn.add_argument("--quiet", action="store_true", help="no voice; replies shown on the OSD")
    ask = sub.add_parser("ask")
    ask.add_argument("text", nargs="+")
    for sp in (sub.choices["chat"], sub.choices["ask"]):
        sp.add_argument("--model", help="Claude's model (default: brain.claude_model in config.toml)")
        sp.add_argument("--no-think", action="store_true")
        sp.add_argument("--effort", choices=Brain.EFFORTS, help="Claude's effort (default: brain.effort)")
        sp.add_argument("--backup", action="store_true", help="use the backup brain (Codex) directly")
    a = p.parse_args()
    if a.cmd == "daemon":
        from .daemon import main as daemon_main
        return daemon_main(speak=not a.quiet)
    if a.cmd == "doctor":
        from .doctor import main as doctor_main
        return doctor_main()
    if a.cmd == "config":
        from .settings import main as config_main
        return config_main(a.args)
    if a.cmd == "vocab":
        from .vocab import main as vocab_main
        return vocab_main(a.args)
    if a.cmd == "update":
        from .update import main as update_main
        return update_main(a.args)
    if a.cmd == "voice-model":
        from .voice import VoiceModelMissing, download_model
        try:
            print("voice model:", download_model())
        except VoiceModelMissing as e:
            print(f"{RED}{e}{END}")
            return 1
        return 0
    if a.cmd == "wake-model":
        from .wake import WakeModelMissing, download_model
        try:
            print("wake-word model:", download_model())
        except WakeModelMissing as e:
            print(f"{RED}{e}{END}")
            return 1
        return 0
    if a.cmd == "wake-test":
        from .settings import load
        from .wake import test
        s = load()
        return test(a.seconds, a.sensitivity or s["handsfree.sensitivity"], s["handsfree.heard_as"])
    # This process runs its own tool runtime and session: confirmations and turns are its own.
    from .config import RUNTIME_DIR
    from .tools import Runtime
    session = f"chat-{os.getpid()}"
    runtime = Runtime(RUNTIME_DIR / f"tools-{os.getpid()}.sock")
    runtime.start()
    from .settings import load
    s = load()
    if a.backup:
        brain = CodexBrain(runtime, session, model=s["brain.codex_model"], effort=s["brain.codex_effort"])
    else:
        brain = Brain(runtime, session, mode=s["brain"], effort=a.effort or s["effort"],
                      claude_model=a.model or s["brain.claude_model"], codex_model=s["brain.codex_model"],
                      codex_effort=s["brain.codex_effort"], think=not a.no_think)
    try:
        if a.cmd == "ask":
            runtime.new_turn(session)
            handle(brain, " ".join(a.text))
        else:
            print(f"{DIM}auren chat: type a request (Ctrl-D to quit){END}")
            while True:
                try:
                    text = input("you: ").strip()
                except EOFError:
                    break
                if text:
                    runtime.new_turn(session)
                    handle(brain, text)
    finally:
        brain.stop()
        runtime.stop()


if __name__ == "__main__":
    sys.exit(main())
