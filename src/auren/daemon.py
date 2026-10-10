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
    auren-ctl brain auto|claude|codex # which brain answers (saved in ~/.config/auren/settings.json)
    auren-ctl effort low|medium|high|xhigh|max   # Claude's effort (restarts it: a fresh conversation)
    auren-ctl language en|hi          # reply language: Hindi replies are in Devanagari, spoken in Hindi

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
import json
import logging
import os
import re
import subprocess
import time

from .brain import Brain, BrainError
from .brain.prompt import LANGUAGES
from .config import RUNTIME_DIR, SETTINGS, SOCKET, STATE_DIR, STATE_FILE
from .ears import Listening, vocabulary
from .live import Level, describe
from .profile import Profile
from .safety import Confirmations
from .voice import TUNING, tuning

log = logging.getLogger("auren")
MIN_HOLD = 0.35          # shorter key taps are ignored
MAX_LISTEN = 120         # seconds; listening stops by itself (the speech service bills per second)
# Said on its own, these start a fresh conversation instead of going to the brain.
FORGET = re.compile(r"^\W*(?:(?:ok(?:ay)?|hey|auren)\W+)*(?:start (?:fresh|over|a new conversation)|new conversation|"
                    r"forget (?:everything|all that|it all|our conversation)|clear (?:your|the) memory|reset (?:yourself|the conversation))"
                    r"\W*(?:please)?\W*$", re.I)


_mascot_seen = [0.0, False]


def mascot_running() -> bool:
    """Is the mascot (a Quickshell instance running auren's mascot/) on screen? Checked at most every 3 s."""
    if time.monotonic() - _mascot_seen[0] > 3:
        found = False
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                try:
                    with open(f"/proc/{pid}/cmdline", "rb") as f:
                        if b"auren/mascot" in f.read().lower():
                            found = True
                            break
                except OSError:
                    pass
        _mascot_seen[:] = [time.monotonic(), found]
    return _mascot_seen[1]


def osd(message: str, ms: int = 1500, always: bool = False) -> None:
    """A short message on screen, only when the mascot isn't there to show it (or always)."""
    if not always and mascot_running():
        return
    subprocess.Popen(["omarchy-osd", "-m", message, "-d", str(ms)], stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)


def load_settings() -> dict:
    try:
        s = json.loads(SETTINGS.read_text())
    except (OSError, json.JSONDecodeError):
        s = {}
    return {"voice": s.get("voice", True) is not False, "brain": s.get("brain", "auto"),
            "effort": s.get("effort") if s.get("effort") in Brain.EFFORTS else "low",
            "language": s.get("language") if s.get("language") in LANGUAGES else "en",
            "voiceName": s.get("voiceName", "F2"), **tuning(s)}


def save_settings(settings: dict) -> None:
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings, indent=2) + "\n")
    tmp.replace(SETTINGS)


