"""auren selftest: check this Mac's Auren from end to end and write a report to send back.

    auren selftest            # automatic checks, then a few that need you (hear a sentence, press the talk key)
    auren selftest --quick    # only the automatic checks (nothing is said out loud)

The checks that need Auren's permissions (the microphone, the screen, Accessibility) run inside the
daemon (the `selftest` socket command), because macOS gives those permissions to Auren.app, not to
Terminal. The rest runs here. The report (~/auren-selftest-<time>.txt) has every result, the
doctor's checks, the settings and the last lines of both logs. It holds what was said to Auren lately
(the logs), and no keys.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import inspect
import io
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ..config import RUNTIME_DIR, STATE_DIR, secret

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
SYMBOL = {OK: "\033[32m✓\033[0m", WARN: "\033[33m!\033[0m", FAIL: "\033[31m✗\033[0m", SKIP: "\033[2m-\033[0m"}
PLAIN = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "skip"}
HISTORY = STATE_DIR / "history.jsonl"
LOG_LINES = 80


def result(name: str, status: str, detail: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail}


# --- inside the daemon (Auren.app's permissions) ---------------------------------------------------

async def inside(daemon) -> list[dict]:
    """The checks that need Auren's permissions, run in the daemon. Each one reports instead of raising."""
    out: list[dict] = []
    loop = asyncio.get_running_loop()

    async def run(name: str, fn, *args) -> None:
        try:
            r = fn(*args)
            r = await r if inspect.isawaitable(r) else r
        except Exception as e:  # noqa: BLE001 - a broken check is a result, not a crash
            r = [result(name, FAIL, f"{type(e).__name__}: {e}"[:300])]
        out.extend(r if isinstance(r, list) else [r])

    def in_thread(fn):
        return lambda *a: loop.run_in_executor(None, fn, *a)

    await run("permissions", in_thread(_permissions), daemon)
    await run("displays", in_thread(_displays))
    await run("mascot window", in_thread(_mascot_window))
    await run("microphone", _microphone, daemon)
    await run("speech service", _speech_service)
    await run("voice", _voice, daemon)
    await run("screen capture", in_thread(_screen))
    await run("windows", in_thread(_windows))
    await run("clipboard", in_thread(_clipboard))
    await run("browser folders", in_thread(_browser_folders))
    return out


def _browser_folders() -> list[dict]:
    """Whether Auren may read the browsers' folders (profiles, the extension, bookmarks, downloads)."""
    from ..hands import browsers, obu
    out = []
    for kind in browsers.KINDS:
        if browsers.installed(kind):
            ok = obu.readable(kind)
            out.append(result(f"{kind.name} folder", OK if ok else WARN, "readable" if ok else
                              "macOS doesn't let Auren read it (Full Disk Access): the profiles, bookmarks and "
                              "downloads can't be seen"))
    return out


def _permissions(daemon) -> list[dict]:
    from . import permissions
    s = permissions.status()
    out = [result("Microphone permission", OK if s.get("microphone") == "granted" else FAIL, str(s.get("microphone"))),
           result("Accessibility permission", OK if s.get("accessibility") else FAIL,
                  "allowed" if s.get("accessibility") else "not allowed"),
           result("Screen Recording permission", OK if s.get("screen") else WARN,
                  "allowed" if s.get("screen") else "not allowed (look_at_screen and window titles need it)"),
           result("permissions belong to", OK if "Auren.app" in permissions.program() else WARN, permissions.program())]
    keys = bool(getattr(daemon, "_hotkeys", None))
    out.append(result("talk keys listening", OK if keys else FAIL,
                      f"{daemon.settings['keys.talk']} / {daemon.settings['keys.talk_fresh']}" if keys
                      else "the key listener isn't running (Accessibility?)"))
    return out


def _display_rects() -> list[tuple[float, float, float, float]]:
    import Quartz as Q
    err, ids, n = Q.CGGetActiveDisplayList(16, None, None)
    rects = []
    for d in ids[:n]:
        b = Q.CGDisplayBounds(d)
        rects.append((b.origin.x, b.origin.y, b.size.width, b.size.height))
    return rects


