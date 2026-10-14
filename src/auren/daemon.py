"""The auren daemon: push-to-talk → ears → brain → voice.

    auren daemon            # run in the foreground (later: a systemd user service)
    auren-ctl talk          # Super+A: start listening; press again to stop and send
    auren-ctl talk-fresh    # Super+Shift+A: the same, in a fresh conversation
    auren-ctl start|stop    # push-to-talk (press / release), used by holding the mascot
    auren-ctl ask <text>    # a typed request through the same pipeline (spoken reply)
    auren-ctl say <text> | cancel | status | quit
    auren-ctl forget        # a fresh conversation (also by voice: "start fresh", "forget everything")
    auren-ctl voice on|off|toggle     # spoken replies (off: replies only in the mascot's bubble)
    auren-ctl voice-name F1..F5|M1..M5 [sample] # which Supertonic voice (says the sample, or a short default one)
    auren-ctl tts speed 1.1 | steps 5 | volume 0.8 | pause 0.2 | reset   # how the voice speaks
    auren-ctl brain auto|claude|codex # which brain answers (saved in ~/.config/auren/config.toml)
    auren-ctl effort low|medium|high|xhigh|max   # Claude's effort (restarts it: a fresh conversation)
    auren-ctl reload                  # re-read config.toml and the vocabulary (auren config and auren vocab send this)

Commands arrive on a Unix socket ($XDG_RUNTIME_DIR/auren/auren.sock). The current
state (idle, listening, thinking, working, speaking, error) is written to
$XDG_RUNTIME_DIR/auren/state.json for the mascot and the bar plugin (with the
live transcript, what auren is doing, and the sentence being spoken), and the
loudness to $XDG_RUNTIME_DIR/auren/level. When the mascot isn't running, the
Omarchy OSD shows short messages instead. Every exchange is logged to
~/.local/state/auren/history.jsonl.
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import logging
import os
import re
import time

from . import notice
from .brain import Brain, BrainError, Interrupted
from .config import LOCK, RUNTIME_DIR, SOCKET, STATE_DIR, STATE_FILE, TOOLS_SOCKET
from .ears import Listening, vocabulary
from .live import Level, describe, notice_kind
from .profile import Profile
from .safety import TOKEN_TTL
from .settings import load as load_settings
from .settings import save as save_settings
from .tools import Runtime
from .voice import TUNING, tuning

SESSION = "daemon"          # the daemon's tool session (an `auren chat` has its own)
log = logging.getLogger("auren")
MIN_HOLD = 0.35          # shorter key taps are ignored
MAX_LISTEN = 120         # seconds; listening stops by itself (the speech service bills per second)
# Said on its own, these start a fresh conversation instead of going to the brain.
FORGET = re.compile(r"^\W*(?:(?:ok(?:ay)?|hey|auren)\W+)*(?:start (?:fresh|over|a new conversation)|new conversation|"
                    r"forget (?:everything|all that|it all|our conversation)|clear (?:your|the) memory|reset (?:yourself|the conversation))"
                    r"\W*(?:please)?\W*$", re.I)


class Daemon:
    def __init__(self, speak: bool = True):
        self.settings = load_settings()
        self.profile = Profile()
        self.tools = Runtime(TOOLS_SOCKET)           # serving from serve() on
        s = self.settings
        self.brain = Brain(self.tools, SESSION, mode=s["brain"], effort=s["effort"], claude_model=s["brain.claude_model"],
                           codex_model=s["brain.codex_model"], codex_effort=s["brain.codex_effort"])
        self.voice = None
        self.speak_enabled = speak                    # False: started with --quiet, the voice isn't even loaded
        self.vocab = vocabulary(self.profile)
        self.listening: Listening | None = None
        self.state, self.detail = "starting", ""
        self.busy = asyncio.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.level = Level()

    # --- state ---------------------------------------------------------------------

    def set_state(self, state: str, detail: str = "", **extra) -> None:
        self.state, self.detail = state, detail
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps({"state": state, "detail": detail, "since": time.time(), "pid": os.getpid(),
                                   "brain": "codex" if self.brain.using_backup else "claude",
                                   "mode": self.brain.mode, "effort": self.brain.primary.effort, "voice": self.voice_on,
                                   "voiceName": self.voice.name if self.voice else self.settings["voiceName"],
                                   "voices": self.voice.names if self.voice else [],
                                   "tts": tuning(self.settings), **extra}))
        tmp.replace(STATE_FILE)
        if state != "listening":           # partial transcripts would flood the log
            log.info("state %s %s", state, detail[:120])

    @property
    def voice_on(self) -> bool:
        return bool(self.voice and self.speak_enabled and self.settings["voice"])

    def _refresh_state(self) -> None:
        """A setting changed: rewrite the state file so the mascot and the bar see it."""
        try:
            extra = {k: v for k, v in json.loads(STATE_FILE.read_text()).items()
                     if k not in ("state", "detail", "since", "pid", "brain", "mode", "effort", "voice", "voiceName",
                                  "voices", "tts")}
        except (OSError, json.JSONDecodeError):
            extra = {}
        self.set_state(self.state, self.detail, **extra)

    def _from_thread(self, state: str, detail: str = "", **extra) -> None:
        self.loop.call_soon_threadsafe(lambda: self.set_state(state, detail, **extra))

    # --- warm-up ------------------------------------------------------------------------

    async def warm_up(self) -> None:
        """Load the voice and start the brain (Claude's process; none in codex mode). Neither failing stops
        the daemon: without the voice, replies show in the mascot's bubble; a brain that didn't start is
        retried on the first request."""
        t = time.perf_counter()
        problems = []
        if self.speak_enabled:
            try:
                from .voice import Voice
                self.voice = await asyncio.to_thread(Voice, on_level=self.level.set, on_sentence=self._caption)
                self.voice.tune(self.settings)
                try:
                    self.voice.set_voice(self.settings["voiceName"])
                except ValueError as e:
                    log.warning("%s", e)
            except Exception as e:  # noqa: BLE001 - run text-only rather than not at all
                log.error("voice didn't load, replies are text only: %s", e)
                problems.append(f"voice off ({str(e)[:80]})")
        try:
            await asyncio.to_thread(self.brain.warm)
        except Exception as e:  # noqa: BLE001
            log.error("brain didn't start: %s", e)
            problems.append(f"brain didn't start ({str(e)[:80]})")
        log.info("ready in %.1fs (vocabulary %d terms)", time.perf_counter() - t, len(self.vocab))
        self.set_state("idle", "; ".join(problems))

    # --- commands -----------------------------------------------------------------------

    async def command(self, line: str) -> str:
        cmd, _, arg = line.strip().partition(" ")
        if cmd in ("talk", "talk-fresh") and not self.listening and self.state in ("thinking", "working"):
            await self.cancel()                      # the talk key stops an answer in progress
            return "stopped"
        if cmd == "talk":
            return await (self.release() if self.listening else self.press())
        if cmd == "talk-fresh":
            return await (self.release() if self.listening else self.press(fresh=True))
        if cmd == "start":
            return await self.press()
        if cmd == "stop":
            return await self.release()
        if cmd == "voice-name":
            if not self.voice:
                return "the voice isn't loaded"
            name, _, sample = arg.strip().partition(" ")
            try:
                self.voice.set_voice(name)
            except ValueError as e:
                return str(e)
            self._save({"voiceName": self.voice.name})
            self._refresh_state()
            if self.state == "idle":
                asyncio.create_task(self.say(sample.strip() or f"Hi, this is voice {self.voice.name}."))
            return "voice " + self.voice.name
        if cmd == "tts":
            return self._tts(arg.split())
        if cmd == "cancel":
            await self.cancel()
            return "cancelled"
        if cmd == "ask" and arg:
            asyncio.create_task(self.respond(arg, source="typed"))
            return "asked"
        if cmd == "say" and arg:
            asyncio.create_task(self.say(arg))
            return "saying"
        if cmd == "forget":
            asyncio.create_task(self.forget())
            return "forgetting"
        if cmd == "voice":
            want = {"on": True, "off": False, "toggle": not self.settings["voice"]}.get(arg.strip())
            if want is None:
                return "usage: voice on|off|toggle"
            self._save({"voice": want})
            if not want and self.voice:
                self.voice.stop()
            self._refresh_state()
            return "voice " + ("on" if want else "off")
        if cmd == "brain":
            mode = arg.strip()
            if mode not in Brain.MODES:
                return "usage: brain auto|claude|codex"
            self.brain.mode = mode
            self._save({"brain": mode})
            asyncio.create_task(self._warm())
            self._refresh_state()
            return "brain " + mode
        if cmd == "effort":
            level = arg.strip()
            if level not in Brain.EFFORTS:
                return "usage: effort " + "|".join(Brain.EFFORTS)
            self._save({"effort": level})
            asyncio.create_task(self._set_claude(effort=level))
            return "effort " + level
        if cmd == "status":
            return json.dumps({"state": self.state, "detail": self.detail,
                               "brain": "codex" if self.brain.using_backup else "claude",
                               "mode": self.brain.mode, "effort": self.brain.primary.effort,
                               "voice": self.voice_on})
        if cmd == "reload":
            return self._reload()
        if cmd == "quit":
            asyncio.get_running_loop().call_later(0.1, self.loop.stop)
            return "bye"
        return f"unknown command: {cmd}"

    def _save(self, changes: dict) -> None:
        """Use these settings now and write only them to config.toml: edits made to the file since the
        last reload stay as they are."""
        self.settings.update(changes)
        save_settings(changes)

    def _reload(self) -> str:
        """Apply config.toml and the vocabulary as they are now (after `auren config set`, `auren vocab add`
        or an edit). New words are used from the next press."""
        vocab = vocabulary(self.profile)
        vocab_changed, self.vocab = vocab != self.vocab, vocab
        new = load_settings()
        changed = [k for k in new if new[k] != self.settings.get(k)]
        if not changed:
            return "Auren picked up the new words" if vocab_changed else "no changes"
        old, self.settings = self.settings, new
        if self.voice:
            if new["voiceName"] != old["voiceName"]:
                try:
                    self.voice.set_voice(new["voiceName"])
                except ValueError as e:
                    log.warning("config: %s", e)
                    self.settings["voiceName"] = self.voice.name
            self.voice.tune(new)
            if old["voice"] and not new["voice"]:
                self.voice.stop()
        self.brain.mode = new["brain"]
        self.brain.set_codex(new["brain.codex_model"], new["brain.codex_effort"])
        claude = {}
        if new["effort"] != old["effort"]:
            claude["effort"] = new["effort"]
        if new["brain.claude_model"] != old["brain.claude_model"]:
            claude["model"] = new["brain.claude_model"]
        if claude:
            asyncio.create_task(self._set_claude(**claude))
        elif new["brain"] != old["brain"]:
            asyncio.create_task(self._warm())
        self._refresh_state()
        return "Auren picked it up"

    def _tts(self, words: list[str]) -> str:
        """tts speed 1.1 | steps 5 | volume 0.8 | pause 0.2 (several at once is fine) | reset"""
        if words == ["reset"]:
            values = tuning()
        else:
            if not words or len(words) % 2 or any(k not in TUNING for k in words[::2]):
                return "usage: tts speed 0.7..1.6 | steps 2..12 | volume 0.1..1 | pause 0..1 | reset"
            values = tuning({**self.settings, **dict(zip(words[::2], words[1::2], strict=True))})
        self._save(values)
        if self.voice:
            self.voice.tune(values)
        self._refresh_state()
        return "tts " + " ".join(f"{k} {v}" for k, v in values.items())

    async def press(self, fresh: bool = False) -> str:
        if self.listening:
            return "already listening"
        if self.voice:
            self.voice.stop()            # talking over auren interrupts it
        if fresh:                        # restart the brain while the user speaks; the answer waits for it
            asyncio.create_task(self._forget_quietly())
        self.listening = Listening(self.vocab, on_partial=lambda t: self.set_state("listening", t, fresh=fresh),
                                   on_level=self.level.set)
        session = self.listening
        try:
            await self.listening.start()
        except Exception as e:  # noqa: BLE001
            self.listening = None
            self.set_state("error", str(e))
            notice.notify("Auren couldn't start listening", str(e), "error")
            return f"error: {e}"
        self.set_state("listening", "", fresh=fresh)
        notice.show("Listening (fresh start)…" if fresh else "Listening…", "listening", 1200)
        asyncio.get_running_loop().call_later(MAX_LISTEN, lambda: asyncio.create_task(self._listen_timeout(session)))
        return "listening"

    async def _listen_timeout(self, session: Listening) -> None:
        if self.listening is session:
            log.info("listening stopped after %ss", MAX_LISTEN)
            await self.release()

    async def _set_claude(self, effort: str | None = None, model: str | None = None) -> None:
        what = " and ".join(x for x in (effort and f"{effort} effort", model and f"model {model}") if x)
        async with self.busy:              # after the current answer, if there is one
            self.set_state("thinking", f"Switching to {what}…")
            await asyncio.to_thread(self.brain.set_claude, effort, model)
            log.info("claude: %s (fresh conversation)", what)
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": f"({what})",
                           "tools": [], "brain": "claude"})
        self.idle()

    async def _warm(self) -> None:
        """After a mode change: start Claude if the mode now uses it, stop it if not (between answers)."""
        async with self.busy:
            try:
                await asyncio.to_thread(self.brain.warm)
            except Exception as e:  # noqa: BLE001 - retried on the next request
                log.error("brain didn't start: %s", e)

    async def _forget_quietly(self) -> None:
        async with self.busy:
            await asyncio.to_thread(self.brain.forget)
            log.info("fresh conversation (Super+Shift+A)")
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": "(fresh conversation)",
                           "tools": [], "brain": "claude"})

    async def release(self) -> str:
        session, self.listening = self.listening, None
        if session is None:
            return "not listening"
        self.level.set(0)
        if session.held_seconds < MIN_HOLD:
            await session.cancel()
            self.set_state("idle", "key tap ignored")
            return "too short"
        t = time.perf_counter()
        text = await session.finish()
        heard_in = time.perf_counter() - t
        if session.error and not text:
            self.set_state("error", session.error)
            notice.notify("Auren couldn't hear you", session.error, "error")
            return "error"
        if not text:
            self.set_state("idle", "heard nothing")
            notice.show("Didn't catch that", "unheard")
            return "heard nothing"
        log.info("heard (%.2fs after release): %s", heard_in, text)
        asyncio.create_task(self.respond(text, source="voice", heard_s=heard_in))
        return "heard: " + text

    async def cancel(self) -> None:
        if self.listening:
            await self.listening.cancel()
            self.listening = None
        self.level.set(0)
        if self.state in ("thinking", "working"):     # an answer in progress: stop the brain and its tools
            self.tools.cancel(SESSION)
            await asyncio.to_thread(self.brain.interrupt)
            notice.show("Stopped", "stopped")
        if self.voice:
            self.voice.stop()
        self.set_state("idle", "cancelled")

    # --- answering ------------------------------------------------------------------------

    async def forget(self, say: bool = True) -> None:
        async with self.busy:
            self.set_state("thinking", "Starting fresh…")
            await asyncio.to_thread(self.brain.forget)
            log.info("fresh conversation")
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": "(fresh conversation)",
                           "tools": [], "brain": "claude"})
        if say:
            await self.say("Okay, fresh start. I've forgotten our conversation.")
        else:
            self.idle()

    async def respond(self, text: str, source: str, heard_s: float | None = None) -> None:
        if FORGET.match(text):
            await self.forget()
            return
        async with self.busy:                          # one request at a time; later ones wait
            t0 = time.perf_counter()
            # Counted here, before "thinking" shows: a cancel from now on sticks (the worker can't undo it).
            self.tools.new_turn(SESSION)              # once per user message, whichever brain answers
            self.set_state("thinking", text, transcript=text)
            notice.show("Thinking…", "thinking")
            tools: list[str] = []
            reply = ""

            def progress(state: str, detail: str, **extra) -> None:
                # Checked on the loop, where cancel() runs: once stopped, events still in the stream
                # mustn't bring back "working" over idle, or over a new listen.
                if not self.tools.cancelled(SESSION):
                    self.set_state(state, detail, **extra)

            def run() -> str:
                said = ""
                for ev in self.brain.ask(text):
                    if ev[0] == "tool":
                        tools.append(ev[1])
                        doing, mouse = describe(ev[1], ev[2] if len(ev) > 2 else {})
                        self.loop.call_soon_threadsafe(lambda d=doing, t=ev[1], m=mouse: progress(
                            "working", d, tool=t, mouse=m, transcript=text))
                        if (kind := notice_kind(ev[1])) and not self.tools.cancelled(SESSION):
                            notice.show(doing, kind, always=True)      # actions show on the OSD with the bubble up too
                    elif ev[0] == "switch":
                        self.loop.call_soon_threadsafe(lambda d=ev[1]: progress("thinking", d))
                    elif ev[0] == "say":
                        said = ev[1]
                return said

            try:
                reply = await asyncio.to_thread(run)
            except Interrupted:
                log.info("cancelled while %s", "working" if tools else "thinking")
                self._history({"at": time.time(), "source": source, "transcript": text, "reply": "(cancelled)",
                               "tools": tools, "heard_s": heard_s, "think_s": round(time.perf_counter() - t0, 2),
                               "brain": "codex" if self.brain.using_backup else "claude"})
                if not self.listening:            # the talk key may already have started the next listen
                    self.idle("cancelled")
                return
            except BrainError as e:
                reply = "Sorry, my brain hit an error: " + str(e)[:120]
                log.error("brain error: %s", e)
            think_s = time.perf_counter() - t0
            log.info("reply (%.2fs, tools %s): %s", think_s, tools, reply)
            self._history({"at": time.time(), "source": source, "transcript": text, "reply": reply, "tools": tools,
                           "heard_s": heard_s, "think_s": round(think_s, 2),
                           "brain": "codex" if self.brain.using_backup else "claude"})
            if reply:
                await self.say(reply)
            else:
                self.idle()

    async def say(self, text: str) -> None:
        if not self.voice_on:
            notice.notify("Auren", text)
            self.set_state("speaking", text, reply=text, silent=True)
            await asyncio.sleep(min(8.0, 1.5 + len(text) / 18))    # time to read it in the mascot's bubble
            if self.state == "speaking":
                self.idle()
            return
        self._reply = text
        self.set_state("speaking", "", reply=text)
        finished = await asyncio.to_thread(self.voice.speak, text)
        if self.state == "speaking":
            self.idle("" if finished else "interrupted")

    def _caption(self, sentence: str) -> None:
        """The voice started a new sentence: show it in the mascot's bubble."""
        self._from_thread("speaking", sentence, reply=getattr(self, "_reply", ""))

    def idle(self, detail: str = "") -> None:
        """Back to idle; if the brain asked a yes/no question that's still open, say so for the mascot."""
        p = self.tools.awaiting(SESSION)
        if p:
            self.set_state("idle", detail, awaiting=True, question=p.question, until=p.at + TOKEN_TTL)
        else:
            self.set_state("idle", detail)

    def _history(self, record: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "history.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- server -------------------------------------------------------------------------------

    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        if SOCKET.exists():                   # left over from a crash: we hold the lock, so it isn't in use
            SOCKET.unlink()

        async def client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            line = (await reader.readline()).decode().strip()
            try:
                reply = await self.command(line) if line else "empty"
            except Exception as e:  # noqa: BLE001
                log.exception("command failed")
                reply = f"error: {e}"
            writer.write((reply + "\n").encode())
            await writer.drain()
            writer.close()

        self.tools.start()
        server = await asyncio.start_unix_server(client, path=str(SOCKET))
        os.chmod(SOCKET, 0o600)
        self.set_state("starting")
        asyncio.create_task(self.warm_up())
        log.info("listening on %s", SOCKET)
        async with server:
            await server.serve_forever()


def take_lock():
    """Only one daemon at a time: hold an exclusive lock for as long as this process runs."""
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    f = open(LOCK, "a+")  # noqa: SIM115 - kept open on purpose: closing it releases the lock. "a+": don't wipe the holder's pid
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return None
    f.truncate(0)
    f.write(str(os.getpid()))
    f.flush()
    return f


def main(speak: bool = True) -> int:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(STATE_DIR / "daemon.log")])
    lock = take_lock()
    if lock is None:
        log.error("another auren daemon is already running (%s)", LOCK)
        return 0                              # not a failure: systemd shouldn't keep restarting it
    d = Daemon(speak=speak)
    try:
        asyncio.run(d.serve())
    except (KeyboardInterrupt, RuntimeError):
        pass
    finally:
        try:
            d.set_state("off")
        except Exception:  # noqa: BLE001
            pass
        d.brain.stop()
        d.tools.stop()
        if SOCKET.exists():
            SOCKET.unlink()
        lock.close()
    return 0