class Daemon:
    def __init__(self, speak: bool = True):
        self.settings = load_settings()
        self.profile = Profile()
        self.brain = Brain(mode=self.settings["brain"], effort=self.settings["effort"])
        self.brain.set_language(self.settings["language"], self.speaker)
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
                                   "language": self.settings["language"],
                                   "voiceName": self.voice.name if self.voice else self.settings["voiceName"],
                                   "voices": self.voice.names if self.voice else [],
                                   "tts": tuning(self.settings), **extra}))
        tmp.replace(STATE_FILE)
        if state != "listening":           # partial transcripts would flood the log
            log.info("state %s %s", state, detail[:120])

    @property
    def speaker(self) -> str:
        """The voice's gender, for Hindi verb forms: Supertonic's voices are F1..F5 and M1..M5."""
        return "male" if self.settings["voiceName"].startswith("M") else "female"

    @property
    def voice_on(self) -> bool:
        return bool(self.voice and self.speak_enabled and self.settings["voice"])

    def _refresh_state(self) -> None:
        """A setting changed: rewrite the state file so the mascot and the bar see it."""
        try:
            extra = {k: v for k, v in json.loads(STATE_FILE.read_text()).items()
                     if k not in ("state", "detail", "since", "pid", "brain", "mode", "effort", "language", "voice", "voiceName",
                                  "voices", "tts")}
        except (OSError, json.JSONDecodeError):
            extra = {}
        self.set_state(self.state, self.detail, **extra)

    def _from_thread(self, state: str, detail: str = "", **extra) -> None:
        self.loop.call_soon_threadsafe(lambda: self.set_state(state, detail, **extra))

    # --- warm-up ------------------------------------------------------------------------

    async def warm_up(self) -> None:
        t = time.perf_counter()
        if self.speak_enabled:
            from .voice import Voice
            self.voice = await asyncio.to_thread(Voice, on_level=self.level.set, on_sentence=self._caption)
            self.voice.tune(self.settings)
            try:
                self.voice.set_voice(self.settings["voiceName"])
            except ValueError as e:
                log.warning("%s", e)
        await asyncio.to_thread(self.brain.primary.start)
        log.info("ready in %.1fs (vocabulary %d terms)", time.perf_counter() - t, len(self.vocab))
        self.set_state("idle")

    # --- commands -----------------------------------------------------------------------

    async def command(self, line: str) -> str:
        cmd, _, arg = line.strip().partition(" ")
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
            self.settings["voiceName"] = self.voice.name
            save_settings(self.settings)
            self.brain.set_language(self.settings["language"], self.speaker)
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
            self.settings["voice"] = want
            save_settings(self.settings)
            if not want and self.voice:
                self.voice.stop()
            self._refresh_state()
            return "voice " + ("on" if want else "off")
        if cmd == "brain":
            mode = arg.strip()
            if mode not in Brain.MODES:
                return "usage: brain auto|claude|codex"
            self.settings["brain"] = self.brain.mode = mode
            save_settings(self.settings)
            self._refresh_state()
            return "brain " + mode
        if cmd == "language":
            lang = arg.strip()
            if lang not in LANGUAGES:
                return "usage: language " + "|".join(LANGUAGES)
            self.settings["language"] = lang
            save_settings(self.settings)
            self.brain.set_language(lang, self.speaker)
            self._refresh_state()
            return "language " + lang
        if cmd == "effort":
            level = arg.strip()
            if level not in Brain.EFFORTS:
                return "usage: effort " + "|".join(Brain.EFFORTS)
            self.settings["effort"] = level
            save_settings(self.settings)
            asyncio.create_task(self._set_effort(level))
            return "effort " + level
        if cmd == "status":
            return json.dumps({"state": self.state, "detail": self.detail,
                               "brain": "codex" if self.brain.using_backup else "claude",
                               "mode": self.brain.mode, "effort": self.brain.primary.effort,
                               "language": self.settings["language"], "voice": self.voice_on})
        if cmd == "quit":
            asyncio.get_running_loop().call_later(0.1, self.loop.stop)
            return "bye"
        return f"unknown command: {cmd}"

    def _tts(self, words: list[str]) -> str:
        """tts speed 1.1 | steps 5 | volume 0.8 | pause 0.2 (several at once is fine) | reset"""
        if words == ["reset"]:
            values = tuning()
        else:
            if not words or len(words) % 2 or any(k not in TUNING for k in words[::2]):
                return "usage: tts speed 0.7..1.6 | steps 2..12 | volume 0.1..1 | pause 0..1 | reset"
            values = tuning({**self.settings, **dict(zip(words[::2], words[1::2]))})
        self.settings.update(values)
        save_settings(self.settings)
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
            osd(f"Auren: {e}", 3000)
            return f"error: {e}"
        self.set_state("listening", "", fresh=fresh)
        osd("Listening (fresh start)…" if fresh else "Listening…", 1200)
        asyncio.get_running_loop().call_later(MAX_LISTEN, lambda: asyncio.create_task(self._listen_timeout(session)))
        return "listening"

    async def _listen_timeout(self, session: Listening) -> None:
        if self.listening is session:
            log.info("listening stopped after %ss", MAX_LISTEN)
            await self.release()

    async def _set_effort(self, level: str) -> None:
        async with self.busy:              # after the current answer, if there is one
            self.set_state("thinking", f"Switching to {level} effort…")
            await asyncio.to_thread(self.brain.set_effort, level)
            log.info("effort %s (fresh conversation)", level)
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": f"(effort {level})",
                           "tools": [], "brain": "claude"})
        self.idle()

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
            osd("Auren: couldn't hear (" + session.error[:60] + ")", 3000)
            return "error"
        if not text:
            self.set_state("idle", "heard nothing")
            osd("Didn't catch that", 1500)
            return "heard nothing"
        log.info("heard (%.2fs after release): %s", heard_in, text)
        asyncio.create_task(self.respond(text, source="voice", heard_s=heard_in))
        return "heard: " + text

    async def cancel(self) -> None:
        if self.listening:
            await self.listening.cancel()
            self.listening = None
        self.level.set(0)
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
            self.set_state("thinking", text, transcript=text)
            osd("Thinking…", 1500)
            tools: list[str] = []
            reply = ""

            def run() -> str:
                said = ""
                for ev in self.brain.ask(text):
                    if ev[0] == "tool":
                        tools.append(ev[1])
                        doing, mouse = describe(ev[1], ev[2] if len(ev) > 2 else {})
                        self._from_thread("working", doing, tool=ev[1], mouse=mouse, transcript=text)
                    elif ev[0] == "switch":
                        self._from_thread("thinking", ev[1])
                    elif ev[0] == "say":
                        said = ev[1]
                return said

            try:
                reply = await asyncio.to_thread(run)
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
            osd(text[:200], 4000)
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
        pending = Confirmations().pending
        if pending:
            p = max(pending.values(), key=lambda v: v["at"])
            self.set_state("idle", detail, awaiting=True, question=p.get("question", ""), until=p["at"] + 300)
        else:
            self.set_state("idle", detail)

    def _history(self, record: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "history.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    # --- server -------------------------------------------------------------------------------

    async def serve(self) -> None:
        self.loop = asyncio.get_running_loop()
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        if SOCKET.exists():
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

        server = await asyncio.start_unix_server(client, path=str(SOCKET))
        os.chmod(SOCKET, 0o600)
        self.set_state("starting")
        asyncio.create_task(self.warm_up())
        log.info("listening on %s", SOCKET)
        async with server:
            await server.serve_forever()


def main(speak: bool = True) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=[logging.StreamHandler(), logging.FileHandler(STATE_DIR / "daemon.log")])
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
        if SOCKET.exists():
            SOCKET.unlink()
