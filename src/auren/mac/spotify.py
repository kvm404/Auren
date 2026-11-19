"""Spotify on macOS: AppleScript (Spotify's own dictionary) in place of MPRIS over D-Bus, and
`screencapture` of its window to find the Top result's play button. The rest of hands/spotify.py
(search links, the green button, checking what plays) is shared."""

from __future__ import annotations

import subprocess
import tempfile

from ..hands.spotify import _URI, SpotifyError, find_green_button

__all__ = ["running", "now_playing", "open_uri", "_click_top_result"]

_STATUS = {"playing": "Playing", "paused": "Paused", "stopped": "Stopped"}


def _osa(script: str, timeout: float = 5) -> str:
    try:
        p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise SpotifyError(f"AppleScript failed: {e}") from None
    if p.returncode != 0:
        raise SpotifyError((p.stderr or p.stdout).strip()[:200] or "AppleScript failed")
    return p.stdout.strip()


def running() -> bool:
    try:
        return _osa('application "Spotify" is running') == "true"
    except SpotifyError:
        return False


def now_playing() -> dict:
    if not running():
        return {}
    sep = "\x1f"
    try:
        out = _osa('tell application "Spotify"\n'
                   f'  set s to (player state as text) & "{sep}"\n'
                   '  if exists current track then\n'
                   f'    set s to s & (name of current track) & "{sep}" & (artist of current track) & "{sep}" & '
                   '(album of current track) & "' + sep + '" & (id of current track)\n'
                   '  end if\n'
                   '  return s\n'
                   'end tell')
    except SpotifyError:
        return {}
    parts = (out.split(sep) + ["", "", "", "", ""])[:5]
    return {"status": _STATUS.get(parts[0], parts[0].title()), "title": parts[1], "artist": parts[2],
            "album": parts[3], "track": parts[4]}


def open_uri(uri: str) -> None:
    if not _URI.match(uri):
        raise SpotifyError(f"not a Spotify link: {uri}")
    _osa(f'tell application "Spotify" to open location "{uri}"')


def _click_top_result() -> bool:
    from PIL import Image

    from ..hands import screen
    from ..hands.spotify import _window
    w = _window()
    x, y = w["at"]
    pw, ph = w["size"]
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        subprocess.run(["screencapture", "-x", "-t", "png", f"-R{x},{y},{pw},{ph}", f.name], check=True,
                       capture_output=True, timeout=10)
        img = Image.open(f.name)
        img.load()
    hit = find_green_button(img, min_side=round(24 * img.width / pw))
    if hit is None:
        return False
    screen.click_layout(x + hit[0] * pw / img.width, y + hit[1] * ph / img.height)
    return True
