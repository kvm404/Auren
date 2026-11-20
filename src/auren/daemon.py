"""The auren daemon: push-to-talk → ears → brain → voice.

    auren daemon            # run in the foreground (later: a systemd user service)
    auren-ctl talk          # Super+A: start listening; press again to stop and send
    auren-ctl talk-fresh    # Super+Shift+A: the same, in a fresh conversation
    auren-ctl listen        # hands-free (a phone button): start listening; it sends when you stop talking
    auren-ctl listen-fresh  # the same, in a fresh conversation
    auren-ctl start|stop    # push-to-talk (press / release), used by holding the mascot
    auren-ctl ask <text>    # a typed request through the same pipeline (spoken reply)
    auren-ctl say <text> | cancel | status | quit
    auren-ctl forget        # a fresh conversation (also by voice: "start fresh", "forget everything")
    auren-ctl voice on|off|toggle     # spoken replies (off: replies only in the mascot's bubble)
    auren-ctl voice-name Everly|Juniper|Hugh|Trevor [sample] # which voice (says the sample, or a short default one)
    auren-ctl voice-load              # speak now if the voice wasn't there yet (auren voice-model sends this)
    auren-ctl tts speed 1.1 | volume 0.8 | pause 0.2 | reset   # how the voice speaks
    auren-ctl brain auto|claude|codex # which brain answers (saved in ~/.config/auren/config.toml)
    auren-ctl effort low|medium|high|xhigh|max   # Claude's effort (restarts it: a fresh conversation)
    auren-ctl safety ask|task|free|never    # how much it asks before risky actions (saved in config.toml)
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

The phone remote (phone_server.py) talks to the same pipeline. One turn runs at a time, from listening to
the end of the answer: while the desk has one, the phone gets "busy", and the other way round. Stop works
from both. A phone's turn is answered on the phone, as text: the PC doesn't speak it or show notices.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import itertools
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass

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
from .voice import TUNING, VOICES, tuning

SESSION = "daemon"          # the daemon's tool session (an `auren chat` has its own)
log = logging.getLogger("auren")
MIN_HOLD = 0.35          # shorter key taps are ignored
MAX_LISTEN = 120         # seconds; listening stops by itself (the speech service bills per second)
# When the user stops talking (no new words; see ears.Listening.quiet_seconds). With auto-send off, it reminds
# them to press the key, then sends by itself; nothing said at all ends the listen the same way.
REMIND_AFTER = 3         # seconds without new words: "Press Super+A to send"
SEND_AFTER = 12          # seconds without new words: send what was heard
QUIET_TICK = 0.2         # how often a listen checks for quiet
# Seconds between tries to download the voice when it failed (offline: a laptop's Wi-Fi isn't up yet); the
# last one repeats until it works.
VOICE_RETRY = (30, 60, 120, 300, 600)
RESTART_EXIT = 75        # macOS: the daemon restarts itself to turn on a permission (the launch agent starts it again)
ALREADY_SENT = 2         # seconds after a send by itself (of something heard) in which the talk key does nothing
# Said on its own, these start a fresh conversation instead of going to the brain.
FORGET = re.compile(r"^\W*(?:(?:ok(?:ay)?|hey|auren)\W+)*(?:start (?:fresh|over|a new conversation)|new conversation|"
                    r"forget (?:everything|all that|it all|our conversation)|clear (?:your|the) memory|reset (?:yourself|the conversation))"
                    r"\W*(?:please)?\W*$", re.I)


# A request with more than one part ("close it and open Spotify"): after a yes to one part, the brain must go on.
PHONE_KEYS = ("phone.enabled", "phone.port", "phone.lan")    # a reload that changes one restarts the phone server
# Told to the brain with a request from the phone: the user isn't at the laptop, and reads the reply.
PHONE_NOTE = "(From the user's phone: they may be away from the laptop. Your reply is shown on the phone as text.)"
COMPOUND = re.compile(r"\b(?:and|then|also|after|afterwards|before|while|plus|next|too|as well)\b|[,;]|[.?!]\s*\S", re.I)


@dataclass(eq=False)
class Turn:
    """One request, from its listen (or typed text) to the end of its answer."""
    origin: str = "desk"        # "desk" (keys, mascot, auren-ctl) or "phone"
    id: str = ""                # the phone's request id
    device: str = ""            # the phone's device id
    open: bool = True           # until the answer ends (or nothing was heard, or it was cancelled)


class Daemon:
    _open: tuple[Turn, ...] = ()          # admitted turns, oldest first: while one is open, anyone else gets "busy"
    _hearing: Turn | None = None          # the turn the current listen belongs to
    _finishing: dict = {}                 # listens whose transcript release() waits for -> their turns (a Stop
                                          # empties it; always replaced, never changed in place)
    phone = None                          # the phone server's hub (phone_server.Hub), when it runs
    phone_requests = None                 # what phones asked (phone_server.Requests): kept across its restarts
    _phone_lock: asyncio.Lock | None = None
    _request = ""                       # what the user asked, when Auren answered with questions (not told to the brain again)
    _handover_until: float | None = None  # the end of Codex's turn that a state refresh is set for
    _earlier: tuple[str, ...] = ()       # what happened to answers the brain wasn't told of yet (told with the next message)
    _sent_by_itself = float("-inf")       # when a listen last ended without the key (quiet), time.monotonic()
    _reminding = False                    # the "Press Super+A to send" reminder is up (state.json `remind`)
    _quiet_watch: asyncio.Task | None = None   # the current listen's _watch_quiet (kept: the loop holds tasks weakly)
    _voice_task: asyncio.Task | None = None    # _fetch_voice, while the voice is missing (one at a time)
    _voice_wake: asyncio.Event | None = None   # set by voice-load: try the cache now, not after the wait

    def __init__(self, speak: bool = True):
        self.settings = load_settings()
        self.profile = Profile()
        self.tools = Runtime(TOOLS_SOCKET)           # serving from serve() on
        s = self.settings
        self.tools.level = s["safety"]
        self.brain = Brain(self.tools, SESSION, mode=s["brain"], effort=s["effort"], claude_model=s["brain.claude_model"],
                           codex_model=s["brain.codex_model"], codex_effort=s["brain.codex_effort"])
        self._keys_lock = threading.Lock()            # macOS: one talk-key tap at a time (_listen_for_keys)
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
                                   "voices": list(VOICES),
                                   "tts": tuning(self.settings), "autosend": self.autosend,
                                   "autosendAfter": self.autosend_after, "origin": self._origin(), **extra}))
        tmp.replace(STATE_FILE)
        if self.phone:
            self.phone.state_changed()
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
                                  "voice", "voiceName", "voices", "tts", "safety", "autosend", "autosendAfter", "origin")}
        except (OSError, json.JSONDecodeError):
            extra = {}
        self.set_state(self.state, self.detail, **extra)

    # --- turns: one at a time, from the desk or the phone ---------------------------------------------

    def _live(self) -> tuple[Turn, ...]:
        self._open = tuple(t for t in self._open if t.open)
        return self._open

    def _origin(self) -> str | None:
        live = self._live()
        return live[0].origin if live else None

    def busy_for(self, origin: str, device: str = "") -> bool:
        """Is someone else's turn under way? Someone is the desk, or one phone: a second phone is someone else.
        The same someone's next request queues behind its first (the brain answers one at a time)."""
        return any((t.origin, t.device) != (origin, device) for t in self._live())

    def admit(self, turn: Turn) -> bool:
        """Start this turn, unless someone else has one open: then it's refused (busy). A new turn never
        replaces an open one: each stays counted until it closes."""
        if self.busy_for(turn.origin, turn.device):
            return False
        self._open = (*self._live(), turn)
        return True

    def open_turn(self, device: str, rid: str) -> bool:
        """Is this phone's request still being heard or answered (so its reply is still to come)?"""
        return any(t.origin == "phone" and t.device == device and t.id == rid for t in self._live())

    def _phone_turn(self) -> bool:
        return self._origin() == "phone"

    def _show(self, *args, **kwargs) -> None:
        if not self._phone_turn():           # the user is at the phone: nothing pops up on the PC
            notice.show(*args, **kwargs)

    def _notify(self, *args, **kwargs) -> None:
        if not self._phone_turn():
            notice.notify(*args, **kwargs)

    def _desk_refused(self) -> str:
        notice.show("Auren is busy with your phone", "auren", 2000)
        return "busy"

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
            from .voice import VoiceModelMissing
            try:
                await self._load_voice()
            except VoiceModelMissing:
                # An update that changed the voice model (Supertonic to Phonon in 0.1.7): a package update (yay)
                # runs no setup, so the daemon fetches the new model itself, in the background. Replies show as
                # text until it's ready.
                self._start_fetch()
                problems.append("downloading the voice: replies are text until it's ready")
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

    async def _load_voice(self) -> None:
        from .voice import Voice
        voice = await asyncio.to_thread(Voice, on_level=self.level.set, on_sentence=self._caption)
        voice.tune(self.settings)
        try:
            voice.set_voice(self.settings["voiceName"])
        except ValueError as e:
            log.warning("%s", e)
        self.voice = voice
        from .voice import retire_old_model
        await asyncio.to_thread(retire_old_model, log.info)   # an update from Supertonic: its 380 MB go

    def _start_fetch(self, cached: bool = False) -> None:
        self._voice_wake = asyncio.Event()
        if cached:
            self._voice_wake.set()
        self._voice_task = asyncio.create_task(self._fetch_voice(announce=not cached))

    async def _fetch_voice(self, announce: bool = True) -> None:
        """Download the voice model and its voices, then speak with them (replies were text until then).
        While that fails it keeps trying (VOICE_RETRY); a voice-load (`auren voice-model` filled the cache)
        tries the cache at once."""
        from .voice import VoiceModelMissing, download_model
        if announce:
            log.info("the voice model isn't downloaded: downloading it now (about 100 MB)")
            self._notify("Auren", "Downloading Auren's new voice (about 100 MB). Replies show as text until it's ready.",
                         always=True)
        for attempt in itertools.count():
            try:
                if self._voice_wake.is_set():
                    self._voice_wake.clear()
                    with contextlib.suppress(VoiceModelMissing):
                        await self._load_voice()
                if not self.voice:
                    await asyncio.to_thread(download_model, log.info)
                    await self._load_voice()
                break
            except Exception as e:  # noqa: BLE001 - offline, the Hub down: text only until it works
                wait = VOICE_RETRY[min(attempt, len(VOICE_RETRY) - 1)]
                log.error("couldn't download the voice, replies are text only; trying again in %d s: %s", wait, e)
                if attempt == 0:
                    self._notify("Auren", "Couldn't download Auren's voice yet (offline?). It keeps trying; replies "
                                 "are text until then.", always=True)
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._voice_wake.wait(), wait)
        log.info("voice downloaded and loaded")
        if self.state == "idle" and "downloading the voice" in self.detail:
            self.set_state("idle", "; ".join(p for p in self.detail.split("; ") if not p.startswith("downloading the voice")))

    def _voice_load(self) -> str:
        """voice-load: speak with the voice now if it wasn't there (`auren voice-model` just fetched it), not
        after the next try or a restart."""
        if self.voice:
            return "voice loaded"
        if not self.speak_enabled:
            return "voice off (started with --quiet)"
        if self._voice_task and not self._voice_task.done():
            self._voice_wake.set()
        else:
            self._start_fetch(cached=True)
        return "loading the voice"

    # --- commands -----------------------------------------------------------------------

    async def command(self, line: str) -> str:
        cmd, _, arg = line.strip().partition(" ")
        if cmd in ("talk", "talk-fresh", "listen", "listen-fresh") and not self.listening and time.monotonic() - self._sent_by_itself < ALREADY_SENT:
            return "already sent"                    # pressed to send just as the quiet sent it: not a new listen or a stop
        if cmd in ("talk", "talk-fresh", "listen", "listen-fresh") and not self.listening and self.state in ("thinking", "working"):
            await self.cancel()                      # the talk key stops an answer in progress
            return "stopped"
        if cmd in ("talk", "talk-fresh", "listen", "listen-fresh", "start", "stop") and self.busy_for("desk"):
            return self._desk_refused()              # the phone is talking or being answered
        if cmd == "talk":
            return await (self.release() if self.listening else self.press())
        if cmd == "talk-fresh":
            return await (self.release() if self.listening else self.press(fresh=True))
        if cmd in ("listen", "listen-fresh"):
            return await (self.release() if self.listening else self.press(cmd == "listen-fresh", hands_free=True))
        if cmd == "start":
            return await self.press()
        if cmd == "stop":
            return await self.release()
        if cmd == "voice-name":
            name, _, sample = arg.strip().partition(" ")
            try:
                name = self.set_voice_name(name)
            except ValueError as e:
                return str(e)
            if self.voice and self.state == "idle":
                asyncio.create_task(self.say(sample.strip() or f"Hi, this is {name}."))
            return "voice " + name
        if cmd == "tts":
            return self._tts(arg.split())
        if cmd == "voice-load":
            return self._voice_load()
        if cmd == "cancel":
            await self.cancel()
            return "cancelled"
        if cmd == "ask" and arg:
            turn = Turn("desk")
            if not self.admit(turn):
                return self._desk_refused()
            asyncio.create_task(self.respond(arg, source="typed", turn=turn))
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
            return self.yes_no(cmd == "yes", Turn("desk"), "button")
        if cmd == "status":
            return json.dumps({"state": self.state, "detail": self.detail,
                               "brain": "codex" if self.brain.using_backup else "claude",
                               "mode": self.brain.mode, "effort": self.brain.primary.effort,
                               "voice": self.voice_on, "safety": self.tools.level})
        if cmd == "reload":
            return self._reload()
        if cmd == "permissions" and MAC:          # macOS asks this process, the one that needs them
            return await self._permissions(arg.strip())
        if cmd == "quit":
            asyncio.get_running_loop().call_later(0.1, self.loop.stop)
            return "bye"
        return f"unknown command: {cmd}"

    def yes_no(self, yes: bool, turn: Turn, source: str, ask: str | None = None) -> str:
        """The answer buttons (the mascot's, auren-ctl yes|no, the phone's). The phone names the question it
        answers (`ask`, from the state it showed): a yes meant for another batch approves nothing."""
        waiting = self.tools.confirmations.batch(SESSION)
        if not waiting:
            return "nothing is waiting for an answer"
        if ask is not None and ask != ask_id(waiting):
            return "that question is no longer open"
        if not self.admit(turn):
            return "busy" if turn.origin == "phone" else self._desk_refused()
        # Only what is shown now: a second click, or a question asked meanwhile, isn't covered by this one.
        asyncio.create_task(self.answer(yes, source, {t for t, _ in waiting}, turn))
        return ("approved " if yes else "dropped ") + str(len(waiting))

    # --- the phone (phone_server.Hub calls these) --------------------------------------------------

    def _second_start(self, turn: Turn) -> bool:
        """The phone asks or starts again while its own turn is open: a second start is busy (it doesn't
        queue behind the first, as the desk's own does)."""
        return any(t.origin == "phone" and t.device == turn.device for t in self._live())

    def phone_ask(self, turn: Turn, text: str, source: str = "phone") -> str:
        if self._second_start(turn) or not self.admit(turn):
            return "busy"
        asyncio.create_task(self.respond(text, source=source, turn=turn))
        return "ok"

    async def phone_talk(self, turn: Turn, audio: asyncio.Queue, hands_free: bool = False, fresh: bool = False) -> str:
        if self.listening or self.state in ("thinking", "working") or self._second_start(turn) or not self.admit(turn):
            return "busy"
        out = await self.press(fresh, hands_free, turn=turn, audio=audio)
        return "ok" if out == "listening" else out

    def _hears(self, device: str) -> bool:
        h = self._hearing
        return bool(h and h.open and h.origin == "phone" and h.device == device)

    async def phone_talk_stop(self, device: str) -> str:
        if not self.listening or not self._hears(device):       # only the phone that started it ends it
            return "not listening"
        return await self.release()

    async def phone_gone(self, device: str) -> None:
        """A phone disconnected: a listen it was feeding can't go on (nor the transcript it was waiting for)."""
        finishing = any(t.origin == "phone" and t.device == device for t in self._finishing.values())
        if (self.listening and self._hears(device)) or finishing:
            await self.cancel()

    async def phone_paired_changed(self) -> None:
        """The first phone was paired or the last one unpaired: Claude's tools and rules change with it, and
        they're fixed when it starts, so it starts again (a fresh conversation), between answers."""
        async with self.busy:
            self._fresh()
            await asyncio.to_thread(self.brain.forget)
            log.info("phone paired or unpaired: fresh conversation, with send_to_phone %s",
                     "offered" if self.phone and self.phone.paired else "left out")

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
        if any(new.get(k) != old.get(k) for k in PHONE_KEYS):
            asyncio.create_task(self._restart_phone())
        self._refresh_state()
        return "Auren picked it up"

    def set_voice_name(self, name: str) -> str:
        """Pick the voice (`auren-ctl voice-name`, the bar, a phone's voice.set): one setting, which the phone
        also speaks with. Works without the PC's voice loaded. Returns the name; ValueError for an unknown one."""
        try:
            name = SETTING_KEYS["voiceName"].check(name)
        except ValueError as e:
            raise ValueError(f"no voice {name!r}: choose {e}") from None
        if self.voice:
            self.voice.set_voice(name)
        self._save({"voiceName": name})
        self._refresh_state()
        return name

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
        """tts speed 1.1 | volume 0.8 | pause 0.2 (several at once is fine) | reset"""
        if words == ["reset"]:
            values = tuning()
        else:
            if not words or len(words) % 2 or any(k not in TUNING for k in words[::2]):
                return "usage: tts speed {1}..{2} | volume {4}..{5} | pause {7}..{8} | reset".format(
                    *TUNING["speed"], *TUNING["volume"], *TUNING["pause"])
            values = tuning({**self.settings, **dict(zip(words[::2], words[1::2], strict=True))})
        self._save(values)
        if self.voice:
            self.voice.tune(values)
        self._refresh_state()
        return "tts " + " ".join(f"{k} {v}" for k, v in values.items())

    async def press(self, fresh: bool = False, hands_free: bool = False, turn: Turn | None = None,
                    audio: asyncio.Queue | None = None) -> str:
        if self.listening:
            return "already listening"
        turn = turn or Turn("desk")
        if not self.admit(turn):
            return self._desk_refused()
        self._hearing = turn
        if self.voice:
            self.voice.stop()            # talking over auren interrupts it
        if fresh:                        # restart the brain while the user speaks; the answer waits for it
            asyncio.create_task(self._forget_quietly())
        def ended() -> None:                 # hands-free: the user stopped talking, so send it
            if self.listening is session:
                asyncio.create_task(self.release())
        session = self.listening = Listening(self.vocab, on_level=self.level.set, on_end=ended if hands_free else None,
                                             audio=audio)
        session.on_partial = lambda _text: self._show_listening(session, fresh)
        self._reminding = False
        try:
            await self.listening.start()
        except Exception as e:  # noqa: BLE001
            self.listening = None
            self._notify("Auren couldn't start listening", str(e), "error")
            self._close(turn)
            self.set_state("error", str(e))
            return f"error: {e}"
        self.set_state("listening", "", fresh=fresh)
        self._show("Listening (fresh start)…" if fresh else "Listening…", "listening", 1200)
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
            self._notify("Auren stopped listening", str(e)[:200], "error")

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
                    self._show("Press Super+A to send", "listening", 3000)

    async def _set_claude(self, effort: str | None = None, model: str | None = None) -> None:
        what = " and ".join(x for x in (effort and f"{effort} effort", model and f"model {model}") if x)
        async with self.busy:              # after the current answer, if there is one
            if not self.listening:         # under a listen, it keeps the listen's state (see forget)
                self.set_state("thinking", f"Switching to {what}…")
            self._fresh()
            await self._restart_brain(self.brain.set_claude, effort, model)
            log.info("claude: %s (fresh conversation)", what)
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": f"({what})",
                           "tools": [], "brain": self._brain_name()})
        if not self.listening:
            self.idle()

    async def _warm(self) -> None:
        """After a mode change: start Claude if the mode now uses it, stop it if not (between answers)."""
        async with self.busy:
            try:
                await asyncio.to_thread(self.brain.warm)
            except Exception as e:  # noqa: BLE001 - retried on the next request
                log.error("brain didn't start: %s", e)

    async def _restart_brain(self, fn, *args) -> None:
        """A brain call that (re)starts Claude: one that can't start (not installed, say) is logged, and
        the next request says why. It mustn't leave the state stuck on "Starting fresh…"."""
        try:
            await asyncio.to_thread(fn, *args)
        except BrainError as e:
            log.error("brain didn't start: %s", e)

    def _brain_name(self) -> str:
        return "codex" if self.brain.using_backup else "claude"

    def _fresh(self) -> None:
        """A new conversation: what was asked in the old one can't be answered, and its notes are gone."""
        self._earlier, self._request = (), ""
        self.tools.forget(SESSION)

    async def _forget_quietly(self) -> None:
        async with self.busy:
            self._fresh()
            await self._restart_brain(self.brain.forget)
            log.info("fresh conversation")
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": "(fresh conversation)",
                           "tools": [], "brain": self._brain_name()})

    async def release(self) -> str:
        session, self.listening = self.listening, None
        if session is None:
            return "not listening"
        turn = self._hearing or Turn("desk")
        self.level.set(0)
        if session.held_seconds < MIN_HOLD:
            await session.cancel()
            self._close(turn)
            self.set_state("idle", "key tap ignored")
            return "too short"
        t = time.perf_counter()
        self._finishing = {**self._finishing, session: turn}     # another press may finish alongside it
        try:
            text = await session.finish()
        finally:
            stopped = session not in self._finishing
            self._finishing = {s: t for s, t in self._finishing.items() if s is not session}
        if stopped:                          # Stop came while the transcript was coming: nothing is sent
            self._close(turn)
            return "cancelled"
        heard_in = time.perf_counter() - t
        if session.error and not text:
            self._notify("Auren couldn't hear you", session.error, "error")
            self._close(turn)
            self.set_state("error", session.error)
            return "error"
        if not text:
            self._show("Didn't catch that", "unheard")
            self._close(turn)
            self.set_state("idle", "heard nothing")
            return "heard nothing"
        log.info("heard (%.2fs after release): %s", heard_in, text)
        source = "phone-voice" if turn.origin == "phone" else "voice"
        asyncio.create_task(self.respond(text, source=source, heard_s=heard_in, turn=turn))
        return "heard: " + text

    async def cancel(self) -> None:
        if self.listening or self._finishing:
            # The turn is the one heard now: a newer press while this awaits isn't this Stop's to close.
            session, hearing = self.listening, self._hearing
            self.listening, self._finishing = None, {}
            if session:
                await session.cancel()
            if hearing:
                self._close(hearing)            # an answer in progress closes its own turn when it ends
        self.level.set(0)
        if self.state in ("thinking", "working"):     # an answer in progress: stop the brain and its tools
            self.tools.cancel(SESSION)
            await asyncio.to_thread(self.brain.interrupt)
            self._show("Stopped", "stopped")
        if self.voice:
            self.voice.stop()
        self.set_state("idle", "cancelled")

    # --- answering ------------------------------------------------------------------------

    async def forget(self, say: bool = True, turn: Turn | None = None) -> None:
        async with self.busy:
            # Fresh (the panel, the phone) while someone is talking: the brain restarts under the listen, which
            # keeps its state, or the phone would take "thinking" for the end of its listen and stop recording.
            if not self.listening:
                self.set_state("thinking", "Starting fresh…")
            self._fresh()
            await self._restart_brain(self.brain.forget)
            log.info("fresh conversation")
            self._history({"at": time.time(), "source": "forget", "transcript": "", "reply": "(fresh conversation)",
                           "tools": [], "brain": self._brain_name()})
        if self.listening:
            return
        if say:
            await self._say("Okay, fresh start. I've forgotten our conversation.", turn)
        else:
            self.idle()

    async def respond(self, text: str, source: str, heard_s: float | None = None, turn: Turn | None = None) -> None:
        """Answer a request. Its turn (admitted by the caller) stays open until the answer ends."""
        turn = turn or Turn("desk")
        try:
            if source != "phone-share" and FORGET.match(text):
                await self.forget(turn=turn)
                return
            async with self.busy:                          # one request at a time; later ones wait
                # A plain yes or no to what it just asked is read here, not by the brain: the user's own words
                # are the proof, and nothing waits for a model to turn them into a tool call. Something shared
                # from the phone isn't their words: it never answers a question, whatever it says.
                if source != "phone-share" and self.tools.answerable(SESSION) and (answer := parse_answer(text)):
                    await self._answer(answer == "yes", source, text, heard_s, turn=turn)
                else:
                    await self._think(text, source, heard_s, turn=turn)
        finally:
            self._close(turn)

    def _close(self, turn: Turn) -> None:
        if turn.open:
            turn.open = False
            self._live()
            if self.phone:
                self.phone.state_changed()        # the phone sees the turn is over (the state may not change)
                if turn.origin == "phone":
                    self.phone.closed(turn)       # a turn that ended without a reply says so (no endless wait)

    async def answer(self, yes: bool, source: str, tokens: set[str] | None = None, turn: Turn | None = None) -> None:
        """A yes or no that didn't come as words (the bubble's buttons, auren-ctl yes|no, the phone), to these questions."""
        turn = turn or Turn("desk")
        try:
            async with self.busy:
                await self._answer(yes, source, tokens=tokens, turn=turn)
        finally:
            self._close(turn)

    async def _answer(self, yes: bool, source: str, text: str = "", heard_s: float | None = None,
                      tokens: set[str] | None = None, turn: Turn | None = None) -> None:
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
            await self._finish_answer(said, source, heard_s, t0, "Okay, I won't.", [], turn)
            return
        self.set_state("working", "Doing it", transcript=said)
        self._show("Doing it", "auren", always=True)
        outcomes = await asyncio.to_thread(self.tools.answer, SESSION, True, tokens)
        if self.tools.cancelled(SESSION):             # stopped meanwhile: what was running finished, nothing more
            self._earlier += ("The user said yes and then stopped you; some of it may have run.",)
            self._history({"at": time.time(), "source": source, "transcript": said, "reply": "(cancelled)",
                           "tools": ["confirm_action"], "heard_s": heard_s, "brain": "none"})
            return
        if outcomes and all(o.ok and o.say for o in outcomes) and self._request and not COMPOUND.search(self._request):
            self._earlier += (f"You asked {'; '.join(repr(q) for q in asked)}, the user said yes and it was done.",)
            await self._finish_answer(said, source, heard_s, t0, " ".join(o.say for o in outcomes), ["confirm_action"],
                                      turn)
            return
        # Anything else: the brain sees what happened, so it can go on with the task or say how it went.
        await self._think(said, source, heard_s, brain_text=_followup(outcomes, len(batch)), same_task=True,
                          already=["confirm_action"], turn=turn)

    async def _finish_answer(self, said: str, source: str, heard_s: float | None, t0: float, reply: str,
                             tools: list[str], turn: Turn | None = None) -> None:
        log.info("answered %r without the brain (%.2fs): %s", said, time.perf_counter() - t0, reply)
        self.tools.replied(SESSION, reply)
        self._history({"at": time.time(), "source": source, "transcript": said, "reply": reply, "tools": tools,
                       "heard_s": heard_s, "think_s": round(time.perf_counter() - t0, 2), "brain": "none"})
        await self._say(reply, turn)

    async def _think(self, text: str, source: str, heard_s: float | None = None, brain_text: str | None = None,
                     same_task: bool = False, already: list[str] | None = None, turn: Turn | None = None) -> None:
        """Ask the brain (the lock is held). text: what the user said; brain_text: what the brain gets, if not that."""
        t0 = time.perf_counter()
        # Counted here, before "thinking" shows: a cancel from now on sticks (the worker can't undo it). The judge
        # reads what the user said once: an answer's follow-up (brain_text) was counted by _answer.
        self.tools.new_turn(SESSION, same_task=same_task,          # once per user message, whichever brain answers
                            said="" if brain_text is not None else text)
        earlier, self._earlier = self._earlier, ()
        to_brain = (brain_text or text) if not earlier else f"({' '.join(earlier)})\n{brain_text or text}"
        if turn and turn.origin == "phone":
            to_brain = f"{PHONE_NOTE}\n{to_brain}"
        self.set_state("thinking", text, transcript=text)
        self._show("Thinking…", "thinking")
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
                        self._show(doing, kind, always=True)      # actions show on the OSD with the bubble up too
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
            await self._say(reply, turn)
        else:
            if turn and turn.origin == "phone" and self.phone:
                self.phone.reply(turn, "")
            self.idle()

    async def _say(self, text: str, turn: Turn | None) -> None:
        """The reply to a turn: a phone's is shown on the phone as text (it may read it out itself), not spoken here."""
        if turn and turn.origin == "phone":
            if self.phone:
                self.phone.reply(turn, text)
            self.idle()
            return
        await self.say(text)

    async def say(self, text: str) -> None:
        if not self.voice_on:
            self._notify("Auren", text)
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
                           until=min(p.at for p in waiting) + TOKEN_TTL,
                           ask=ask_id(self.tools.confirmations.batch(SESSION)))
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
        with self._keys_lock:              # the reload (loop thread) and _keys_once_allowed (a worker) both call this
            if getattr(self, "_hotkeys", None):
                self._hotkeys.stop()
            self._hotkeys = hotkey.start({"talk": self.settings["keys.talk"],
                                          "talk-fresh": self.settings["keys.talk_fresh"]},
                                         lambda command: send(command, timeout=10))

    async def _keys_once_allowed(self, every: float = 2.0) -> None:
        """macOS: the talk keys couldn't start (Accessibility not allowed yet). Wait for the user to allow it,
        then start them, so the setup window's Allow is enough and no restart is needed. The setup window's
        check wakes it as soon as it sees Accessibility allowed (_keys_wake)."""
        from .mac import permissions
        loop = asyncio.get_running_loop()
        self._keys_wake = asyncio.Event()
        self._keys_failed = False
        failed = 0
        while not getattr(self, "_hotkeys", None):
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._keys_wake.wait(), every)
            self._keys_wake.clear()
            if getattr(self, "_hotkeys", None):          # a reload started them meanwhile
                return
            if await loop.run_in_executor(None, permissions.accessibility):
                await loop.run_in_executor(None, self._listen_for_keys)
                if self._hotkeys:
                    log.info("Accessibility was allowed: the talk keys are on")
                    return
                failed += 1                   # allowed, yet the tap fails (or a key isn't a shortcut)
                if failed >= 3:
                    log.error("the talk keys still can't start: a restart may turn them on (auren doctor)")
                    self._keys_failed = True
                    return
                every = max(every, 10.0)

    # --- macOS: noticing permissions given while Auren runs -----------------------------------------

    def _grants(self):
        from .mac import permissions
        if getattr(self, "_grants_now", None) is None:
            self._grants_now = permissions.Grants()
        return self._grants_now

    async def _permissions(self, what: str) -> str:
        """`permissions [status|<name>]`: ask for them (all, or one), or only say how they are. The answer has
        what a new process already has ("waiting": a restart turns it on), what Auren restarts for once it's
        idle ("restarting"), and what a restart was tried for already but didn't turn on ("restart")."""
        from .mac import permissions
        loop = asyncio.get_running_loop()
        grants = self._grants()
        if what != "status":
            ask = (lambda: permissions.request_one(what)) if what in permissions.PANES else permissions.request
            await loop.run_in_executor(None, ask)
            grants.forget_fresh()
            if what in ("", *permissions.LATER):
                self._watch_grants()
        now = await loop.run_in_executor(None, grants.check)
        keys = bool(getattr(self, "_hotkeys", None))
        if keys:
            grants.done("keys")
        waiter = getattr(self, "_keys_task", None)
        if not keys and now.get("accessibility") and getattr(self, "_keys_wake", None):
            self._keys_wake.set()              # allowed just now: start the keys at once, not on the next round
        restarting, stuck = self._plan_restart(now.get("waiting", []))
        return json.dumps({**now, "program": permissions.program(), "talk_keys": keys,
                           "keys_waiting": bool(waiter and not waiter.done()),
                           "restarting": restarting, "restart": stuck})

    def _plan_restart(self, waiting: list[str]) -> tuple[list[str], list[str]]:
        """(what Auren restarts for once it's idle, what a restart can't turn on: the user's Restart button).
        One restart per grant: one that didn't help isn't tried again."""
        from .mac import hotkey, service
        grants = self._grants()
        names = list(waiting)
        if getattr(self, "_keys_failed", False) and not getattr(self, "_hotkeys", None) and hotkey.valid(
                {"talk": self.settings["keys.talk"], "talk-fresh": self.settings["keys.talk_fresh"]}):
            names.append("keys")
        tried = grants.tried()
        due = [n for n in names if n not in tried]
        if due and service.managed("daemon"):
            self._restart_for = sorted({*getattr(self, "_restart_for", ()), *due})
            if not getattr(self, "_restart_task", None):
                self._restart_task = asyncio.create_task(self._restart_when_idle())
            due = []
        return sorted(getattr(self, "_restart_for", ())), [n for n in names if n in tried] + due

    def _quiet(self) -> bool:
        """Nothing is going on that a restart would cut off: no turn, listen, answer or open question. A failed
        listen stays at "error" until the next turn: that's quiet too, or a restart could wait forever."""
        return (self.state in ("idle", "error") and not self.listening and not self._live() and not self.busy.locked()
                and not self.tools.batch(SESSION))

    async def _restart_when_idle(self, every: float = 1.0, settle: int = 3) -> None:
        """Restart (launchd starts the daemon again, as the launch agent says for a failed exit) once Auren
        has been idle for `settle` checks in a row, so a permission given meanwhile is turned on."""
        quiet = 0
        while quiet < settle:
            await asyncio.sleep(every)
            quiet = quiet + 1 if self._quiet() else 0
        names = list(self._restart_for)
        self._grants().mark(names)
        log.info("restarting to turn on %s", ", ".join(names))
        self.exit_code = RESTART_EXIT
        self.loop.stop()

    def _watch_grants(self, every: float = 3.0, for_s: float = 600, names: tuple[str, ...] | None = None) -> None:
        """After an Allow (or a start without Accessibility, whose prompt macOS shows by itself): check for a
        while even with the setup window closed, so a grant made in System Settings or macOS's prompt is
        noticed by itself. names: what to wait for (all of permissions.LATER by default)."""
        old = getattr(self, "_grant_watch", None)
        if old and not old.done():
            return

        async def watch():
            loop = asyncio.get_running_loop()
            end = time.monotonic() + for_s
            from .mac import permissions
            while time.monotonic() < end:
                await asyncio.sleep(every)
                now = await loop.run_in_executor(None, self._grants().check)
                self._plan_restart(now.get("waiting", []))
                if all(permissions.allowed(n, now.get(n)) for n in names or permissions.LATER) or getattr(
                        self, "_restart_task", None):
                    return
        self._grant_watch = asyncio.create_task(watch())

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
        self._phone_task = asyncio.create_task(self._serve_phone())
        log.info("listening on %s", SOCKET)
        if MAC:
            self._listen_for_keys()
            if not self._hotkeys:
                self._keys_task = asyncio.create_task(self._keys_once_allowed())
                self._watch_grants(names=("accessibility",))
        async with server:
            await server.serve_forever()


    async def _restart_phone(self) -> None:
        """phone.enabled, phone.port or phone.lan changed: stop the server (its sockets and the phones'
        connections close) and start it again as the settings now say, or not at all."""
        # One restart at a time: two at once could each start a server, and only one would be tracked (and
        # stopped by a later disable).
        self._phone_lock = self._phone_lock or asyncio.Lock()
        async with self._phone_lock:
            old = getattr(self, "_phone_task", None)
            if old:
                old.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await old
            self._phone_task = asyncio.create_task(self._serve_phone())

    async def _serve_phone(self) -> None:
        """The phone remote's server, on the Tailscale and private LAN addresses, while phone.enabled isn't false."""
        if self.settings.get("phone.enabled") is False:
            from .phone import STATUS
            STATUS.unlink(missing_ok=True)
            return
        try:
            from .phone_server import supervise
            await supervise(self)
        except Exception:  # noqa: BLE001 - the phone failing never takes the desk down
            log.exception("the phone server stopped")


def ask_id(batch: list[tuple[str, object]]) -> str:
    """Which yes/no question this is: the same batch of tokens, the same id. Not the tokens themselves (they
    are what a yes redeems), only a digest."""
    return hashlib.sha256(",".join(sorted(t for t, _ in batch)).encode()).hexdigest()[:12]


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
    d.exit_code = 0
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
        from .phone import STATUS as PHONE_STATUS
        PHONE_STATUS.unlink(missing_ok=True)
        if SOCKET.exists():
            SOCKET.unlink()
        lock.close()
    return d.exit_code