def _displays() -> dict:
    rects = _display_rects()
    text = "; ".join(f"{int(w)}x{int(h)} at {int(x)},{int(y)}" for x, y, w, h in rects)
    return result("displays", OK if rects else FAIL, f"{len(rects)}: {text}" if rects else "none found")


def _mascot_window() -> dict:
    """Whether the mascot's window is on a screen: it once sat at 1760,1769, below every screen."""
    import Quartz as Q
    try:
        pid = int((RUNTIME_DIR / "mascot.pid").read_text().strip())
    except (OSError, ValueError):
        return result("mascot window", FAIL, "the mascot isn't running (no mascot.pid)")
    infos = Q.CGWindowListCopyWindowInfo(Q.kCGWindowListOptionAll, Q.kCGNullWindowID) or []
    wins = [i for i in infos if int(i.get("kCGWindowOwnerPID", 0)) == pid and int(i.get("kCGWindowLayer", 0)) >= 0]
    if not wins:
        return result("mascot window", FAIL, f"the mascot (pid {pid}) has no windows")
    rects = _display_rects()
    seen = []
    for i in wins:
        b = i.get("kCGWindowBounds") or {}
        x, y, w, h = (float(b.get(k, 0)) for k in ("X", "Y", "Width", "Height"))
        if w < 60 or h < 60:                 # the menu bar icon, and the bubble while it's empty
            continue
        cx, cy = x + w / 2, y + h / 2
        on = any(dx <= cx < dx + dw and dy <= cy < dy + dh for dx, dy, dw, dh in rects)
        shown = bool(i.get("kCGWindowIsOnscreen"))
        seen.append((on and shown, f"{int(w)}x{int(h)} at {int(x)},{int(y)}"
                     + ("" if shown else " (hidden)") + ("" if on else " (off every screen)")))
    if not seen:
        return result("mascot window", FAIL, "no window big enough to be the robot")
    good = [t for ok, t in seen if ok]
    return result("mascot window", OK if good else FAIL, "; ".join(t for _, t in seen))


