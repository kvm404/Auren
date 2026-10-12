"""auren on the command line.

  auren daemon                # the voice daemon (the auren systemd service runs this)
  auren doctor                # what works on this machine, and how to fix what doesn't
  auren config                # settings (~/.config/auren/config.toml): show, get, set, edit
  auren voice-model           # download the voice model (install.sh runs this)
  auren chat                  # type requests, see tool calls and the reply
  auren ask "open whatsapp"   # one request
"""

from __future__ import annotations

import argparse
import json
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
    dmn = sub.add_parser("daemon")
    dmn.add_argument("--quiet", action="store_true", help="no voice; replies shown on the OSD")
    ask = sub.add_parser("ask")
    ask.add_argument("text", nargs="+")
    for sp in (sub.choices["chat"], sub.choices["ask"]):
        sp.add_argument("--model", default="haiku")
        sp.add_argument("--no-think", action="store_true")
        sp.add_argument("--effort", default="low", choices=Brain.EFFORTS)
        sp.add_argument("--language", default="en", choices=["en", "hi"], help="reply language")
        sp.add_argument("--male", action="store_true", help="Hindi: male verb forms (for an M voice)")
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
    if a.cmd == "voice-model":
        from .voice import VoiceModelMissing, download_model
        try:
            print("voice model:", download_model())
        except VoiceModelMissing as e:
            print(f"{RED}{e}{END}")
            return 1
        return 0
    brain = CodexBrain() if a.backup else Brain(model=a.model, think=not a.no_think, effort=a.effort)
    if a.language != "en":
        if isinstance(brain, Brain):
            brain.set_language(a.language, "male" if a.male else "female")
        else:
            brain.language, brain.speaker = a.language, "male" if a.male else "female"
    try:
        if a.cmd == "ask":
            handle(brain, " ".join(a.text))
        else:
            print(f"{DIM}auren chat: type a request (Ctrl-D to quit){END}")
            while True:
                try:
                    text = input("you: ").strip()
                except EOFError:
                    break
                if text:
                    handle(brain, text)
    finally:
        brain.stop()


if __name__ == "__main__":
    sys.exit(main())
