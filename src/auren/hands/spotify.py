"""Spotify: play things and read what's playing, without typing or guessing where to click.

Why this exists: driven through the screen, Spotify's search box ate typed text, a single click on a
result only selected it (a song row needs a double-click), and the Connect button was one of several
identical icons. So this talks to the desktop client the way Spotify itself supports:

- MPRIS over D-Bus (`org.mpris.MediaPlayer2.spotify`): what's playing, and `OpenUri`, which opens any
  `spotify:` link inside the running client. `spotify:search:<words>` shows the search results page, so
  nothing is typed into the search box.
- The results page's "Top result" card always has a round green play button. It is found by colour in a
  screenshot of the window (so window size and sidebars don't matter) and clicked once.
- Whether it worked is read back from MPRIS (the track changed), not from a screenshot.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from urllib.parse import quote

from . import desktop, screen

BUS = "org.mpris.MediaPlayer2.spotify"
_OBJ = "/org/mpris/MediaPlayer2"
_PLAYER = "org.mpris.MediaPlayer2.Player"
_URI = re.compile(r"^spotify:(track|album|artist|playlist|show|episode|search|user):[\w:%.+-]+$")


class SpotifyError(RuntimeError):
    pass


def _bus(args: list[str], timeout: float = 5) -> str:
    p = subprocess.run(["busctl", "--user", *args], capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise SpotifyError((p.stderr or p.stdout).strip()[:200] or "busctl failed")
    return p.stdout


def running() -> bool:
    try:
        return BUS in _bus(["list", "--no-legend"])
    except (SpotifyError, subprocess.TimeoutExpired):
        return False


def now_playing() -> dict:
    """{status, title, artist, album, track} of the running client ({} when it isn't running)."""
    if not running():
        return {}
    try:
        meta = json.loads(_bus(["--json=short", "get-property", BUS, _OBJ, _PLAYER, "Metadata"]))["data"]
        status = json.loads(_bus(["--json=short", "get-property", BUS, _OBJ, _PLAYER, "PlaybackStatus"]))["data"]
    except (SpotifyError, json.JSONDecodeError, KeyError, subprocess.TimeoutExpired):
        return {}
    val = lambda k, d="": (meta.get(k) or {}).get("data", d)   # noqa: E731 - D-Bus variants wrap values
    artists = val("xesam:artist", [])
    return {"status": status, "title": val("xesam:title"), "artist": ", ".join(artists),
            "album": val("xesam:album"), "track": val("mpris:trackid")}


def _describe(n: dict) -> str:
    if not n or not n.get("title"):
        return "nothing is loaded in Spotify"
    return f"{n['status'].lower()}: {n['title']} by {n['artist']}" + (f" ({n['album']})" if n["album"] else "")


def ensure_running() -> None:
    """Start Spotify if it isn't running (and wait for its media service), then show its window."""
    if not running():
        desktop.launch_app("Spotify")
        for _ in range(60):
            if running():
                break
            time.sleep(0.5)
        else:
            raise SpotifyError("Spotify didn't start")
        time.sleep(2)             # the client takes a moment after registering before it takes links
    else:
        desktop.focus_window("Spotify")


def open_uri(uri: str) -> None:
    if not _URI.match(uri):
        raise SpotifyError(f"not a Spotify link: {uri}")
    _bus(["call", BUS, _OBJ, _PLAYER, "OpenUri", "s", uri])


def _window() -> dict:
    w = desktop.find_window("Spotify")
    ws = (w.get("workspace") or {}).get("id")
    if ws is not None and ws < 0:
        raise SpotifyError("Spotify's window is hidden (scratchpad)")
    return w


def find_green_button(img, min_side: int = 24) -> tuple[int, int] | None:
    """Centre (x, y) in pixels of the biggest roughly round Spotify-green blob in `img`, or None. The Top
    result card's play button is one: it is the only large green disc in the upper half of a results page."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(int)
    h, w, _ = a.shape
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    mask = (g > 170) & (r < 110) & (b < 140) & (g - r > 90)
    mask[int(h * 0.55):] = False        # not the "Playing on …" strip or the player bar at the bottom
    seen = np.zeros_like(mask)
    best = None
    for y0, x0 in zip(*np.nonzero(mask), strict=True):
        if seen[y0, x0]:
            continue
        stack, xs, ys = [(y0, x0)], [], []
        seen[y0, x0] = True
        while stack:
            y, x = stack.pop()
            xs.append(x); ys.append(y)
            for ny, nx in ((y + 1, x), (y - 1, x), (y, x + 1), (y, x - 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        bw, bh = max(xs) - min(xs) + 1, max(ys) - min(ys) + 1
        if min(bw, bh) >= min_side and 0.8 <= bw / bh <= 1.25 and (best is None or len(xs) > best[0]):
            best = (len(xs), (min(xs) + max(xs)) // 2, (min(ys) + max(ys)) // 2)
    return (int(best[1]), int(best[2])) if best else None


def _click_top_result() -> bool:
    """Click the green play button of the results page's Top result. False if there isn't one (yet)."""
    from PIL import Image
    w = _window()
    x, y = w["at"]
    pw, ph = w["size"]
    with tempfile.NamedTemporaryFile(suffix=".png") as f:
        subprocess.run(["grim", "-g", f"{x},{y} {pw}x{ph}", f.name], check=True, capture_output=True, timeout=10)
        img = Image.open(f.name)
        img.load()
    hit = find_green_button(img, min_side=round(24 * img.width / pw))
    if hit is None:
        return False
    screen.click_layout(x + hit[0] * pw / img.width, y + hit[1] * ph / img.height)
    return True


def play(query: str) -> str:
    """Search Spotify for `query` and play its Top result (a song, artist, album or playlist)."""
    query = " ".join(query.split())
    if not query:
        raise SpotifyError("say what to play")
    ensure_running()
    before = now_playing()
    open_uri(f"spotify:search:{_search(query)}")
    deadline = time.time() + 12
    clicked = False
    while time.time() < deadline and not clicked:
        time.sleep(1.0)               # the page renders asynchronously: look until the button is there
        clicked = _click_top_result()
    if not clicked:
        return (f"error: Spotify's results for '{query}' are showing, but no Top result play button was found. "
                "Use look_at_screen and click the green play circle on the Top result, or double-click a song row")
    for _ in range(10):
        time.sleep(0.5)
        n = now_playing()
        if n.get("status") == "Playing" and n.get("track") != before.get("track"):
            if _overlap(query, n):
                return f"now playing in Spotify, {_describe(n)}. This is the Top result for '{query}'"
            return (f"now playing in Spotify, {_describe(n)}, which doesn't look like '{query}'. "
                    "Tell the user what is playing; don't say it is what they asked for")
    n = now_playing()
    if n.get("status") == "Playing" and n.get("title") and _overlap(query, n):
        return f"now playing in Spotify, {_describe(n)}"
    return (f"error: clicked the Top result for '{query}' but Spotify is still {_describe(n)}. "
            "Use look_at_screen to see what is on the page")


def _search(query: str) -> str:
    """The text of a spotify:search: link. Everything is escaped, or "AC/DC" fails the link check."""
    return quote(" ".join(query.split()), safe="")


def _overlap(query: str, n: dict) -> bool:
    words = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
    have = set(re.findall(r"\w+", f"{n.get('title', '')} {n.get('artist', '')} {n.get('album', '')}".lower()))
    return bool(words & have)


def search(query: str) -> str:
    """Show the search results page for `query` (nothing is played)."""
    ensure_running()
    open_uri(f"spotify:search:{_search(query)}")
    return f"Spotify is showing search results for '{query}'"


def act(action: str, query: str = "") -> str:
    action = action.lower().strip()
    if action in ("play", "search_and_play"):
        if not query:
            raise SpotifyError("play needs what to play (a song, artist, album or playlist), or use the media tool to resume")
        return play(query)
    if action == "search":
        return search(query)
    if action in ("now_playing", "status"):
        return _describe(now_playing())
    if action == "open":
        ensure_running()
        open_uri(query)
        return f"opened {query} in Spotify"
    raise SpotifyError("action must be play, search, now_playing or open")
