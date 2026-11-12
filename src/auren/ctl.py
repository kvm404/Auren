"""auren-ctl: send one command to the daemon and print its reply (see docs/ipc.md).

    auren-ctl talk | talk-fresh | cancel | status | ask <text> | say <text> | voice off | ...

Key bindings, the mascot and the bar plugin call this, so it only imports what it needs.
"""

from __future__ import annotations

import socket
import sys

from .config import SOCKET


def send(line: str, timeout: float = 30) -> str:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(SOCKET))
        s.sendall(line.encode() + b"\n")
        chunks = []
        while chunk := s.recv(65536):
            chunks.append(chunk)
    return b"".join(chunks).decode().strip()


def main() -> int:
    line = " ".join(sys.argv[1:]).strip()
    if not line:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        print(send(line))
    except (FileNotFoundError, ConnectionRefusedError):
        start = "auren service start" if sys.platform == "darwin" else "systemctl --user start auren"
        print(f"auren daemon isn't running ({start})", file=sys.stderr)
        return 1
    except TimeoutError:
        print("auren daemon didn't answer", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
