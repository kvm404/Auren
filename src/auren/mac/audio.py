"""The microphone and the speaker on macOS, through sounddevice (PortAudio on Core Audio).

They stand in for the `pw-record` and `pw-cat` processes the Linux code starts, with the parts of the
process interface that ears.py and voice.py use, so those modules don't change:

    mic = Mic(16000)                 # like asyncio's Process: await mic.stdout.read(n), send_signal, wait, kill
    player = Player(44100)           # like Popen: player.stdin.write(bytes), stdin.close(), wait, poll, kill

Core Audio converts between these rates and the device's own. Without the Microphone permission macOS
gives silence rather than an error; `auren doctor` checks the permission.
"""

from __future__ import annotations

import asyncio
import threading


class Mic:
    """16-bit mono from the default input, read like a process's stdout."""

    def __init__(self, rate: int, block: int = 800):
        import sounddevice as sd

        self.loop = asyncio.get_running_loop()
        self.returncode: int | None = None
        self.stdout = self
        self._queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._buf = bytearray()
        self._eof = False
        try:
            self._stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", blocksize=block,
                                             callback=self._heard)
            self._stream.start()
        except Exception as e:  # noqa: BLE001 - PortAudio errors have their own type
            raise RuntimeError(f"couldn't open the microphone: {e}") from None

    def _heard(self, data, frames, when, status) -> None:      # PortAudio's thread
        self.loop.call_soon_threadsafe(self._queue.put_nowait, bytes(data))

    async def read(self, n: int) -> bytes:
        while len(self._buf) < n and not self._eof:
            chunk = await self._queue.get()
            if chunk is None:
                self._eof = True
            else:
                self._buf.extend(chunk)
        out = bytes(self._buf[:n])
        del self._buf[:n]
        return out

    def _close(self) -> None:
        if self.returncode is not None:
            return
        self.returncode = 0
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001
            pass
        self.loop.call_soon_threadsafe(self._queue.put_nowait, None)

    def send_signal(self, sig) -> None:
        self._close()

    def kill(self) -> None:
        self._close()

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0


class Player:
    """32-bit float mono to the default output. Writes don't block: the audio is queued and played by
    PortAudio's callback. close() then wait() lets it finish; kill() cuts it off."""

    def __init__(self, rate: int):
        import sounddevice as sd

        self.stdin = self
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._closed = False
        self._killed = False
        self._drained = threading.Event()
        self._stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="float32", callback=self._play)
        self._stream.start()

    def _play(self, out, frames, when, status) -> None:     # PortAudio's thread
        n = len(out)
        with self._lock:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            done = self._closed and not self._buf
        out[:len(chunk)] = chunk
        if len(chunk) < n:
            out[len(chunk):] = b"\0" * (n - len(chunk))
        if done:
            self._drained.set()

    # the stdin side
    def write(self, data: bytes) -> int:
        if self._killed:
            raise BrokenPipeError("the player was stopped")
        with self._lock:
            self._buf.extend(data)
        return len(data)

    def flush(self) -> None:
        if self._killed:
            raise BrokenPipeError("the player was stopped")

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if not self._buf:
                self._drained.set()

    # the process side
    def wait(self, timeout: float | None = None) -> int:
        self._drained.wait(timeout)
        self._finish()
        return 0

    def poll(self) -> int | None:
        return 0 if self._killed or self._drained.is_set() else None

    def kill(self) -> None:
        self._killed = True
        with self._lock:
            self._buf.clear()
            self._closed = True
        self._drained.set()
        self._finish()

    def _finish(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:  # noqa: BLE001 - stopped twice, or the device went away
            pass
