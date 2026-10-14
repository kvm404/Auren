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
    auren-ctl handsfree on|off|toggle # say "Auren" instead of pressing the key (docs/handsfree.md)
    auren-ctl handsfree sensitivity low|medium|high | follow-up off|questions|always

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
import collections
import fcntl
import json
import logging
import os
import re
import time

from . import notice, wake
from .brain import Brain, BrainError, Interrupted
from .config import LOCK, RUNTIME_DIR, SOCKET, STATE_DIR, STATE_FILE, TOOLS_SOCKET
from .ears import Listening, vocabulary
from .live import Level, describe, notice_kind
from .profile import Profile
from .safety import TOKEN_TTL
from .settings import BY_FLAT
from .settings import load as load_settings
from .settings import save as save_settings
from .tools import Runtime
from .voice import TUNING, tuning

SESSION = "daemon"          # the daemon's tool session (an `auren chat` has its own)
log = logging.getLogger("auren")
MIN_HOLD = 0.35          # shorter key taps are ignored
MAX_LISTEN = 120         # seconds; listening stops by itself (the speech service bills per second)
NO_SPEECH = 8.0          # hands-free: seconds after the name with nothing said, then back to waiting for it
FOLLOW_UP = 5.0          # hands-free: seconds to start answering after a reply, without the name
ECHO_TAIL = 1.0          # hands-free: seconds a spoken sentence with the name in it still blocks the name
MIN_TURN_SILENCE = 500   # ms: hands-free, a finished sentence ends the turn after this much silence
# Said on its own, these start a fresh conversation instead of going to the brain.
FORGET = re.compile(r"^\W*(?:(?:ok(?:ay)?|hey|auren)\W+)*(?:start (?:fresh|over|a new conversation)|new conversation|"
                    r"forget (?:everything|all that|it all|our conversation)|clear (?:your|the) memory|reset (?:yourself|the conversation))"
                    r"\W*(?:please)?\W*$", re.I)
# Said on its own, these stop what Auren was doing (the name already did) and go no further.
STOP = re.compile(r"^\W*(?:stop(?: it| talking| that)?|cancel(?: it| that)?|never ?mind|nothing|forget it|shut up|be quiet|"
                  r"quiet|that's (?:all|it)|no,? thanks?(?: you)?|thanks?(?: you)?)\W*(?:please)?\W*$", re.I)
HANDSFREE_OFF = re.compile(r"^\W*(?:(?:turn off|stop|disable|end)\W+hands[- ]?free(?:\W+mode)?|hands[- ]?free\W+off|"
                           r"stop listening(?: for (?:me|your name))?)\W*(?:please)?\W*$", re.I)
HANDSFREE_ON = re.compile(r"^\W*(?:(?:turn on|start|enable)\W+hands[- ]?free(?:\W+mode)?|hands[- ]?free\W+on)\W*(?:please)?\W*$",
                          re.I)
# Fields every state.json has; _refresh_state() keeps the others (transcript, awaiting...).
BASE_FIELDS = ("state", "detail", "since", "pid", "brain", "mode", "effort", "voice", "voiceName", "voices", "tts",
               "handsfree", "handsfreeOptions")


