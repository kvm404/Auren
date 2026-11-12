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
    auren-ctl safety ask|task|free    # how much it asks before risky actions (saved in config.toml)
    auren-ctl autosend on|off|toggle  # send by itself when you stop talking (off: remind you to press Super+A)
    auren-ctl autosend after 5        # seconds without new words before auto-send sends (1 to 15)
    auren-ctl yes | no                # answer the question(s) it is waiting on (the bubble's buttons do this)
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
from .config import LOCK, MAC, RUNTIME_DIR, SOCKET, STATE_DIR, STATE_FILE, TOOLS_SOCKET
from .ears import Listening, vocabulary
from .live import Level, describe, notice_kind
from .profile import Profile
from .safety import LEVELS, TOKEN_TTL, parse_answer
from .settings import BY_FLAT as SETTING_KEYS
from .settings import load as load_settings
from .settings import save as save_settings
from .tools import Outcome, Runtime
from .voice import TUNING, tuning

SESSION = "daemon"          # the daemon's tool session (an `auren chat` has its own)
log = logging.getLogger("auren")
MIN_HOLD = 0.35          # shorter key taps are ignored
MAX_LISTEN = 120         # seconds; listening stops by itself (the speech service bills per second)
# When the user stops talking (no new words; see ears.Listening.quiet_seconds). With auto-send off, it reminds
# them to press the key, then sends by itself; nothing said at all ends the listen the same way.
REMIND_AFTER = 3         # seconds without new words: "Press Super+A to send"
SEND_AFTER = 12          # seconds without new words: send what was heard
QUIET_TICK = 0.2         # how often a listen checks for quiet
ALREADY_SENT = 2         # seconds after a send by itself (of something heard) in which the talk key does nothing
# Said on its own, these start a fresh conversation instead of going to the brain.
FORGET = re.compile(r"^\W*(?:(?:ok(?:ay)?|hey|auren)\W+)*(?:start (?:fresh|over|a new conversation)|new conversation|"
                    r"forget (?:everything|all that|it all|our conversation)|clear (?:your|the) memory|reset (?:yourself|the conversation))"
                    r"\W*(?:please)?\W*$", re.I)


# A request with more than one part ("close it and open Spotify"): after a yes to one part, the brain must go on.
COMPOUND = re.compile(r"\b(?:and|then|also|after|afterwards|before|while|plus|next|too|as well)\b|[,;]|[.?!]\s*\S", re.I)