async def _microphone(daemon) -> list[dict]:
    import numpy as np
    import sounddevice as sd

    from ..live import loudness
    from .audio import Mic
    name = _device_name(sd, "input")
    if getattr(daemon, "listening", None):
        return [result("microphone", SKIP, "Auren was listening")]
    mic = Mic(16000)
    peak, total, chunks, nonzero = 0.0, 0, 0, False
    end = time.monotonic() + 3
    try:
        while time.monotonic() < end:
            chunk = await asyncio.wait_for(mic.read(1600), 2)
            if not chunk:
                break
            samples = np.frombuffer(chunk[: len(chunk) // 2 * 2], dtype=np.int16)
            nonzero = nonzero or bool(samples.any())
            peak = max(peak, loudness(samples / 32768.0))
            total += len(samples)
            chunks += 1
    except TimeoutError:
        pass
    finally:
        mic.kill()
    if not chunks:
        return [result("microphone", FAIL, f"{name}: no audio came in for 3 s")]
    if not nonzero:
        return [result("microphone", FAIL, f"{name}: only silence (all zeros): the Microphone permission, or a muted "
                       "device")]
    return [result("microphone", OK, f"{name}: {total / 16000:.1f} s recorded, loudest {peak:.2f}")]


def _device_name(sd, kind: str) -> str:
    try:
        return str(sd.query_devices(kind=kind)["name"])
    except Exception:  # noqa: BLE001
        return f"(no default {kind} device)"


async def _speech_service() -> dict:
    """Connect to AssemblyAI with the key, as a talk turn does, and close again (no audio sent)."""
    from urllib.parse import urlencode

    import websockets

    from ..ears import URL, connection_params
    key = secret("ASSEMBLYAI_API_KEY")
    if not key:
        return result("speech service", FAIL, "no AssemblyAI key")
    t = time.perf_counter()
    try:
        async with websockets.connect(f"{URL}?{urlencode(connection_params([]))}",
                                      additional_headers={"Authorization": key}, open_timeout=10) as ws:
            first = json.loads(await asyncio.wait_for(ws.recv(), 10))
            await ws.send(json.dumps({"type": "Terminate"}))
    except Exception as e:  # noqa: BLE001
        return result("speech service", FAIL, f"AssemblyAI: {type(e).__name__}: {e}"[:300])
    took = time.perf_counter() - t
    if first.get("type") != "Begin":
        return result("speech service", FAIL, f"AssemblyAI answered {str(first)[:200]}")
    return result("speech service", OK if took < 3 else WARN, f"AssemblyAI connected in {took:.1f} s")


async def _voice(daemon) -> list[dict]:
    import sounddevice as sd
    out = _device_name(sd, "output")
    v = getattr(daemon, "voice", None)
    if v is None:
        return [result("voice", FAIL, "the voice model didn't load (see the daemon log)"),
                result("speaker", OK, out)]
    return [result("voice", OK, f"loaded, voice {daemon.settings.get('voiceName')}, "
                   f"{'on' if getattr(daemon, 'voice_on', True) else 'turned off'}"),
            result("speaker", OK, out)]


def _screen() -> dict:
    from . import windows
    from .screen import _capture
    d = windows.main_display()
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        t = time.perf_counter()
        _capture(d, f.name)
        size = Path(f.name).stat().st_size
    return result("screen capture", OK, f"{d['width']}x{d['height']}, {size // 1024} KB in {time.perf_counter() - t:.1f} s")


def _windows() -> list[dict]:
    from . import windows
    ws = windows.list_windows()
    apps = sorted({w["class"] for w in ws if w["class"]})
    titled = sum(bool(w["title"]) for w in ws)
    front = windows.active_window()
    return [result("windows", OK if ws else WARN, f"{len(ws)} on screen ({titled} with titles): {', '.join(apps)[:300]}"),
            result("front window", OK if front else WARN,
                   f"{front['class']}: {front['title'][:80]}" if front else "none found")]


def _clipboard() -> dict:
    from . import clipboard
    saved = clipboard.save()
    mark = f"auren selftest {time.time():.0f}"
    try:
        clipboard.copy(mark.encode(), "text/plain")
        back = clipboard.text()
    finally:
        clipboard.restore(saved, mark.encode())
    return result("clipboard", OK if back == mark else FAIL, "copied and read back" if back == mark
                  else f"read back {back[:60]!r}")


# --- here, in Terminal ------------------------------------------------------------------------------

def _sh(*cmd: str, timeout: float = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _about() -> list[str]:
    root = Path(__file__).resolve().parents[3]
    commit = _sh("git", "-C", str(root), "log", "-1", "--format=%h %s (%cr)") if (root / ".git").exists() else ""
    from ..update import _version
    return [f"date: {time.strftime('%Y-%m-%d %H:%M:%S %Z')}",
            f"macOS: {platform.mac_ver()[0]} on {_sh('sysctl', '-n', 'hw.model')} ({platform.machine()})",
            f"auren: {_version()}" + (f", checkout {root}: {commit}" if commit else f", {root}"),
            f"python: {sys.version.split()[0]} ({sys.executable})"]


def _doctor() -> list[dict]:
    from .. import doctor
    return [result(f"{c.group}: {c.name}", c.status, c.detail + (f" (fix: {c.fix})" if c.fix and c.status != OK else ""))
            for c in doctor.checks()]


def _daemon() -> tuple[list[dict], dict | None]:
    from ..ctl import send
    try:
        st = json.loads(send("status", timeout=10))
    except (OSError, TimeoutError, ValueError) as e:
        return [result("daemon", FAIL, f"not answering ({type(e).__name__}): auren service restart")], None
    out = [result("daemon", OK, f"{st['state']}" + (f" ({st['detail']})" if st.get("detail") else "")
                  + f", brain {st['brain']} (mode {st['mode']}), safety {st['safety']}")]
    try:
        reply = send("selftest", timeout=90)
        out += json.loads(reply)
    except ValueError:
        out.append(result("checks in the daemon", FAIL, f"the daemon answered {reply[:200]!r} (an old version? "
                          "auren service restart)"))
    except (OSError, TimeoutError) as e:
        out.append(result("checks in the daemon", FAIL, f"{type(e).__name__}: {e}"))
    return out, st


def _brain() -> dict:
    """One short request to the brain, through `auren ask` (its own brain, not the daemon's conversation)."""
    t = time.perf_counter()
    try:
        p = subprocess.run([sys.executable, "-m", "auren.cli", "ask", "Reply with only the word: ready"],
                           capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return result("brain", FAIL, "no answer in 120 s")
    took = time.perf_counter() - t
    text = " ".join((p.stdout + "\n" + p.stderr).split())
    good = p.returncode == 0 and "ready" in p.stdout.lower()
    return result("brain", OK if good else FAIL, f"{took:.1f} s: {text[-300:]}")


def _browser() -> dict:
    from .. import browser_cli
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        code = browser_cli.status()
    text = buf.getvalue().strip()
    connected = "connected:" in text
    last = text.splitlines()[-1] if text else ""
    status = OK if code == 0 and connected else (SKIP if "turned off" in last else WARN)
    return result("browser tools", status, " | ".join(line.strip() for line in text.splitlines()))


def _browser_details() -> list[str]:
    """What the browser check looked at, for when it's wrong: each profile's extension folder and
    preferences entry, and each live connection with the processes above it."""
    from ..hands import browsers, obu
    from .obu import _ps
    lines = []
    for kind in browsers.KINDS:
        if not browsers.installed(kind):
            continue
        base = browsers.config_dir(kind)
        try:
            inside = sorted(os.listdir(base)) if base else []
            seen = f"{len(inside)} entries: {', '.join(inside)[:300]}"
        except OSError as e:               # macOS can keep another app's folder from Terminal
            seen = f"can't list it: {e}"
        lines.append(f"{kind.name}: {base} ({seen})")
        for p in obu.profiles(kind):
            ext = p.path / "Extensions" / obu.EXTENSION_ID
            entry = {}
            for prefs in ("Secure Preferences", "Preferences"):
                with contextlib.suppress(OSError, ValueError, KeyError, TypeError):
                    entry = json.loads((p.path / prefs).read_text())["extensions"]["settings"][obu.EXTENSION_ID]
                    entry = {k: entry.get(k) for k in ("state", "disable_reasons", "from_webstore", "location", "path")
                             if k in entry} | {"in": prefs}
                    break
            storage = p.path / "Local Extension Settings" / obu.EXTENSION_ID
            lines.append(f"  {p.label()}: extension folder {'yes' if ext.is_dir() else 'no'}"
                         + (f" ({', '.join(sorted(x.name for x in ext.iterdir()))})" if ext.is_dir() else "")
                         + f", storage {'yes' if storage.is_dir() else 'no'}, prefs {entry or 'no entry'}")
    for sock in obu._sockets():
        pid = obu._listener_pid(sock)
        chain, q = [], pid
        for _ in range(6):
            q = obu._parent(q) if q else None
            if not q or q <= 1:
                break
            chain.append(f"{q} {os.path.basename(_ps(q, 'comm'))}")
        binary = obu.cli()
        lines.append(f"socket {sock}: host pid {pid}, instance {obu._instance(binary, sock) if binary else '?'}, "
                     f"above: {' < '.join(chain) or 'none'}")
    for c in obu.connections(fresh=True):
        lines.append(f"connection: {c.kind.name}, profile {c.profile.label() if c.profile else 'unknown'}, {c.socket}")
    return lines or ["no Chromium browser installed"]


def _settings() -> list[str]:
    from .. import settings
    s = settings.current()
    return [f"{k} = {v!r}" for k, v in sorted(s.items())]


def _tail(path: Path, n: int = LOG_LINES) -> list[str]:
    try:
        return path.read_text(errors="replace").splitlines()[-n:]
    except OSError as e:
        return [f"(can't read {path}: {e})"]


# --- the steps that need the person at the Mac --------------------------------------------------------

def _ask(question: str) -> str:
    try:
        return input(f"  {question} [y/n] ").strip().lower()[:1]
    except EOFError:
        return ""


def _history_len() -> int:
    try:
        return len(HISTORY.read_text().splitlines())
    except OSError:
        return 0


def _wait_history(before: int, source: str, timeout: float, wanted: str = "",
                  states: list[str] | None = None) -> dict | None:
    """The first new answer from this source (with `wanted` in what was heard, if given; else, at the end,
    any). states, if given, collects the daemon's states meanwhile (listening, thinking, …)."""
    end = time.monotonic() + timeout
    seen: list[dict] = []
    while time.monotonic() < end:
        try:
            lines = HISTORY.read_text().splitlines()[before:]
        except OSError:
            lines = []
        seen = []
        for line in lines:
            with contextlib.suppress(ValueError):
                rec = json.loads(line)
                if rec.get("source") == source:
                    seen.append(rec)
        hits = [r for r in seen if wanted.lower() in r.get("transcript", "").lower()]
        hit = next((r for r in hits if r.get("reply") != "(cancelled)"), None)    # a cancelled try: wait for another
        if hit is not None:
            return hit
        if states is not None:
            _note_state(states)
        time.sleep(0.25 if states is not None else 0.5)
    hits = [r for r in seen if wanted.lower() in r.get("transcript", "").lower()]
    return (hits or seen or [None])[0]


def _note_state(states: list[str]) -> None:
    from ..ctl import send
    try:
        now = json.loads(send("status", timeout=2)).get("state", "?")
    except (OSError, TimeoutError, ValueError):
        now = "not answering"
    if not states or states[-1] != now:
        states.append(now)


def _turn(rec: dict | None, name: str, wanted_tool: str = "") -> dict:
    if rec is None:
        return result(name, FAIL, "no answer was recorded")
    tools = rec.get("tools") or []
    detail = (f"heard {rec.get('transcript', '')!r}" + (f" in {rec['heard_s']} s" if rec.get("heard_s") else "")
              + f"; {rec.get('brain')} answered in {rec.get('think_s')} s"
              + (f" with {', '.join(tools)}" if tools else "") + f": {rec.get('reply', '')[:200]!r}")
    bad = rec.get("reply", "").startswith(("Sorry, my brain hit an error", "(cancelled)")) or not rec.get("reply")
    return result(name, FAIL if bad else (WARN if wanted_tool and wanted_tool not in tools else OK), detail)


def _by_hand(daemon_state: dict | None, keys: str) -> list[dict]:
    from ..ctl import send
    out = []
    print("\n\033[1mA few checks need you\033[0m (answer y or n; Enter skips one)")

    send("say This is Auren's voice test. If you can hear me, answer yes.", timeout=10)
    time.sleep(4)
    a = _ask("Did you hear Auren say a sentence?")
    out.append(result("you heard the voice", {"y": OK, "n": FAIL}.get(a, SKIP), {"y": "yes", "n": "no"}.get(a, "")))

    a = _ask("Can you see the robot on the screen?")
    out.append(result("you see the mascot", {"y": OK, "n": FAIL}.get(a, SKIP), {"y": "yes", "n": "no"}.get(a, "")))

    # The windows and the time are in every request's context, so this asks for something only a tool gives
    before = _history_len()
    send("ask Look at my screen and tell me in one short sentence what's on it.", timeout=10)
    print("  Asking Auren to look at the screen (it answers out loud)…")
    out.append(_turn(_wait_history(before, "typed", 120), "a typed request with a tool", "look_at_screen"))
    time.sleep(1)

    print(f"\n  Now the talk key: press {keys}, say \033[1m\"what time is it\"\033[0m, then press {keys} again.")
    print("  Waiting up to 60 s…")
    before = _history_len()
    states: list[str] = []
    rec = _wait_history(before, "voice", 60, wanted="time", states=states)
    row = _turn(rec, "talk key, microphone and speech to text")
    row["detail"] += f" (Auren went {' → '.join(states) or 'nowhere'})"
    if rec is None and "listening" not in states:
        row["detail"] = f"no key press reached Auren: it never started listening ({' → '.join(states)})"
    elif rec is not None and "time" not in rec.get("transcript", "").lower():
        row["status"] = WARN if row["status"] == OK else row["status"]
        row["detail"] = "not the words asked for: " + row["detail"]
    out.append(row)
    if rec is not None:
        time.sleep(3)
        a = _ask("Did you hear the answer?")
        out.append(result("you heard the answer", {"y": OK, "n": FAIL}.get(a, SKIP), {"y": "yes", "n": "no"}.get(a, "")))
    return out


# --- the report ------------------------------------------------------------------------------------

def _show(rows: list[dict]) -> None:
    for r in rows:
        print(f"  {SYMBOL[r['status']]} {r['name']}" + (f": {r['detail']}" if r["detail"] else ""))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="auren selftest", description=__doc__.split("\n\n")[0])
    ap.add_argument("--quick", action="store_true", help="only the automatic checks: nothing is said out loud")
    ap.add_argument("--out", help="where to write the report (default: ~/auren-selftest-<time>.txt)")
    a = ap.parse_args(argv)
    from ..settings import current, key_label

    print("\033[1mAuren self-test\033[0m: this takes about a minute.\n")
    sections: list[tuple[str, list[dict]]] = []

    def section(title: str, rows: list[dict]) -> None:
        print(f"\033[1m{title}\033[0m")
        _show(rows)
        sections.append((title, rows))

    about = _about()
    section("Install (auren doctor)", _doctor())
    print("\033[2mRunning the checks inside Auren (microphone, screen, keys)…\033[0m")
    rows, st = _daemon()
    section("Inside Auren", rows)
    print("\033[2mAsking the brain…\033[0m")
    section("Brain and browser", [_brain(), _browser()])
    if not a.quick and sys.stdin.isatty() and st is not None:
        keys = key_label(str(current().get("keys.talk") or "ctrl+alt+a"))
        rows = _by_hand(st, keys)
        print()
        section("With you", rows)

    stamp = time.strftime("%Y%m%d-%H%M")
    # The home folder, not the Desktop: macOS asks before Terminal may write there
    out = Path(a.out).expanduser() if a.out else Path.home() / f"auren-selftest-{stamp}.txt"
    all_rows = [r for _, rows in sections for r in rows]
    counts = {s: sum(r["status"] == s for r in all_rows) for s in (OK, WARN, FAIL, SKIP)}
    lines = ["Auren self-test", *about,
             f"result: {counts[FAIL]} failed, {counts[WARN]} warnings, {counts[OK]} ok, {counts[SKIP]} skipped", ""]
    for title, rows in sections:
        lines.append(f"== {title}")
        lines += [f"{PLAIN[r['status']]} {r['name']}" + (f": {r['detail']}" if r["detail"] else "") for r in rows]
        lines.append("")
    try:
        details = _browser_details()
    except Exception as e:  # noqa: BLE001 - details for debugging: never lose the report over them
        details = [f"(couldn't look: {type(e).__name__}: {e})"]
    lines += ["== browser details", *details, ""]
    lines += ["== settings (config.toml; keys aren't in it)", *_settings(), ""]
    for name in ("daemon", "mascot"):
        lines += [f"== {name} log (last {LOG_LINES} lines)", *_tail(STATE_DIR / f"{name}.out.log"), ""]
    out.write_text("\n".join(lines) + "\n")
    os.chmod(out, 0o600)

    verdict = (f"\033[31m{counts[FAIL]} failed\033[0m" if counts[FAIL] else "\033[32mall passed\033[0m")
    print(f"\n{verdict}, {counts[WARN]} warning(s). The report is \033[1m{out}\033[0m: send that file.")
    if not a.out and sys.stdin.isatty():
        _sh("open", "-R", str(out))          # show it in Finder, ready to drag into a chat
    return 1 if counts[FAIL] else 0