class Daemon:
    handsfree: wake.HandsFree | None = None      # the open mic and the wake word, while hands-free is on
    _hf_problem = ""                             # why hands-free didn't start
    _hf_task: asyncio.Task | None = None         # it's starting (the first time, its model downloads)
    _waking = False                              # the name was just heard: a listen is starting
    _spoke_until = 0.0                           # when Auren's voice last stopped (for the echo guard)

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
        self._spoken: collections.deque[tuple[float, str]] = collections.deque(maxlen=2)   # (started, sentence) just spoken

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
                                   "tts": tuning(self.settings), "handsfree": self._hf_status(),
                                   "handsfreeOptions": {"sensitivity": self.settings.get("handsfree.sensitivity", "medium"),
                                                        "followUp": self.settings.get("handsfree.follow_up", "questions")},
                                   **extra}))
        tmp.replace(STATE_FILE)
        if state != "listening":           # partial transcripts would flood the log
            log.info("state %s %s", state, detail[:120])

    @property
    def voice_on(self) -> bool:
        return bool(self.voice and self.speak_enabled and self.settings["voice"])

    def _refresh_state(self) -> None:
        """A setting changed: rewrite the state file so the mascot and the bar see it."""
        try:
            extra = {k: v for k, v in json.loads(STATE_FILE.read_text()).items() if k not in BASE_FIELDS}
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
        if self.settings.get("handsfree"):
            self._start_handsfree()

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
                               "voice": self.voice_on, "handsfree": self._hf_status()})
        if cmd == "handsfree":
            return self._handsfree_command(arg.split())
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
        if not new["handsfree"]:
            self._stop_handsfree()
        elif not old.get("handsfree") or any(new[k] != old.get(k) for k in ("handsfree.sensitivity",
                                                                             "handsfree.pause_when_mic_busy")):
            self._stop_handsfree()               # started again with the new settings
            self._start_handsfree()
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

    # --- hands-free ----------------------------------------------------------------------------

    def _hf_status(self) -> str:
        """off, starting, on, paused (another app has the mic) or error (see the log)."""
        if not self.settings.get("handsfree"):
            return "off"
        if self.handsfree:
            return self.handsfree.status
        return "error" if self._hf_problem else "starting"

    def _handsfree_command(self, words: list[str]) -> str:
        """handsfree on|off|toggle | sensitivity low|medium|high | follow-up off|questions|always"""
        names = {"sensitivity": "handsfree.sensitivity", "follow-up": "handsfree.follow_up"}
        if len(words) == 2 and words[0] in names:
            key = names[words[0]]
            try:
                value = BY_FLAT[key].check(words[1])
            except ValueError as e:
                return f"usage: handsfree {words[0]} {e}"
            changed = value != self.settings.get(key)
            self._save({key: value})
            if changed and key == "handsfree.sensitivity" and (self.handsfree or self._hf_task):
                self._stop_handsfree()
                self._start_handsfree()
            self._refresh_state()
            return f"handsfree {words[0]} {value}"
        want = {"on": True, "off": False, "toggle": not self.settings.get("handsfree")}.get(words[0]) \
            if len(words) == 1 else None
        if want is None:
            return "usage: handsfree on|off|toggle | sensitivity low|medium|high | follow-up off|questions|always"
        self._save({"handsfree": want})
        if want:
            self._start_handsfree()
        else:
            self._stop_handsfree()
        self._refresh_state()
        return "handsfree " + ("on" if want else "off")

    def _start_handsfree(self) -> None:
        """Open the mic and listen for the name (in the background: the first time, the model downloads)."""
        if self.handsfree or (self._hf_task and not self._hf_task.done()):
            return
        self._hf_problem = ""
        self._hf_task = asyncio.create_task(self._open_handsfree())

    async def _open_handsfree(self) -> None:
        s = self.settings
        hf = None
        try:
            if wake.model_dir() is None:
                notice.show("Getting the wake word ready…", "auren", 4000)
                await asyncio.to_thread(wake.download_model, log.info)
            hf = wake.HandsFree(self._wake, on_audio=self._route, on_change=self._refresh_state,
                                sensitivity=s["handsfree.sensitivity"], pause_when_busy=s["handsfree.pause_when_mic_busy"])
            await hf.start()
        except asyncio.CancelledError:
            if hf:
                hf.stop()
            raise
        except Exception as e:  # noqa: BLE001 - the talk key still works
            if hf:
                hf.stop()
            self._hf_problem = str(e)[:160] or type(e).__name__
            log.error("hands-free didn't start: %s", e)
            notice.notify("Hands-free didn't start", self._hf_problem, "error")
            self._refresh_state()
            return
        self.handsfree = hf
        log.info("hands-free on (sensitivity %s)", s["handsfree.sensitivity"])
        self._refresh_state()

    def _stop_handsfree(self) -> None:
        if self._hf_task and not self._hf_task.done():
            self._hf_task.cancel()
        self._hf_task = None
        hf, self.handsfree = self.handsfree, None
        if hf:
            hf.stop()
            log.info("hands-free off")
            if self.listening and self.listening.external:      # a listen started by the name: its mic is gone
                asyncio.create_task(self._drop(self.listening, ""))
        self._hf_problem = ""

    def _route(self, chunk: bytes) -> None:
        """Audio from the hands-free mic: to the listen in progress, if there is one."""
        if self.listening and self.listening.external:
            self.listening.push(chunk)

    def _saying_the_name(self) -> bool:
        """Is Auren's own voice saying its name (or a sound-alike)? The sentence being heard, and each one for
        a moment after it ended, the last one too (the spotter is a little behind)."""
        now = time.monotonic()
        recent = list(self._spoken)
        for i, (_, text) in enumerate(recent):
            ended = recent[i + 1][0] if i + 1 < len(recent) else (None if self.state == "speaking" else self._spoke_until)
            if (ended is None or now - ended < ECHO_TAIL) and wake.mentions(text):
                return True
        return False

    def _wake(self, word: str) -> None:
        """The spotter heard the name (on the loop)."""
        if not self.handsfree or self.handsfree.paused:
            return                         # turned off or paused since: a late hit from the spotter's thread
        if self.listening or self._waking or self.state in ("starting", "off"):
            return                         # already listening: the name inside a request doesn't start over
        if self._saying_the_name():
            log.info("wake word ignored: Auren was saying it (%s)", word)
            return
        log.info("heard the wake word (%s) while %s", word.lower(), self.state)
        self._waking = True
        asyncio.create_task(self._woken())

    async def _woken(self) -> None:
        try:
            if not self.handsfree or self.listening:       # turned off, or a key press, since the hit
                return
            # The audio before the name goes too, so the transcript can be checked for it; except while
            # Auren speaks: that audio is its own voice, and the name only stops it (no check).
            preroll = [] if self.state == "speaking" else self.handsfree.preroll()
            working = self.state in ("thinking", "working")
            if working:
                self.tools.cancel(SESSION)           # sticks now; the brain is stopped once the listen is up
            await self.press(hands_free="wake", preroll=preroll, check=bool(preroll))
            if working:
                await asyncio.to_thread(self.brain.interrupt)
                notice.show("Stopped", "stopped")
        finally:
            self._waking = False

    def _wants_follow_up(self, reply: str) -> bool:
        if not self.handsfree or self.handsfree.paused:
            return False
        mode = self.settings.get("handsfree.follow_up", "questions")
        return mode == "always" or (mode == "questions" and (reply.rstrip().endswith("?")
                                                              or self.tools.awaiting(SESSION) is not None))

    async def _end_on_pause(self, session: Listening, wait: float) -> None:
        """A hands-free listen: send it at the pause that ends the request. The name alone keeps listening;
        nothing said for `wait` seconds goes back to waiting for the name."""
        deadline = time.monotonic() + wait
        while self.listening is session:
            try:
                await asyncio.wait_for(session.turn.wait(), timeout=0.25)
                ended = True
            except TimeoutError:
                ended = False
            if self.listening is not session:
                return
            if not ended and session.closed:             # the connection ended (or failed): report it now
                await self.release()
                return
            text = " ".join(f for f in session.finals if f)
            if ended:
                session.turn.clear()
                if session.check and text:
                    if not wake.addressed(text):           # "Lauren said..." woke the spotter, not the user
                        log.info("not for me: %s", text)
                        await self._drop(session, "not for me")
                        return
                    session.check = False
                if wake.strip_wake(text):
                    await self.release()
                    return
                deadline = time.monotonic() + wait         # just the name so far: wait for the request
            elif not session.speaking and time.monotonic() > deadline:
                log.info("hands-free: nothing said")
                await self._drop(session, "heard nothing" if session.hands == "wake" else "")
                return

    async def _drop(self, session: Listening, detail: str) -> None:
        """End a listen without sending anything."""
        if self.listening is session:
            self.listening = None
            await session.cancel()
            if self.listening is None and self.state == "listening":    # nothing newer started meanwhile
                self.level.set(0)
                self.idle(detail)

    # --- listening ------------------------------------------------------------------------------

    async def press(self, fresh: bool = False, hands_free: str | None = None, preroll: list[bytes] = (),
                    check: bool = False) -> str:
        """Start listening. hands_free: "wake" (the name was heard) or "follow-up" (after a reply): a pause
        ends it instead of the key. check: the transcript must start with the name (see wake.addressed)."""
        if self.listening:
            return "already listening"
        if self.voice:
            self.voice.stop()            # talking over auren interrupts it
        if fresh:                        # restart the brain while the user speaks; the answer waits for it
            asyncio.create_task(self._forget_quietly())
        extra = {"fresh": fresh}
        silence = None
        if hands_free:
            extra = {"wake": True, "followUp": hands_free == "follow-up"}
            end = int(self.settings.get("handsfree.end_silence", 1.2) * 1000)
            silence = (MIN_TURN_SILENCE, max(MIN_TURN_SILENCE, end))
        self.listening = Listening(self.vocab, on_partial=lambda t: self.set_state("listening", t, **extra),
                                   on_level=self.level.set, external=bool(hands_free and self.handsfree),
                                   turn_silence=silence)
        session = self.listening
        session.hands, session.check = hands_free, check
        for chunk in preroll:
            session.push(chunk)
        try:
            await self.listening.start()
        except Exception as e:  # noqa: BLE001
            self.listening = None
            self.set_state("error", str(e))
            notice.notify("Auren couldn't start listening", str(e), "error")
            return f"error: {e}"
        self.set_state("listening", "", **extra)
        if hands_free == "wake" and self.settings.get("handsfree.chime", True):
            wake.chime()
        if hands_free != "follow-up":
            notice.show("Listening (fresh start)…" if fresh else "Listening…", "listening", 1200)
        if hands_free:
            asyncio.create_task(self._end_on_pause(session, FOLLOW_UP if hands_free == "follow-up" else NO_SPEECH))
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
        if not session.hands and session.held_seconds < MIN_HOLD:
            await session.cancel()
            self.set_state("idle", "key tap ignored")
            return "too short"
        t = time.perf_counter()
        text = await session.finish()
        heard_in = time.perf_counter() - t
        if session.hands and text:              # "Auren, yes" after a question is fine too
            if session.check and not wake.addressed(text):
                log.info("not for me: %s", text)
                self.idle("not for me")
                return "not for me"
            text = wake.strip_wake(text)
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
        session, self.listening = self.listening, None      # detached first: its last words can't be sent now
        answering = self.state in ("thinking", "working")
        if answering:                                  # an answer in progress: stop the brain and its tools
            self.tools.cancel(SESSION)
        if session:
            await session.cancel()
        if answering:
            await asyncio.to_thread(self.brain.interrupt)
            notice.show("Stopped", "stopped")
        if self.listening:                             # a new listen started meanwhile: it owns the state now
            return
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
        if source == "voice" and STOP.match(text):
            log.info("nothing to do: %s", text)
            self.idle("cancelled")
            return
        if source == "voice" and (self.settings.get("handsfree") and HANDSFREE_OFF.match(text)
                                  or not self.settings.get("handsfree") and HANDSFREE_ON.match(text)):
            on = self._handsfree_command(["toggle"]) == "handsfree on"
            await self.say("Hands-free is on. Say Auren when you need me." if on
                           else "Okay, hands-free is off. Press Super A when you need me.")
            return
        spoke = False
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
            if self.tools.cancelled(SESSION):         # stopped as the reply came in: the user moved on
                log.info("cancelled with a reply ready: %s", reply)
                self._history({"at": time.time(), "source": source, "transcript": text, "reply": "(cancelled)",
                               "tools": tools, "heard_s": heard_s, "think_s": round(time.perf_counter() - t0, 2),
                               "brain": "codex" if self.brain.using_backup else "claude"})
                if not self.listening:
                    self.idle("cancelled")
                return
            think_s = time.perf_counter() - t0
            log.info("reply (%.2fs, tools %s): %s", think_s, tools, reply)
            self._history({"at": time.time(), "source": source, "transcript": text, "reply": reply, "tools": tools,
                           "heard_s": heard_s, "think_s": round(think_s, 2),
                           "brain": "codex" if self.brain.using_backup else "claude"})
            if reply:
                spoke = await self.say(reply)
            else:
                self.idle()
        # Hands-free: an answer to Auren's question (or anything, with follow_up = always) needs no name.
        if spoke and self.state == "idle" and not self.listening and self._wants_follow_up(reply):
            await self.press(hands_free="follow-up")

    async def say(self, text: str) -> bool:
        """Speak (or show) a reply; False if something cut it off."""
        if not self.voice_on:
            notice.notify("Auren", text)
            self.set_state("speaking", text, reply=text, silent=True)
            await asyncio.sleep(min(8.0, 1.5 + len(text) / 18))    # time to read it in the mascot's bubble
            if self.state == "speaking":
                self.idle()
                return True
            return False
        self._reply = text
        self._spoken.clear()
        self.set_state("speaking", "", reply=text)
        finished = await asyncio.to_thread(self.voice.speak, text)
        self._spoke_until = time.monotonic()
        if self.state == "speaking":
            self.idle("" if finished else "interrupted")
            return finished
        return False

    def _caption(self, sentence: str) -> None:
        """The voice started a new sentence: show it in the mascot's bubble."""
        self._spoken.append((time.monotonic(), sentence))     # for the wake word: Auren saying its name isn't the user
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
        if d.handsfree:
            d.handsfree.stop()
        d.brain.stop()
        d.tools.stop()
        if SOCKET.exists():
            SOCKET.unlink()
        lock.close()
    return 0