class Daemon:
    _request = ""                       # what the user asked, when Auren answered with questions (not told to the brain again)
    _handover_until: float | None = None  # the end of Codex's turn that a state refresh is set for
    _earlier: tuple[str, ...] = ()       # what happened to answers the brain wasn't told of yet (told with the next message)
    _sent_by_itself = float("-inf")       # when a listen last ended without the key (quiet), time.monotonic()
    _reminding = False                    # the "Press Super+A to send" reminder is up (state.json `remind`)
    _quiet_watch: asyncio.Task | None = None   # the current listen's _watch_quiet (kept: the loop holds tasks weakly)

    def __init__(self, speak: bool = True):
        self.settings = load_settings()
        self.profile = Profile()
        self.tools = Runtime(TOOLS_SOCKET)           # serving from serve() on
        s = self.settings
        self.tools.level = s["safety"]
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
                                   "mode": self.brain.mode, "effort": self.brain.primary.effort, "codexModel": self.brain.backup.model,
                                   "codexEffort": self.brain.backup.effort, "voice": self.voice_on,
                                   "safety": self.tools.level,
                                   "voiceName": self.voice.name if self.voice else self.settings["voiceName"],
                                   "voices": self.voice.names if self.voice else [],
                                   "tts": tuning(self.settings), "autosend": self.autosend,
                                   "autosendAfter": self.autosend_after, **extra}))
        tmp.replace(STATE_FILE)
        if state != "listening":           # partial transcripts would flood the log
            log.info("state %s %s", state, detail[:120])
        self._watch_handover()

    def _watch_handover(self) -> None:
        """Codex answers for Claude only until Brain.RETRY_AFTER is up: rewrite the state then, or an idle
        daemon leaves the panel showing Codex (and its effort) while Claude would answer."""
        until = self.brain.backup_until
        if until is None or until == self._handover_until or self.loop is None:
            return
        self._handover_until = until
        self.loop.call_later(max(0.0, until - time.time()) + 0.5, self._refresh_state)

    @property
    def autosend(self) -> bool:
        return bool(self.settings.get("autosend", False))

    @property
    def autosend_after(self) -> float:
        return float(self.settings.get("autosendAfter", 5))

    @property
    def voice_on(self) -> bool:
        return bool(self.voice and self.speak_enabled and self.settings["voice"])

    def _refresh_state(self) -> None:
        """A setting changed: rewrite the state file so the mascot and the bar see it."""
        try:
            extra = {k: v for k, v in json.loads(STATE_FILE.read_text()).items()
                     if k not in ("state", "detail", "since", "pid", "brain", "mode", "effort", "codexModel", "codexEffort",
                                  "voice", "voiceName", "voices", "tts", "safety", "autosend", "autosendAfter")}
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
        if cmd in ("talk", "talk-fresh") and not self.listening and time.monotonic() - self._sent_by_itself < ALREADY_SENT:
            return "already sent"                    # pressed to send just as the quiet sent it: not a new listen or a stop
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
        if cmd == "codex-effort":
            level = arg.strip()
            if level not in Brain.CODEX_EFFORTS:
                return "usage: codex-effort " + "|".join(Brain.CODEX_EFFORTS)
            self.brain.set_codex(self.brain.backup.model, level)      # from the next request, no fresh start
            self._save({"brain.codex_effort": level})
            self._refresh_state()
            return "codex-effort " + level
        if cmd == "safety":
            level = arg.strip()
            if level not in LEVELS:
                return "usage: safety " + "|".join(LEVELS)
            self.tools.set_level(level)
            self._save({"safety": level})
            self._refresh_state()
            return "safety " + level
        if cmd == "autosend":
            return self._autosend(arg.split())
        if cmd in ("yes", "no"):
            waiting = self.tools.confirmations.batch(SESSION)
            if not waiting:
                return "nothing is waiting for an answer"
            # Only what is shown now: a second click, or a question asked meanwhile, isn't covered by this one.
            asyncio.create_task(self.answer(cmd == "yes", "button", {t for t, _ in waiting}))
            return ("approved " if cmd == "yes" else "dropped ") + str(len(waiting))
        if cmd == "status":
            return json.dumps({"state": self.state, "detail": self.detail,
                               "brain": "codex" if self.brain.using_backup else "claude",
                               "mode": self.brain.mode, "effort": self.brain.primary.effort,
                               "voice": self.voice_on, "safety": self.tools.level})
        if cmd == "reload":
            return self._reload()
        if cmd == "permissions" and MAC:          # macOS asks this process, the one that needs them
            from .mac import permissions
            ask = permissions.status if arg.strip() == "status" else permissions.request
            now = await asyncio.get_running_loop().run_in_executor(None, ask)
            return json.dumps({**now, "program": permissions.program(),
                               "talk_keys": bool(getattr(self, "_hotkeys", None))})
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
        if MAC and (new["keys.talk"], new["keys.talk_fresh"]) != (old["keys.talk"], old["keys.talk_fresh"]):
            self._listen_for_keys()
        if new["safety"] != old["safety"]:
            self.tools.set_level(new["safety"])
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

    def _autosend(self, words: list[str]) -> str:
        """autosend on | off | toggle | after 5"""
        usage = "usage: autosend on|off|toggle | autosend after 1..15"
        if len(words) == 2 and words[0] == "after":
            try:
                after = SETTING_KEYS["autosendAfter"].check(words[1])
            except ValueError as e:
                return f"autosend after should be {e}"
            self._save({"autosendAfter": after})
        elif len(words) == 1 and words[0] in ("on", "off", "toggle"):
            self._save({"autosend": not self.autosend if words[0] == "toggle" else words[0] == "on"})
        else:
            return usage
        self._refresh_state()
        return f"autosend {'on' if self.autosend else 'off'}, after {self.autosend_after:g} s"

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
        session = self.listening = Listening(self.vocab, on_level=self.level.set)
        session.on_partial = lambda _text: self._show_listening(session, fresh)
        self._reminding = False
        try:
            await self.listening.start()
        except Exception as e:  # noqa: BLE001
            self.listening = None
            self.set_state("error", str(e))
            notice.notify("Auren couldn't start listening", str(e), "error")
            return f"error: {e}"
        self.set_state("listening", "", fresh=fresh)
        notice.show("Listening (fresh start)…" if fresh else "Listening…", "listening", 1200)
        self._quiet_watch = asyncio.create_task(self._watch_quiet(session, fresh))
        return "listening"

    def _quiet_action(self, session: Listening) -> str:
        return after_quiet(session.quiet_seconds, session.words > 0, self.autosend, self.autosend_after)

    def _show_listening(self, session: Listening, fresh: bool, what: str | None = None) -> None:
        """What this listen heard so far, with the reminder if it's due. New live text and the quiet watch
        both write it, so a revised partial (no new words) keeps a reminder that is still due."""
        if self.listening is not session:                # a partial arriving after the send, or an older listen
            return
        self._reminding = (self._quiet_action(session) if what is None else what) == "remind"
        self.set_state("listening", session.text, fresh=fresh, **({"remind": True} if self._reminding else {}))

    async def _watch_quiet(self, session: Listening, fresh: bool) -> None:
        """While this listen lasts: remind the user to send when they go quiet, and send by itself after a
        while (auto-send: after its own delay), so a forgotten key press doesn't leave the mic open. It also
        enforces MAX_LISTEN, so if it fails, it stops the listen rather than leave the mic open."""
        try:
            await self._watch_quiet_until_sent(session, fresh)
        except Exception as e:  # noqa: BLE001 - logged and reported; the listen must not outlive its watch
            log.exception("the quiet watch failed: stopping this listen")
            if self.listening is session:
                await self.cancel()
            else:                                            # it failed in release(): its mic may still be open
                await session.cancel()
                if self.listening is None:                   # not if the user started another listen meanwhile
                    self.level.set(0)
                    self.set_state("error", f"listening failed: {e}"[:200])
            notice.notify("Auren stopped listening", str(e)[:200], "error")

    async def _watch_quiet_until_sent(self, session: Listening, fresh: bool) -> None:
        while self.listening is session:
            await asyncio.sleep(QUIET_TICK)
            if self.listening is not session:
                return
            quiet, heard = session.quiet_seconds, session.words > 0
            what = after_quiet(quiet, heard, self.autosend, self.autosend_after)
            if session.held_seconds >= MAX_LISTEN:
                log.info("listening stopped after %ss", MAX_LISTEN)
                what = "send"
            elif what == "send":
                how = "nothing said" if not heard else "auto-send" if self.autosend else "sending by itself"
                log.info("%s after %.1fs without new words", how, quiet)
            if what == "send":
                # Stamped before and after: a press while the final transcript comes in, or just as the answer
                # starts, was meant to send this listen, not to start another or stop the answer. With nothing
                # heard there's nothing to answer, and a press is a new try.
                if heard:
                    self._sent_by_itself = time.monotonic()
                await self.release()
                if heard:
                    self._sent_by_itself = time.monotonic()
                return
            if (what == "remind") != self._reminding:      # due now, or talking again took it away
                self._show_listening(session, fresh, what)
                if self._reminding:
                    notice.show("Press Super+A to send", "listening", 3000)

    async def _set_claude(self, effort: str | None = None, model: str | None = None) -> None:
        what = " and ".join(x for x in (effort and f"{effort} effort", model and f"model {model}") if x)
        async with self.busy:              # after the current answer, if there is one
            self.set_state("thinking", f"Switching to {what}…")
            self._fresh()
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

    def _fresh(self) -> None:
        """A new conversation: what was asked in the old one can't be answered, and its notes are gone."""
        self._earlier, self._request = (), ""
        self.tools.forget(SESSION)

    async def _forget_quietly(self) -> None:
        async with self.busy:
            self._fresh()
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
            self._fresh()
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
            # A plain yes or no to what it just asked is read here, not by the brain: the user's own words
            # are the proof, and nothing waits for a model to turn them into a tool call.
            if self.tools.answerable(SESSION) and (answer := parse_answer(text)):
                await self._answer(answer == "yes", source, text, heard_s)
            else:
                await self._think(text, source, heard_s)

    async def answer(self, yes: bool, source: str, tokens: set[str] | None = None) -> None:
        """A yes or no that didn't come as words (the bubble's buttons, auren-ctl yes|no), to these questions."""
        async with self.busy:
            await self._answer(yes, source, tokens=tokens)

    async def _answer(self, yes: bool, source: str, text: str = "", heard_s: float | None = None,
                      tokens: set[str] | None = None) -> None:
        batch = [p for t, p in self.tools.confirmations.batch(SESSION) if tokens is None or t in tokens]
        if not batch:
            return
        said = text or ("yes" if yes else "no")
        t0 = time.perf_counter()
        # The user's answer is a message, which is what lets the tokens run; it goes on with the task, so what an
        # earlier yes covered (level "task") stays covered. The judge reads it too: a "no" takes back what was asked.
        self.tools.new_turn(SESSION, same_task=True, said=said)
        asked = [p.question for p in batch]
        if not yes:
            self.tools.answer(SESSION, False, tokens)
            self._earlier += (f"You asked {'; '.join(repr(q) for q in asked)} and the user said no, so none of it was done.",)
            await self._finish_answer(said, source, heard_s, t0, "Okay, I won't.", [])
            return
        self.set_state("working", "Doing it", transcript=said)
        notice.show("Doing it", "auren", always=True)
        outcomes = await asyncio.to_thread(self.tools.answer, SESSION, True, tokens)
        if self.tools.cancelled(SESSION):             # stopped meanwhile: what was running finished, nothing more
            self._earlier += ("The user said yes and then stopped you; some of it may have run.",)
            self._history({"at": time.time(), "source": source, "transcript": said, "reply": "(cancelled)",
                           "tools": ["confirm_action"], "heard_s": heard_s, "brain": "none"})
            return
        if outcomes and all(o.ok and o.say for o in outcomes) and self._request and not COMPOUND.search(self._request):
            self._earlier += (f"You asked {'; '.join(repr(q) for q in asked)}, the user said yes and it was done.",)
            await self._finish_answer(said, source, heard_s, t0, " ".join(o.say for o in outcomes), ["confirm_action"])
            return
        # Anything else: the brain sees what happened, so it can go on with the task or say how it went.
        await self._think(said, source, heard_s, brain_text=_followup(outcomes, len(batch)), same_task=True,
                          already=["confirm_action"])

    async def _finish_answer(self, said: str, source: str, heard_s: float | None, t0: float, reply: str,
                             tools: list[str]) -> None:
        log.info("answered %r without the brain (%.2fs): %s", said, time.perf_counter() - t0, reply)
        self.tools.replied(SESSION, reply)
        self._history({"at": time.time(), "source": source, "transcript": said, "reply": reply, "tools": tools,
                       "heard_s": heard_s, "think_s": round(time.perf_counter() - t0, 2), "brain": "none"})
        await self.say(reply)

    async def _think(self, text: str, source: str, heard_s: float | None = None, brain_text: str | None = None,
                     same_task: bool = False, already: list[str] | None = None) -> None:
        """Ask the brain (the lock is held). text: what the user said; brain_text: what the brain gets, if not that."""
        t0 = time.perf_counter()
        # Counted here, before "thinking" shows: a cancel from now on sticks (the worker can't undo it). The judge
        # reads what the user said once: an answer's follow-up (brain_text) was counted by _answer.
        self.tools.new_turn(SESSION, same_task=same_task,          # once per user message, whichever brain answers
                            said="" if brain_text is not None else text)
        earlier, self._earlier = self._earlier, ()
        to_brain = (brain_text or text) if not earlier else f"({' '.join(earlier)})\n{brain_text or text}"
        self.set_state("thinking", text, transcript=text)
        notice.show("Thinking…", "thinking")
        tools = list(already or [])
        reply = ""

        def progress(state: str, detail: str, **extra) -> None:
            # Checked on the loop, where cancel() runs: once stopped, events still in the stream
            # mustn't bring back "working" over idle, or over a new listen.
            if not self.tools.cancelled(SESSION):
                self.set_state(state, detail, **extra)

        def run() -> str:
            said = ""
            for ev in self.brain.ask(to_brain):
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
            self._earlier = earlier + self._earlier        # the brain never got them
            log.info("cancelled while %s", "working" if tools else "thinking")
            self._history({"at": time.time(), "source": source, "transcript": text, "reply": "(cancelled)",
                           "tools": tools, "heard_s": heard_s, "think_s": round(time.perf_counter() - t0, 2),
                           "brain": "codex" if self.brain.using_backup else "claude"})
            if not self.listening:            # the talk key may already have started the next listen
                self.idle("cancelled")
            return
        except BrainError as e:
            self._earlier = earlier + self._earlier
            reply = "Sorry, my brain hit an error: " + str(e)[:120]
            log.error("brain error: %s", e)
        think_s = time.perf_counter() - t0
        if brain_text is None:                    # what the user asked, if this reply asked them something
            self._request = text if self.tools.batch(SESSION) else ""
        log.info("reply (%.2fs, tools %s): %s", think_s, tools, reply)
        self.tools.replied(SESSION, reply)
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
        """Back to idle; if the brain asked yes/no questions that are still open, say so for the mascot: all of
        them in `question`, since one answer covers them."""
        waiting = self.tools.batch(SESSION)
        if waiting:
            question = waiting[0].question if len(waiting) == 1 else (
                f"{len(waiting)} things, one answer:\n" + "\n".join("• " + p.question for p in waiting))
            self.set_state("idle", detail, awaiting=True, question=question,
                           until=min(p.at for p in waiting) + TOKEN_TTL)
        else:
            self.set_state("idle", detail)

    def _history(self, record: dict) -> None:
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        with (STATE_DIR / "history.jsonl").open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _listen_for_keys(self) -> None:
        """macOS: the talk keys come from an event tap here (on Omarchy they are Hyprland bindings). A press
        goes through the socket, like auren-ctl's."""
        from .ctl import send
        from .mac import hotkey
        if getattr(self, "_hotkeys", None):
            self._hotkeys.stop()
        self._hotkeys = hotkey.start({"talk": self.settings["keys.talk"], "talk-fresh": self.settings["keys.talk_fresh"]},
                                     lambda command: send(command, timeout=10))

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
        if MAC:
            self._listen_for_keys()
        async with server:
            await server.serve_forever()


def after_quiet(quiet: float, heard: bool, autosend: bool, autosend_after: float) -> str:
    """What a listen does after `quiet` seconds without new words: "send", "remind" or "" (keep listening).
    heard: whether any words came at all (before that, the quiet is counted from the start)."""
    if heard and autosend:
        return "send" if quiet >= autosend_after else ""
    if quiet >= SEND_AFTER:
        return "send"
    return "remind" if heard and quiet >= REMIND_AFTER else ""


def _followup(outcomes: list[Outcome], asked: int) -> str:
    """What the brain is told after the user's yes ran its questions (it didn't make that call itself)."""
    lines = [f"- {o.question or 'a question'} -> {'done' if o.ok else 'FAILED'}: {o.result[:1500]}" for o in outcomes]
    if len(outcomes) < asked:
        lines.append(f"- {asked - len(outcomes)} more were not run because of the failure: look again and ask again.")
    return ("(The user said yes to what you asked. This is what happened:\n" + "\n".join(lines)
            + "\nGo on with their task if there is more to do; otherwise tell them how it went in a sentence or two.)")


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
