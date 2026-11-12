"""auren on the command line.

  auren daemon                # the voice daemon (the auren systemd service runs this)
  auren doctor                # what works on this machine, and how to fix what doesn't
  auren config                # settings (~/.config/auren/config.toml): show, get, set, edit
  auren voice-model           # download the voice model (install.sh runs this)
  auren update                # check for a newer version, and install it if you say so
  auren browser               # the browser tools: status, setup, which browser to use
  auren chat                  # type requests, see tool calls and the reply
  auren ask "open whatsapp"   # one request

macOS only:
  auren mascot                # the mascot and the menu bar icon (the com.auren.mascot launch agent runs this)
  auren service install|start|stop|restart|status   # the launch agents (install.sh sets them up)
  auren permissions           # ask for the Microphone, Accessibility and Screen Recording permissions
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


def handle(brain, text: str, replied=lambda text: None) -> None:
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
                replied(ev[1])
            elif ev[0] == "done":
                info = ev[1]
                print(f"{DIM}  ({info['seconds']}s" + (f", context {info['context_tokens']} tokens" if info.get('context_tokens') else "")
                      + (f", {info['brain']}" if info.get('brain') else "") + f"){END}")
    except UsageLimit as e:
        print(f"{RED}usage limit reached on the backup brain too ({e}).{END}")
    except BrainError as e:
        print(f"{RED}brain error: {e}{END}")


def _permissions() -> int:
    """Have the daemon ask macOS for its permissions (the dialogs then name it, not this terminal)."""
    from .ctl import send
    try:
        print(send("permissions", timeout=30))
    except (FileNotFoundError, ConnectionRefusedError):
        print("Auren isn't running: start it with `auren service start`, then run this again")
        return 1
    print("Turn Auren on in each Settings page that opened, then run `auren service restart`.")
    return 0


def _service(args: list[str]) -> int:
    from .mac import service
    what = args[0] if args else "status"
    names = [n for n in args[1:] if n in service.LABELS] or list(service.LABELS)
    if what == "install":
        for n in names:
            print("wrote", service.install(n, os.environ.get("AUREN_PATH")))
        return 0
    if what in ("start", "stop", "restart"):
        ok = all(getattr(service, what)(n) for n in names)
        return 0 if ok else 1
    if what == "status":
        for n in names:
            state = "running" if service.running(n) else "loaded" if service.loaded(n) else \
                "installed" if service.plist_path(n).exists() else "not installed"
            print(f"{service.LABELS[n]}: {state}")
        return 0
    if what == "uninstall":
        for n in names:
            service.uninstall(n)
        return 0
    print("auren service install|start|stop|restart|status|uninstall [daemon|mascot]")
    return 2


def main() -> None:
    p = argparse.ArgumentParser(prog="auren", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("chat")
    sub.add_parser("doctor")
    sub.add_parser("config").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("voice-model")
    sub.add_parser("vocab").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("update").add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("browser", add_help=False).add_argument("args", nargs=argparse.REMAINDER)
    sub.add_parser("mascot")
    sub.add_parser("permissions")
    sub.add_parser("service").add_argument("args", nargs=argparse.REMAINDER)
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
    if a.cmd in ("mascot", "permissions", "service"):
        if sys.platform != "darwin":
            print(f"auren {a.cmd} is for macOS; on Omarchy the mascot, permissions and services are set up by install.sh")
            return 2
        if a.cmd == "mascot":
            from .mac.mascot import main as mascot_main
            return mascot_main()
        if a.cmd == "permissions":
            return _permissions()
        return _service(a.args)
    if a.cmd == "doctor":
        from .doctor import main as doctor_main
        return doctor_main()
    if a.cmd == "config":
        from .settings import main as config_main
        return config_main(a.args)
    if a.cmd == "vocab":
        from .vocab import main as vocab_main
        return vocab_main(a.args)
    if a.cmd == "browser":
        from .browser_cli import main as browser_main
        return browser_main(a.args)
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
    # This process runs its own tool runtime and session: confirmations and turns are its own.
    from .config import RUNTIME_DIR
    from .tools import Runtime
    session = f"chat-{os.getpid()}"
    runtime = Runtime(RUNTIME_DIR / f"tools-{os.getpid()}.sock")
    runtime.start()
    from .settings import load
    s = load()
    runtime.set_level(s["safety"])
    if a.backup:
        brain = CodexBrain(runtime, session, model=s["brain.codex_model"], effort=s["brain.codex_effort"])
    else:
        brain = Brain(runtime, session, mode=s["brain"], effort=a.effort or s["effort"],
                      claude_model=a.model or s["brain.claude_model"], codex_model=s["brain.codex_model"],
                      codex_effort=s["brain.codex_effort"], think=not a.no_think)
    try:
        if a.cmd == "ask":
            runtime.new_turn(session, said=" ".join(a.text))
            handle(brain, " ".join(a.text), lambda t: runtime.replied(session, t))
        else:
            print(f"{DIM}auren chat: type a request (Ctrl-D to quit){END}")
            while True:
                try:
                    text = input("you: ").strip()
                except EOFError:
                    break
                if text:
                    runtime.new_turn(session, said=text)
                    handle(brain, text, lambda t: runtime.replied(session, t))
    finally:
        brain.stop()
        runtime.stop()


if __name__ == "__main__":
    sys.exit(main())
