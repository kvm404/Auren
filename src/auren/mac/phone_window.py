"""The Auren window's Phone page on macOS (in mascot/mac-window.qml): what the bar panel's Phone page is on
Omarchy. Where Auren listens (and a note when Tailscale is off), Get the app (its download's QR code), Pair a
phone (a one-time QR code with its countdown; Cancel ends the code at once) and the paired phones, each with
Unpair. The menu bar icon's Phone… opens the window on this page.

It reads what the panel reads: the daemon's phone.json, and `auren phone devices`, `app`, `revoke` (called
here in Python, on a thread when they take a lock or draw a QR code). Pairing runs `auren phone pair --json`
as a child process (QProcess), with the venv's Python, and reads its JSON lines; Cancel stops it with
SIGTERM, which ends the pairing. However the child ends (cancelled, killed, crashed), the window also ends
the code it showed and deletes that code's QR file, and a new pairing waits until the old child is gone.

Everything but bridge() is plain Python, so the tests run it without Qt.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .. import phone

PAIR_ARGS = ("-m", "auren.cli", "phone", "pair", "--json")
STOP_WAIT = 3000            # ms Cancel waits for the pairing to stop before it is killed


def via_name(via: str) -> str:
    return "Tailscale" if via == "tailscale" else "Wi-Fi"


def status_text(st: dict, alive: bool) -> str:
    """Where Auren listens for phones, as the panel says it."""
    if not alive:
        return "Auren is off, so the phone can't reach it."
    if st.get("on"):
        where = " and ".join(f"{via_name(a.get('via', ''))} ({a.get('ip')})" for a in st.get("addrs") or [] if a.get("ok"))
        return f"Listening on {where}."
    if st.get("idle") or "on" not in st:
        return "Not listening: no phone is paired. Pair one to start."
    return f"Not listening: {st.get('why') or 'the phone server is off'}."


def tailscale_warn(st: dict) -> str:
    """Tailscale is installed but off: phones reach the Mac only on this Wi-Fi for now."""
    why = (st.get("missing") or {}).get("tailscale", "")
    if not why or "isn't installed" in why:
        return ""
    return ("Tailscale is off on this Mac: phones reach it only on this Wi-Fi for now. Turn it on (its menu bar"
            " icon) to use the phone from anywhere; phones find the Mac there by themselves.")


def connected_via(st: dict, dev_id: str) -> str:
    rows = st.get("connected") or [] if st.get("on") else []
    return next((r.get("via", "") for r in rows if r.get("id") == dev_id), "")


def phone_rows(devices: list[dict], st: dict) -> list[dict]:
    """The paired phones for the window: {"id", "name", "detail", "connected"}."""
    out = []
    for d in devices:
        via = connected_via(st, d.get("id", ""))
        seen = f"Connected over {via_name(via)}" if via else f"Last seen {phone._ago(d.get('lastSeen') or 0)}"
        out.append({"id": d.get("id", ""), "name": d.get("name", ""), "connected": bool(via),
                    "detail": f"{seen} · paired {phone._ago(d.get('created') or 0)}"})
    return out


def file_url(path: str, stamp: object = "") -> str:
    """A local PNG for QML's Image (the runtime folder has a space in it: Application Support). The stamp makes
    a new code at the same path a new image."""
    return "file://" + quote(path) + (f"?{stamp}" if stamp != "" else "") if path else ""


def pair_command() -> tuple[str, list[str]]:
    """`auren phone pair --json` with the Python this runs on (the venv's, inside Auren.app)."""
    return sys.executable, list(PAIR_ARGS)


class Pairing:
    """One run of `auren phone pair --json`, as its lines say: stage "", "starting", "showing", "paired",
    "expired" or "error"; note (what happened); the code's QR, expiry and where it works."""

    def __init__(self):
        self.stage, self.note, self.shown = "", "", {}

    @property
    def busy(self) -> bool:
        return self.stage in ("starting", "showing")

    def start(self) -> bool:
        """A new pairing (False: one is under way)."""
        if self.busy:
            return False
        self.stage, self.note, self.shown = "starting", "", {}
        return True

    def line(self, text: str) -> str:
        """One line from the child. Returns what changed: "showing", "paired", "expired", "error" or ""."""
        import json
        try:
            m = json.loads(text)
        except ValueError:
            return ""
        if not isinstance(m, dict):
            return ""
        if m.get("uri"):
            self.stage, self.shown = "showing", m
        elif m.get("paired"):
            self.stage, self.note = "paired", f"Paired {m['paired'].get('name', 'a phone')}."
        elif m.get("expired"):
            self.stage, self.note = "expired", "The code expired: pair again for a new one."
        elif m.get("error"):
            self.stage, self.note = "error", str(m["error"])
        else:
            return ""
        return self.stage

    def exited(self) -> tuple[str, str]:
        """The child ended. Returns what it showed, to clean up (cleanup): (code, QR file)."""
        left = self.leftovers()
        if self.busy:
            self.stage, self.note = "error", "Pairing stopped. auren doctor says why."
        self.shown = {}
        return left

    def cancel(self) -> tuple[str, str]:
        """Cancel pressed: back to nothing. Returns what was showing, to clean up: (code, QR file)."""
        left = self.leftovers()
        if self.busy:
            self.stage = ""
        self.shown = {}
        return left

    def leftovers(self) -> tuple[str, str]:
        return self.code(), str(self.shown.get("qr") or "")

    def code(self) -> str:
        uri = self.shown.get("uri") or ""
        return (parse_qs(urlparse(uri).query).get("code") or [""])[0]

    def where(self) -> str:
        return " or ".join(f"{via_name(a.get('via', ''))} ({a.get('ip')})" for a in self.shown.get("where") or [])


def app_text(link: dict) -> str:
    return ((f"It downloads the Auren app {link['version']} for Android." if link.get("version")
             else "It opens Auren's latest release: download the auren-phone APK there.")
            + " Open the download and allow installing from your browser when Android asks. Then pair it here.")


def cleanup(code: str, qr: str) -> None:
    """End a pairing's code (only that one: a newer pairing stays) and delete its QR file (only a file
    auren phone pair made for one pairing)."""
    if code:
        phone.end_pairing(code)
    if qr and phone.is_pair_qr(qr):
        Path(qr).unlink(missing_ok=True)


def background(fn, *args) -> None:
    threading.Thread(target=fn, args=args, daemon=True).start()


def bridge(alive):
    """The page's QObject (`phone` in mac-window.qml). alive(): whether the daemon runs (the mascot knows)."""
    from PySide6.QtCore import Property, QObject, QProcess, QTimer, Signal, Slot

    class PhoneBridge(QObject):
        changed = Signal()
        _loaded = Signal("QVariant")           # from a thread: [what, result]

        def __init__(self):
            super().__init__()
            self._open = False
            self._st: dict = {}
            self._stamp = None
            self._devices: list[dict] = []
            self._alive = False
            self._pairing = Pairing()
            self._proc: QProcess | None = None
            self._stopping: QProcess | None = None     # a cancelled child, until it has exited
            self._buf = b""
            self._app: dict = {}
            self._app_shown = False
            self._unpair_ask = ""
            self._loaded.connect(self._take)

        # ---- what the page shows -------------------------------------------------------------
        @Property(bool, notify=changed)
        def open(self):
            return self._open

        @Property(bool, notify=changed)
        def alive(self):
            return self._alive

        @Property(str, notify=changed)
        def status(self):
            return status_text(self._st, self._alive)

        @Property(str, notify=changed)
        def warn(self):
            return tailscale_warn(self._st)

        @Property("QVariant", notify=changed)
        def phones(self):
            return phone_rows(self._devices, self._st)

        @Property(str, notify=changed)
        def pairStage(self):
            return self._pairing.stage

        @Property(bool, notify=changed)
        def stopping(self):
            return self._stopping is not None

        @Property(str, notify=changed)
        def pairNote(self):
            return self._pairing.note

        @Property(str, notify=changed)
        def pairQr(self):
            s = self._pairing.shown
            return file_url(s.get("qr", ""), s.get("expires", ""))

        @Property(float, notify=changed)
        def pairExpires(self):
            return float(self._pairing.shown.get("expires") or 0)

        @Property(str, notify=changed)
        def pairWhere(self):
            return self._pairing.where()

        @Property(str, notify=changed)
        def pairWarn(self):
            return str(self._pairing.shown.get("warn") or "")

        @Property(bool, notify=changed)
        def appShown(self):
            return self._app_shown and bool(self._app.get("qr"))

        @Property(str, notify=changed)
        def appQr(self):
            return file_url(self._app.get("qr", ""))

        @Property(str, notify=changed)
        def appUrl(self):
            return str(self._app.get("url") or "")

        @Property(str, notify=changed)
        def appText(self):
            return app_text(self._app)

        @Property(str, notify=changed)
        def unpairAsk(self):
            return self._unpair_ask

        # ---- keeping up --------------------------------------------------------------------------
        def poll(self) -> None:
            """phone.json and the daemon, every second while the page shows; the paired phones when
            phone.json changed (a phone came, went, or paired)."""
            if not self._open:
                return
            try:
                stamp = phone.STATUS.stat().st_mtime_ns
            except OSError:
                stamp = None
            now_alive = bool(alive())
            if stamp != self._stamp or now_alive != self._alive:
                self._stamp, self._alive = stamp, now_alive
                self._st = phone.status()
                self._devices = phone.device_rows()
                self.changed.emit()

        def _load_devices(self) -> None:
            self._devices = phone.device_rows()
            self.changed.emit()

        @Slot("QVariant")
        def _take(self, item) -> None:
            what, result = item
            if what == "app":
                self._app = result or {}
                self._app_shown = bool(self._app.get("qr"))
            elif what == "revoked":
                self._devices = phone.device_rows()
            self.changed.emit()

        # ---- the page ----------------------------------------------------------------------------
        @Slot()
        def showPage(self) -> None:
            """The Phone page is showing (the Auren window is open on it): keep up with phone.json."""
            if self._open:
                return
            self._open, self._stamp = True, None
            self.poll()
            self._load_devices()

        @Slot()
        def hidePage(self) -> None:
            """Leaving the page, or closing the window, ends a pairing that's showing (the code stops working),
            as the panel does when it closes."""
            if not self._open and self._proc is None:
                return
            self.cancelPairing()
            self._open, self._app_shown, self._unpair_ask = False, False, ""
            self.changed.emit()

        # ---- Get the app ---------------------------------------------------------------------------
        @Slot()
        def toggleApp(self) -> None:
            if self._app_shown:
                self._app_shown = False
                self.changed.emit()
                return
            if self._app.get("qr"):
                self._app_shown = True
                self.changed.emit()
                return

            def run():
                try:
                    result = phone.app_qr()
                except Exception as e:  # noqa: BLE001 - say it in the log, the window stays as it is
                    print(f"auren mascot: the app's QR code failed: {e!r}", file=sys.stderr, flush=True)
                    result = {}
                self._loaded.emit(["app", result])
            background(run)

        # ---- pairing ---------------------------------------------------------------------------------
        @Slot()
        def startPairing(self) -> None:
            # Not while the last one is still stopping: its cleanup must not meet the new one's.
            if self._stopping is not None or self._proc is not None or not self._pairing.start():
                return
            self._app_shown, self._buf = False, b""
            proc = QProcess(self)
            program, args = pair_command()
            proc.setProgram(program)
            proc.setArguments(args)
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
            proc.readyReadStandardOutput.connect(lambda p=proc: self._read(p))
            proc.finished.connect(lambda *_, p=proc: self._finished(p))
            proc.errorOccurred.connect(lambda err, p=proc: self._failed(p, err))
            self._proc = proc
            proc.start()
            self.changed.emit()

        def _read(self, proc) -> None:
            if proc is not self._proc:
                return
            self._buf += bytes(proc.readAllStandardOutput())
            *lines, self._buf = self._buf.split(b"\n")
            for raw in lines:
                if self._pairing.line(raw.decode("utf-8", "replace")) == "paired":
                    self._load_devices()
            self.changed.emit()

        def _finished(self, proc) -> None:
            if proc is self._stopping:               # a cancelled one is gone: a new pairing may start
                self._stopping = None
                proc.deleteLater()
                self.changed.emit()
                return
            if proc is not self._proc:
                return
            self._read(proc)
            left = self._pairing.exited()            # however it ended, its code and QR code go with it
            background(cleanup, *left)
            self._proc = None
            proc.deleteLater()
            self._load_devices()

        def _failed(self, proc, err) -> None:
            if err == QProcess.ProcessError.FailedToStart and proc is self._proc:
                background(cleanup, *self._pairing.exited())
                self._proc = None
                self.changed.emit()

        @Slot()
        def cancelPairing(self, wait: bool = False) -> None:
            left = self._pairing.cancel()
            proc, self._proc = self._proc, None
            if proc is not None and proc.state() != QProcess.ProcessState.NotRunning:
                self._stopping = proc
                proc.terminate()                     # SIGTERM: auren phone pair ends its code and exits

                def kill():
                    try:
                        if proc.state() != QProcess.ProcessState.NotRunning:
                            proc.kill()
                    except RuntimeError:             # it stopped and is gone already
                        pass
                QTimer.singleShot(STOP_WAIT, kill)
            elif proc is not None:
                proc.deleteLater()
            if wait:                                 # and here too, should it not answer
                cleanup(*left)
            else:
                background(cleanup, *left)
            self.changed.emit()

        def stop(self) -> None:
            """The mascot quits: a pairing that's open ends with it, before the process does."""
            proc = self._proc
            self.cancelPairing(wait=True)
            if proc is not None and not proc.waitForFinished(2000):
                proc.kill()

        # ---- unpairing ---------------------------------------------------------------------------------
        @Slot(str)
        def unpair(self, dev_id: str) -> None:
            """The first click asks ("Unpair? Click again"), the second unpairs."""
            if self._unpair_ask != dev_id:
                self._unpair_ask = dev_id
                self.changed.emit()
                return
            self._unpair_ask = ""
            self.changed.emit()
            background(lambda: (phone.revoke(dev_id), self._loaded.emit(["revoked", dev_id])))

    return PhoneBridge()
