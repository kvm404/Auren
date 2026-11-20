"""Browser tools: work in the user's real, logged-in browser through Open Browser Use.

- Any Chromium browser where Open Browser Use is set up, in any profile: obu.py finds the live hosts
  and which browser and profile each one is, and obu.choose picks one (the brain or config.toml can
  name a browser or profile). Every command goes to that host's own socket.
- auren works in tabs it opens itself (an Open Browser Use session, grouped as
  "auren" in the browser). It never closes or reuses the user's own tabs.
- Pages are read as text plus a numbered list of links, buttons and fields;
  `browser_click` / `browser_type` act on those numbers (or on visible text).
- Clicks and typing are real input (CDP Input events, inside the page only: never the user's mouse or
  keyboard), so rich text boxes and sites that ignore script events work. Where a real click can't land on
  the element (covered, off-screen), it falls back to a script click.
- Downloads are detected by watching that profile's download folder for a new finished file.
- If the browser isn't connected (not running, the profile has no window, or it just started and the
  extension hasn't connected yet), auren starts it or opens a window in that profile, waits for the
  extension (up to CONNECT_WAIT s) and retries once.
- If Open Browser Use isn't set up in that browser, the tools say so and point at browser_setup.
- Page text is untrusted: the tool server wraps it before the brain sees it.
"""

from __future__ import annotations

import json
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

from . import browsers
from . import obu as hosts

HOME = Path.home()
CONNECT_WAIT = 25
RECONNECT_WAIT = 3
SESSION = "auren"
MAX_TEXT = 5000


class BrowserError(RuntimeError):
    pass


class Link:
    """Auren's way into one browser profile: which one (a target), and its live host once connected."""

    def __init__(self, target: hosts.Target):
        self.kind = target.kind
        self.profile = target.profile
        self.conn = target.conn

    @property
    def key(self) -> str:
        return self.kind.key

    @property
    def ident(self) -> str:
        """Names the browser and profile, for binding a confirmation to the page it was asked on."""
        return f"{self.kind.key}:{self.profile.dir if self.profile else ''}"

    def label(self) -> str:
        return self.kind.name + (f" ({self.profile.name})" if self.profile else "")

    def profile_path(self) -> Path | None:
        """Its profile folder (bookmarks, download folder): the connected one, else the last one used."""
        if self.profile and self.profile.path.is_dir():
            return self.profile.path
        return browsers.profile_dir(self.kind)

    def __str__(self) -> str:
        return self.key


_state: dict = {"tab": None, "link": None}

SETUP_HINT = "Call browser_setup to set it up (it asks the user first), unless the user has said no."


def _browser(name: str | None = None) -> Link:
    """The browser to work in: the one named, else the one obu.choose picks (see its rules)."""
    try:
        return Link(hosts.choose(name or None))
    except browsers.UnknownBrowser as e:
        raise BrowserError(str(e)) from None


def _current() -> Link:
    """The browser auren's tab is in (or would be)."""
    return _state["link"] or _browser()


def ensure_connected(link: Link, wait: float = CONNECT_WAIT) -> None:
    """Make sure the browser's Open Browser Use extension is reachable: start the browser (or open a
    window in the profile) if it isn't connected, then wait for the extension to connect (it takes a
    few seconds after a start)."""
    missing = hosts.not_set_up(link.kind, link.profile)
    if missing:
        raise BrowserError(f"{missing}. {SETUP_HINT}")
    conn = _live(link, fresh=True)
    if conn:
        link.conn = conn
        return
    started = not browsers.running(link.kind)
    if not started:
        # A host reconnects now and then (a new socket): give it a moment before opening a window.
        conn = hosts.wait_for(link.kind, link.profile, RECONNECT_WAIT)
        if conn:
            link.conn = conn
            return
    if started or link.profile is not None:
        try:
            hosts.open_in(link.kind, link.profile)     # a running browser opens a window in that profile
        except hosts.SetupError as e:
            raise BrowserError(str(e)) from None
    conn = hosts.wait_for(link.kind, link.profile, wait)
    if conn:
        link.conn, link.profile = conn, conn.profile or link.profile
        time.sleep(1.5 if started else 0.5)            # let a fresh browser finish restoring its windows
        return
    where = link.label()
    if started:
        raise BrowserError(f"I started {where}, but its Open Browser Use extension didn't connect within "
                           f"{wait:.0f} seconds. Is the extension on there?")
    raise BrowserError(f"{where} is running, but its Open Browser Use extension isn't connected. Is it on "
                       "(and in the profile that's open)?")


def _live(link: Link, fresh: bool = False) -> hosts.Conn | None:
    return next((c for c in hosts.connections(fresh=fresh) if hosts.matches(c, link.kind, link.profile)), None)


class _NotConnected(BrowserError):
    pass


def obu(link: Link, *args: str, timeout: float = 30, retry: bool = True) -> dict:
    """Run an Open Browser Use command. If the browser isn't connected, start/wait for it and retry once."""
    if link.conn is None:
        ensure_connected(link)
    try:
        return _obu(link, *args, timeout=timeout)
    except _NotConnected:
        if not retry:
            raise
    link.conn = None
    hosts.forget_scan()
    ensure_connected(link)
    return _obu(link, *args, timeout=timeout)


def _obu(link: Link, *args: str, timeout: float = 30) -> dict:
    binary = hosts.cli()
    if binary is None or link.conn is None:
        raise BrowserError(f"Open Browser Use isn't installed. {SETUP_HINT}")
    cmd = [binary, *args, "--session-id", f"{SESSION}-{link.key}", "--socket", link.conn.socket]
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise BrowserError(f"the browser didn't answer within {timeout:.0f}s") from None
    out = (p.stdout or "").strip()
    try:
        d = json.loads(out) if out else {}
    except json.JSONDecodeError:
        d = {}
    if p.returncode != 0 or "error" in d:
        msg = (d.get("error") or {}).get("message") if isinstance(d.get("error"), dict) else (p.stderr or out)
        if "connect" in str(msg).lower() or "no active" in str(msg).lower() or "socket" in str(msg).lower():
            raise _NotConnected(f"{link.label()} isn't connected to auren (Open Browser Use)")
        raise BrowserError(f"browser: {str(msg).strip()[:300]}")
    return d.get("result", d)


def _eval(browser: Link, tab: int, js: str, timeout: float = 30):
    # In a block, so the helpers a script declares (const find, ...) don't stay behind in the page: declaring
    # them again in the next script would be a SyntaxError. A block still gives its last value.
    params = json.dumps({"expression": "{\n" + js + "\n}", "returnByValue": True, "awaitPromise": True})
    r = obu(browser, "cdp", "--tab-id", str(tab), "--method", "Runtime.evaluate", "--params", params, timeout=timeout)
    if r.get("exceptionDetails"):     # reply shape: {"result": {"type", "value"}, "exceptionDetails"?}
        raise BrowserError("page script failed: " + str(r["exceptionDetails"].get("text"))[:200])
    return (r.get("result") or {}).get("value")


def _tab(tab: int | None) -> int:
    t = tab or _state["tab"]
    if not t:
        raise BrowserError("no browser tab open yet: use browser_open first")
    return int(t)


# --- page reading -------------------------------------------------------------------

_READ_JS = r"""
(() => {
  const shown = el => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  // a link can be zero-sized while the image inside it is what you see
  const visible = el => shown(el) || [...el.querySelectorAll('img, svg')].some(shown);
  const imgLabel = el => { const img = el.querySelector && el.querySelector('img, svg');
    if (!img) return ''; const alt = img.getAttribute('alt') || img.getAttribute('aria-label') || '';
    if (alt) return 'image: ' + alt;
    const src = (img.getAttribute('src') || '').split('?')[0].split('/').pop();
    return src ? 'image: ' + src : ''; };
  const label = el => ([el.innerText, el.value, el.getAttribute('aria-label'), el.getAttribute('title'),
    el.getAttribute('placeholder'), el.getAttribute('alt'), imgLabel(el)].find(x => x && String(x).trim()) || '')
    .toString().trim().replace(/\s+/g, ' ').slice(0, 80);
  document.querySelectorAll('[data-auren-id]').forEach(e => e.removeAttribute('data-auren-id'));
  // rich text boxes (posts, chats, mail bodies) are named by their label first, not by the draft in them
  const editor = el => !['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
    && el.matches('[contenteditable]:not([contenteditable=false]), [role=textbox]');
  const boxLabel = el => (['aria-label', 'aria-placeholder', 'data-placeholder', 'placeholder', 'title']
    .map(a => el.getAttribute(a)).find(x => x && x.trim()) || label(el)).trim().replace(/\s+/g, ' ').slice(0, 80);
  const sel = 'a[href], button, [role=button], [role=link], [role=tab], [role=menuitem], input, select, textarea, summary, [onclick], '
    + '[contenteditable]:not([contenteditable=false]), [role=textbox]';
  const items = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el) || n >= 150) continue;
    const box = editor(el);
    if (box && el.parentElement && el.parentElement.isContentEditable) continue;    // a part of a bigger box
    const text = box ? boxLabel(el) : label(el); const type = el.getAttribute('type') || '';
    if (!text && !box && !['text','search','email','password','number'].includes(type) && el.tagName !== 'TEXTAREA') continue;
    n += 1; el.setAttribute('data-auren-id', String(n)); el.setAttribute('data-auren-label', text);
    const item = {id: n, kind: box ? 'textbox' : el.tagName.toLowerCase() + (type ? ':' + type : ''), text};
    if (el.href && !el.href.startsWith('javascript:')) item.href = el.href.slice(0, 160);
    if (el.target === '_blank') item.new_tab = true;
    if (el.hasAttribute('download')) item.download = true;
    items.push(item);
  }
  // Identical labels ("More actions" on every row): add the row's name so each can be told apart.
  // Walk up from the item until an ancestor holds other text; its first other line names the row.
  const counts = {}; items.forEach(i => counts[i.text] = (counts[i.text] || 0) + 1);
  for (const i of items) {
    if (counts[i.text] < 2) continue;
    const el = document.querySelector(`[data-auren-id="${i.id}"]`);
    let p = el.parentElement, ctx = '';
    for (let depth = 0; p && depth < 10 && !ctx; depth++, p = p.parentElement) {
      const lines = (p.innerText || '').split('\n').map(x => x.trim()).filter(x => x && x !== i.text);
      if (lines.length && lines.length < 40) ctx = lines[0].slice(0, 50);
    }
    if (ctx) { i.text = `${i.text} (${ctx})`; el.setAttribute('data-auren-label', i.text); }
  }
  const text = (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n');
  return {title: document.title, url: location.href, text: text.slice(0, __MAX__), truncated: text.length > __MAX__, items};
})()
""".replace("__MAX__", str(MAX_TEXT))


# Which element a target names: "pin:<id>" (pinned by a confirmation), an item number from the last read,
# or words from its label. Clicking, label_of and pin all use this, so they agree on the element.
_FIND_JS = r"""
const find = (target) => {
  if (target.startsWith('pin:')) return document.querySelector(`[data-auren-pin="${target.slice(4)}"]`);
  let el = /^\d+$/.test(target) ? document.querySelector(`[data-auren-id="${target}"]`) : null;
  if (!el) {
    const want = target.toLowerCase().trim();
    const all = [...document.querySelectorAll('[data-auren-id]')];
    const lab = e => (e.getAttribute('data-auren-label') || '').toLowerCase();
    const words = want.split(/[^a-z0-9]+/).filter(Boolean);
    el = all.find(e => lab(e) === want) || all.find(e => lab(e).includes(want))
      || all.find(e => words.every(w => lab(e).split(/[^a-z0-9]+/).includes(w)));
  }
  return el || null;
};
// fields only by pin or number
const field = (target) => target.startsWith('pin:')
  ? document.querySelector(`[data-auren-pin="${target.slice(4)}"]`)
  : document.querySelector(`[data-auren-id="${target}"]`);
"""

# Where a real click on el lands: the middle of its visible part, in the coordinates CDP's Input events take
# (CSS pixels of the viewport; a pinch zoom shifts them by the visual viewport's offset). null when a click
# there wouldn't reach el: zero-sized, off-screen, or covered (a cookie banner, an overlay). For typing
# (typing=true) the point must also not be on a link or button inside the box: that click wouldn't pass the
# risky-click check.
# It also watches that click (window.__aurenAim): whether the mouse got to el, and whether the press and the
# click did. Trusted events at that point that land on anything else are stopped before the page sees them;
# events anywhere else (the user's own clicks) pass. After 5 s it stops watching and nothing is pressed.
_AIM_JS = r"""
const ACTIONABLE = 'a[href], button, [role=button], [role=link], [role=menuitem], [role=tab], input, select, '
  + 'textarea, summary, [onclick]';
const aim = (el, typing) => {
  el.scrollIntoView({block: 'center', inline: 'center'});
  const size = q => q.width > 0 && q.height > 0;
  let r = el.getBoundingClientRect();
  if (!size(r)) r = [...el.querySelectorAll('img, svg')].map(e => e.getBoundingClientRect()).find(size) || r;
  const left = Math.max(r.left, 0), right = Math.min(r.right, innerWidth);
  const top = Math.max(r.top, 0), bottom = Math.min(r.bottom, innerHeight);
  if (right - left < 1 || bottom - top < 1) return null;
  const x = (left + right) / 2, y = (top + bottom) / 2;
  const on = n => {
    if (!n || !(n === el || el.contains(n))) return false;
    if (!typing) return true;
    const act = n.closest(ACTIONABLE);
    return !act || act === el || act.contains(el);
  };
  if (!on(document.elementFromPoint(x, y))) return null;
  if (window.__aurenAim) window.__aurenAim.off();
  const st = {moved: false, down: false, clicked: false, expired: false};
  const near = e => Math.abs(e.clientX - x) <= 2 && Math.abs(e.clientY - y) <= 2;
  const watch = e => {
    if (!e.isTrusted || !near(e)) return;
    const hit = on(e.target) || (!typing && e.composedPath().includes(el));
    if (e.type === 'mousemove') { st.moved = hit; return; }
    if (!hit) { e.preventDefault(); e.stopImmediatePropagation(); return; }
    if (e.type === 'click') st.clicked = true; else if (e.type.endsWith('down')) st.down = true;
  };
  const types = ['mousemove', 'pointerdown', 'mousedown', 'pointerup', 'mouseup', 'click'];
  types.forEach(t => window.addEventListener(t, watch, true));
  st.off = () => { st.moved = false; st.expired = true; types.forEach(t => window.removeEventListener(t, watch, true)); };
  setTimeout(st.off, 5000);
  window.__aurenAim = st;
  if (typing) window.__aurenFocusBefore = document.activeElement;
  const v = window.visualViewport;
  return {x: x - (v ? v.offsetLeft : 0), y: y - (v ? v.offsetTop : 0)};
};
"""

_AIM_READY_JS = "!!(window.__aurenAim && window.__aurenAim.moved && !window.__aurenAim.expired)"

_AIM_DONE_JS = """(() => { const s = window.__aurenAim; if (!s) return null; s.off(); delete window.__aurenAim;
  return {down: s.down, clicked: s.clicked}; })()"""


# --- real input (CDP Input events: they go to this tab's page only, never the user's mouse or keyboard) --

CTRL, META = 2, 4          # CDP's modifier bits


def _cdp(browser: Link, tab: int, method: str, params: dict, timeout: float = 15) -> dict:
    return obu(browser, "cdp", "--tab-id", str(tab), "--method", method, "--params", json.dumps(params), timeout=timeout)


def _mouse(browser: Link, tab: int, kind: str, x: float, y: float) -> None:
    p: dict = {"type": kind, "x": x, "y": y}
    if kind != "mouseMoved":
        p.update(button="left", buttons=1 if kind == "mousePressed" else 0, clickCount=1)
    _cdp(browser, tab, "Input.dispatchMouseEvent", p)


def _real_click(browser: Link, tab: int, point: dict | None) -> bool:
    """A real left click at `point` (from aim), pressed only once the mouse is seen to be over the element.
    True if the click reached it; False if nothing reached it, so the caller can fall back to a script click
    without clicking twice. If the press reached it but the click may not have finished, raises."""
    if not point:
        return False
    x, y = point["x"], point["y"]
    try:
        _mouse(browser, tab, "mouseMoved", x, y)
        ready = _eval(browser, tab, _AIM_READY_JS, timeout=5) is True
    except BrowserError:
        ready = False
    if not ready:
        try:
            _eval(browser, tab, _AIM_DONE_JS, timeout=5)
        except BrowserError:
            pass
        return False
    try:
        _mouse(browser, tab, "mousePressed", x, y)
    except BrowserError:
        pass                          # it may still have got there: what the page saw decides, below
    try:
        _mouse(browser, tab, "mouseReleased", x, y)       # always, so the mouse isn't left down
        released = True
    except BrowserError:
        released = False
    try:
        st = _eval(browser, tab, _AIM_DONE_JS, timeout=5)
    except BrowserError:
        st = None
    if st is None:                    # the page went away under the click: it navigated
        if released:
            return True
    elif st.get("clicked") or (st.get("down") and released):
        return True                   # (some items act on the press alone)
    elif not st.get("down"):
        return False
    raise BrowserError("the click may not have finished: read the page again")


_KEYS = {"Enter": ("Enter", 13, "\r"), "a": ("KeyA", 65, "")}


def _key(browser: Link, tab: int, key: str, modifiers: int = 0, commands: tuple[str, ...] = ()) -> None:
    """Press and release one key. Enter carries its text ("\\r"), which is what makes it insert a line or send.
    No nativeVirtualKeyCode: Chrome builds a Mac's native key event from it, and Mac key codes differ."""
    code, vk, text = _KEYS[key]
    down: dict = {"type": "keyDown" if text else "rawKeyDown", "key": key, "code": code,
                  "windowsVirtualKeyCode": vk, "modifiers": modifiers}
    if text:
        down.update(text=text, unmodifiedText=text)
    if commands:
        down["commands"] = list(commands)
    _cdp(browser, tab, "Input.dispatchKeyEvent", down)
    _cdp(browser, tab, "Input.dispatchKeyEvent", {"type": "keyUp", "key": key, "code": code,
                                                  "windowsVirtualKeyCode": vk, "modifiers": modifiers})


def _select_all(browser: Link, tab: int) -> None:
    """Ctrl+A (Cmd+A on a Mac). In a focused text box Chrome selects only that box's text. The selectAll
    command is what makes it work on a Mac, where the shortcut comes from the menu, not the page."""
    _key(browser, tab, "a", META if sys.platform == "darwin" else CTRL, ("selectAll",))


def _insert_text(browser: Link, tab: int, text: str) -> None:
    """Type text where the focus is, as the keyboard would (IME-style: no key events, so no Enter)."""
    _cdp(browser, tab, "Input.insertText", {"text": text})


def _wait_ready(browser: Link, tab: int, timeout: float = 15) -> None:
    end = time.time() + timeout
    time.sleep(0.6)
    while time.time() < end:
        try:
            if _eval(browser, tab, "document.readyState", timeout=5) == "complete":
                time.sleep(0.8)          # let client-side apps render after load
                return
        except BrowserError:
            pass
        time.sleep(0.4)


def _format(page: dict) -> str:
    lines = [f"Title: {page['title']}", f"URL: {_mask_url(page['url'])}", "", page["text"].strip()]
    if page.get("truncated"):
        lines.append("… (page text truncated)")
    lines += ["", "Clickable items (use the number with browser_click / browser_type):"]
    for it in page["items"]:
        extra = " [download]" if it.get("download") else ""
        if it.get("href") and (it["text"].startswith("image:") or not it["text"]):
            extra += f" → {_mask_url(it['href'])[:90]}"
        lines.append(f"[{it['id']}] {it['kind']}: {it['text'] or '(empty field)'}{extra}")
    return "\n".join(lines)


def _mask_url(url: str) -> str:
    """Hide long tokens in query strings (login tokens, signed links) when showing a URL."""
    return re.sub(r"([?&][^=&]+=)([^&]{40,})", r"\1…", url)


# --- tools ----------------------------------------------------------------------------

def browser_open(url: str, browser: str = "") -> str:
    """Open a URL (or 'bookmark:<name or folder/name>') in a new auren tab and read it."""
    b = _browser(browser)
    if "…" in url:
        raise BrowserError("that URL is shortened for display; open bookmarks with 'bookmark:<name>' instead")
    if url.startswith("bookmark:"):
        url = _bookmark_url(url.removeprefix("bookmark:"), b)
    elif not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
        url = "https://" + url
    obu(b, "name-session", "--name", "auren")
    try:
        r = obu(b, "open-tab", "--url", url, timeout=40)
    except BrowserError as e:
        # Seen once right after the user closed and reopened profiles: the extension's createTab came back
        # without a tab. It didn't happen again, so one more try.
        if "tab id" not in str(e):
            raise
        time.sleep(1)
        r = obu(b, "open-tab", "--url", url, timeout=40)
    tab = (r.get("tab") or {}).get("id")
    if not tab:
        raise BrowserError(f"couldn't open a tab: {str(r)[:200]}")
    _state.update(tab=tab, link=b)
    _wait_ready(b, tab)
    return f"(tab {tab} in {b.label()})\n" + browser_read(tab)


def _front(browser: Link, tab: int) -> None:
    """Bring auren's tab to the front of its window. Chrome doesn't lay out background tabs, so their
    visible text reads as empty (Google Drive rows, for example)."""
    try:
        if _eval(browser, tab, "document.visibilityState", timeout=5) != "visible":
            obu(browser, "cdp", "--tab-id", str(tab), "--method", "Page.bringToFront", "--params", "{}")
            time.sleep(0.8)
    except BrowserError:
        pass


def browser_read(tab: int | None = None) -> str:
    b, t = _current(), _tab(tab)
    _front(b, t)
    return _format(_eval(b, t, _READ_JS))


def browser_click(target: str, tab: int | None = None, expect_download: bool = False) -> str:
    """Click item number `target` from the last read (or the first item whose text matches `target`)."""
    b, t = _current(), _tab(tab)
    _front(b, t)
    before = _download_snapshot(b) if expect_download else None
    js = _FIND_JS + _AIM_JS + """
    ((target) => {
      const el = find(target);
      if (!el) return {ok: false};
      const text = (el.getAttribute('data-auren-label') || '').slice(0, 60);
      if (el.tagName === 'A' && el.target === '_blank' && el.href && !el.hasAttribute('download')) {
        el.scrollIntoView({block: 'center'});
        location.href = el.href; return {ok: true, text, navigated: true};
      }
      return {ok: true, text, point: aim(el, false)};
    })(%s)
    """ % json.dumps(str(target))
    r = _eval(b, t, js)
    if not r or not r.get("ok"):
        raise BrowserError(f"nothing to click matches '{target}' (read the page again for current numbers)")
    if not r.get("navigated") and not _real_click(b, t, r.get("point")):
        # The mouse can't reach it (covered, off-screen, zero-sized) or the click didn't get there: a script click.
        clicked = _eval(b, t, _FIND_JS + "((target) => { const el = find(target); if (!el) return false; "
                        "el.click(); return true; })(%s)" % json.dumps(str(target)))
        if not clicked:
            raise BrowserError(f"'{target}' went away before it could be clicked (read the page again)")
    msg = f"clicked “{r.get('text')}”"
    if before is not None:
        f = _wait_download(b, before)
        return msg + (f"; downloaded {f}" if f else
                      "; no download appeared within 60 s. The browser is probably showing its own popup (for "
                      "example 'Download multiple files: Allow / Block'), which auren can't click. Ask the user to "
                      "answer it, then check recent_downloads.")
    _wait_ready(b, t)
    return msg + "\n\n" + browser_read(t)


RISKY_CLICK = re.compile(r"\b(buy|pay|purchase|place order|order now|checkout|check out|send|post|publish|submit|"
                         r"delete|remove|transfer|confirm|unsubscribe|sign out|log ?out|book now)\b", re.I)


def label_of(target: str, tab: int | None = None) -> str | None:
    """The label of the item `target` would click, without clicking (for the safety check)."""
    b, t = _current(), _tab(tab)
    js = _FIND_JS + """((target) => { const e = find(target);
      return e ? (e.getAttribute('data-auren-label') || '') : null; })(%s)""" % json.dumps(str(target))
    return _eval(b, t, js)


def pin(target: str, is_field: bool = False, tab: int | None = None) -> str | None:
    """Mark the element `target` names, so a confirmed action reaches that same element even if the page
    is read again (which renumbers items) or changes around it. Returns "pin:<id>", or None if it's gone."""
    b, t = _current(), _tab(tab)
    js = _FIND_JS + """((target, nonce, isField) => { const e = isField ? field(target) : find(target);
      if (!e) return null; e.setAttribute('data-auren-pin', nonce); return 'pin:' + nonce; })(%s, %s, %s)""" % (
        json.dumps(str(target)), json.dumps(secrets.token_hex(6)), "true" if is_field else "false")
    return _eval(b, t, js)


def page() -> dict:
    """The auren tab now: browser, tab id and address (what a confirmation is bound to), and its title."""
    b, t = _current(), _tab(None)
    return {"browser": b.ident, "tab": t, "url": _eval(b, t, "location.href"), "title": _eval(b, t, "document.title")}


def value_of(target: str, tab: int | None = None) -> str | None:
    """The text a field holds now (what a Send click would send), or None if it's gone."""
    b, t = _current(), _tab(tab)
    js = _FIND_JS + """((target) => { const el = field(target);
      return el ? (el.isContentEditable ? el.innerText : String(el.value ?? '')) : null; })(%s)""" % json.dumps(str(target))
    return _eval(b, t, js)


def same_box(button: str, field: str, tab: int | None = None) -> bool:
    """Does this pinned button send this pinned field: not another form's, and the smallest part of the page
    around both holds no other text field (a form or a page with two composers: it may send the other)?"""
    b, t = _current(), _tab(tab)
    js = """((button, field) => {
      const q = p => document.querySelector(`[data-auren-pin="${p.slice(4)}"]`);
      const btn = q(button), el = q(field);
      if (!btn || !el) return false;
      if ((el.form || btn.form) && el.form !== btn.form) return false;
      let box = el.parentElement;
      while (box && !box.contains(btn)) box = box.parentElement;
      const fields = box ? [...box.querySelectorAll('input:not([type=hidden]):not([type=button]):not([type=submit])'
        + ':not([type=checkbox]):not([type=radio]), textarea, [contenteditable]:not([contenteditable=false])')] : [];
      return fields.length === 1 && fields[0] === el;
    })(%s, %s)""" % (json.dumps(button), json.dumps(field))
    return _eval(b, t, js) is True


def field_of(target: str, tab: int | None = None) -> dict | None:
    """A numbered field from the last read: its label and whether it's a search box (pressing Enter in a
    search box only searches; in other fields it can send or post)."""
    b, t = _current(), _tab(tab)
    js = _FIND_JS + """((target) => { const el = field(target);
      if (!el) return null;
      const words = [el.type, el.name, el.id, el.getAttribute('role'), el.getAttribute('aria-label'),
        el.getAttribute('placeholder'), el.form && el.form.getAttribute('role'), el.form && el.form.action]
        .filter(Boolean).join(' ').toLowerCase();
      const search = el.type === 'search' || /\\b(search|searchbox|query|find)\\b/.test(words) || el.name === 'q';
      return {label: el.getAttribute('data-auren-label') || '', search}; })(%s)""" % json.dumps(str(target))
    return _eval(b, t, js)


# Typing into a field: what kind it is, focusing it, selecting what it holds, and checking what it holds after.
# box(target) is the control that really takes the text: the field itself, or for a role=textbox wrapper the
# input or editable part inside it.
_BOX_JS = r"""
const TEXT_TYPES = ['text', 'search', 'email', 'password', 'number', 'tel', 'url'];
const plain = el => el.tagName === 'TEXTAREA' || (el.tagName === 'INPUT' && TEXT_TYPES.includes(el.type));
const rich = el => !['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName)
  && (el.isContentEditable || el.getAttribute('role') === 'textbox');
const takes = n => plain(n) || n.isContentEditable;
const box = (target) => {
  const el = field(target);
  if (!el || plain(el) || el.isContentEditable || !rich(el)) return el;
  const a = document.activeElement;
  if (a && el.contains(a) && takes(a)) return a;
  return [...el.querySelectorAll('input, textarea, [contenteditable]:not([contenteditable=false])')].find(takes) || el;
};
const setValue = (el, text) => {          // the script way: right for selects, dates and the like
  el.focus();
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
  setter && setter.set ? setter.set.call(el, text) : (el.value = text);
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
};
// Focus el and select what it holds, so the typed text replaces it (as setting the value did).
// 'elsewhere': our click moved the focus into another editor (Google Docs types into a hidden frame).
// Typing goes in there at the cursor. Only when the click moved it: an editor that already had the focus
// may be an unrelated one.
const focusIn = (el, reached) => {
  const before = window.__aurenFocusBefore;
  delete window.__aurenFocusBefore;
  if (!el) return {focus: 'none'};
  const inside = () => { const a = document.activeElement; return !!a && (a === el || el.contains(a)); };
  const a = document.activeElement;
  if (reached && !inside() && a && a !== document.body && a !== before
      && (a.tagName === 'IFRAME' || a.isContentEditable || a.getAttribute('role') === 'textbox')) return {focus: 'elsewhere'};
  if (!inside()) el.focus();
  if (!inside()) return {focus: 'none'};
  if (plain(el)) {
    el.select();
    let all = true;
    try { if (el.selectionStart !== null) all = el.selectionStart === 0 && el.selectionEnd === el.value.length; } catch (e) {}
    return {focus: 'in', selected: all || !el.value};
  }
  if (!el.innerText.trim()) return {focus: 'in', selected: true};
  if (!el.isContentEditable) return {focus: 'in', selected: false};
  const r = document.createRange(); r.selectNodeContents(el);
  const s = getSelection(); s.removeAllRanges(); s.addRange(r);
  const n = x => x.replace(/\s+/g, '').length;
  return {focus: 'in', selected: n(s.toString()) >= n(el.innerText)};
};
// Did it take? ok: the box holds the text. lines: it does, but with its line breaks joined. differs: the box
// changed but reads otherwise (now: what it reads). no: nothing changed. unchecked: can't be read.
const typed = (el, text, focus, before) => {
  if (!el) return {r: 'no'};
  if (plain(el)) {
    if (el.value !== (el.tagName === 'INPUT' ? text.replace(/[\r\n]/g, '') : text)) return {r: 'no'};
    el.dispatchEvent(new Event('change', {bubbles: true}));     // as a person leaving the field would
    return {r: 'ok'};
  }
  if (focus === 'elsewhere') { const a = document.activeElement; return {r: a && a !== document.body ? 'unchecked' : 'no'}; }
  const canon = s => String(s).replace(/[\u200b-\u200d\u2060\ufeff]/g, '').replace(/[\u2018\u2019\u201a\u201b]/g, "'")
    .replace(/[\u201c\u201d\u201e\u201f]/g, '"').replace(/[^\S\n]+/g, ' ').replace(/\s*\n\s*/g, '\n').trim();
  const flat = s => s.replace(/\s+/g, ' ');
  const got = canon(el.innerText), want = canon(text);
  if (got === want) return {r: 'ok'};
  if (flat(got) === flat(want)) return {r: 'lines'};
  if (got !== canon(before)) return {r: 'differs', now: flat(got).slice(0, 120)};
  return {r: 'no'};
};
"""


def _box(browser: Link, tab: int, call: str, *args) -> object:
    return _eval(browser, tab, _FIND_JS + _AIM_JS + _BOX_JS + f"({call})(" + ", ".join(json.dumps(a) for a in args) + ")")


def _type_real(b: Link, t: int, target: str, text: str, prep: dict) -> dict | None:
    """Type with real input: click the field (or focus it), select what it holds, insert the text.
    Returns what typed() found (r: ok, lines, differs, unchecked), or None when nothing took (the caller
    falls back). Never presses a key that could send: Enter only comes with submit, in browser_type."""
    reached = _real_click(b, t, prep.get("point"))
    f = _box(b, t, "(target, reached) => focusIn(box(target), reached)", target, reached)
    if not f or f["focus"] == "none":
        return None

    def select() -> None:
        if f["focus"] == "in" and not f["selected"]:
            _select_all(b, t)

    def check() -> dict:
        return _box(b, t, "(target, text, focus, before) => typed(box(target), text, focus, before)",
                    target, text, f["focus"], prep.get("before", "")) or {"r": "no"}
    try:
        select()
        _insert_text(b, t, text)
        got = check()
    except BrowserError:
        got = {"r": "no"}
    if got["r"] == "no" and prep["mode"] == "rich":
        # Nothing changed: the page's own insert command, over what's there again (selected first, and only
        # when nothing went in, so the text can't be typed twice).
        f = _box(b, t, "(target) => focusIn(box(target), false)", target)
        if not f or f["focus"] != "in":
            return None
        select()
        _eval(b, t, "document.execCommand('insertText', false, %s)" % json.dumps(text))
        got = check()
    return None if got["r"] == "no" else got


def browser_type(target: str, text: str, submit: bool = False, tab: int | None = None) -> str:
    b, t = _current(), _tab(tab)
    _front(b, t)                      # real input needs the tab laid out, so in front
    prep = _box(b, t, """(target) => { const el = box(target);
      if (!el) return null;
      if (!plain(el) && !rich(el)) return {mode: 'set'};
      return {mode: plain(el) ? 'plain' : 'rich', point: aim(el, true), before: plain(el) ? '' : el.innerText}; }""",
                str(target))
    if not prep:
        raise BrowserError(f"no field number {target} (read the page again)")
    got = _type_real(b, t, str(target), text, prep) if prep["mode"] != "set" else None
    if got is None and prep["mode"] != "rich":
        if not _box(b, t, "(target, text) => { const el = box(target); if (!el) return false; setValue(el, text); "
                    "return true; }", str(target), text):
            raise BrowserError(f"no field number {target} (read the page again)")
        got = {"r": "ok"}
    if got is None:
        raise BrowserError("that box can't be typed into from the page: click it with click_at and use type_text")
    note = {"lines": "typed, but the box joined the lines (typed as one line)",
            "differs": f"typed, but the box now reads “{got.get('now', '')}”: check it",
            "unchecked": "typed at the cursor, but couldn't select or check what the box already held (it may "
                         "have added to existing text): read the page to check"}.get(got["r"])
    if submit:
        if note:
            # What would be sent isn't (only) what was asked for: leave it for a look first. Errors aren't
            # wrapped as page text, so no page text in this one.
            raise BrowserError("typed, but didn't send: " + {
                "lines": "the box joined the lines (typed as one line)",
                "differs": "the box reads differently from what was typed",
                "unchecked": "couldn't check what the box holds (it may have added to existing text)"}[got["r"]]
                + ". Read the page to check")
        # One way to send, never both (a page that sends on Enter and on submit would send twice).
        sent = _box(b, t, """(target) => { const el = box(target);
          if (!el) return 'gone';
          if (el.form) { el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit(); return 'form'; }
          const a = document.activeElement;
          if (!(a && (a === el || el.contains(a)))) el.focus();
          return 'key'; }""", str(target))
        if sent == "gone":
            raise BrowserError(f"typed, but field {target} went away before Enter could be pressed (read the page again)")
        if sent == "key":
            _key(b, t, "Enter")
        _wait_ready(b, t)
        return "typed and submitted\n\n" + browser_read(t)
    return note or "typed"


def browser_scroll(direction: str = "down", tab: int | None = None) -> str:
    b, t = _current(), _tab(tab)
    _eval(b, t, f"window.scrollBy(0, {'' if direction == 'down' else '-'}window.innerHeight * 0.9)")
    time.sleep(0.6)
    return browser_read(t)


# --- bookmarks ---------------------------------------------------------------------------

def _bookmarks(browser: Link) -> list[tuple[str, str]]:
    profile = browser.profile_path()
    path = profile / "Bookmarks" if profile else None
    if path is None or not path.exists():
        return []
    data = json.loads(path.read_text())
    out = []
    def walk(node, folders):
        if node.get("type") == "folder":
            for c in node.get("children", []):
                walk(c, folders + [node.get("name", "")])
        elif node.get("url"):
            out.append(("/".join(f for f in folders[1:] + [node.get("name", "")] if f), node["url"]))
    for root in data.get("roots", {}).values():
        if isinstance(root, dict):
            walk(root, [])
    return out


def bookmarks(query: str = "", browser: str = "") -> str:
    b = _browser(browser)
    words = query.lower().split()
    hits = [(name, url) for name, url in _bookmarks(b) if all(w in name.lower() for w in words)]
    if not hits:
        return f"no {b.label()} bookmarks match '{query}'"
    return "\n".join(f"{name} → {_mask_url(url)}" for name, url in hits[:30]) + \
        ("\n(open one with browser_open('bookmark:<name>'))")


def _bookmark_url(name: str, browser: Link) -> str:
    want = name.strip().lower()
    marks = _bookmarks(browser)
    for test in (lambda n: n.lower() == want, lambda n: n.lower().endswith("/" + want), lambda n: want in n.lower()):
        hits = [url for n, url in marks if test(n)]
        if hits:
            return hits[0]
    raise BrowserError(f"no bookmark called '{name}' in {browser.label()}")


# --- downloads ------------------------------------------------------------------------------

def download_dir(browser: Link) -> Path:
    try:
        profile = browser.profile_path()
        if profile is None:
            raise OSError("no profile")
        prefs = json.loads((profile / "Preferences").read_text())
        d = prefs.get("download", {}).get("default_directory") or prefs.get("savefile", {}).get("default_directory")
        if d:
            return Path(d)
    except (OSError, json.JSONDecodeError):
        pass
    return HOME / "Downloads"


def _download_snapshot(browser: Link) -> dict[str, float]:
    d = download_dir(browser)
    return {p.name: p.stat().st_mtime for p in d.iterdir()} if d.is_dir() else {}


def _wait_download(browser: Link, before: dict[str, float], timeout: float = 60) -> str | None:
    d = download_dir(browser)
    end = time.time() + timeout
    while time.time() < end:
        time.sleep(0.5)
        new = [p for p in d.iterdir() if p.name not in before or p.stat().st_mtime > before[p.name] + 0.1]
        partial = [p for p in new if p.suffix in (".crdownload", ".part", ".tmp")]
        done = [p for p in new if p not in partial and not p.name.startswith(".")]
        if done and not partial:
            return str(max(done, key=lambda p: p.stat().st_mtime))
    return None


def recent_downloads(browser: str = "", minutes: int = 30) -> str:
    d = download_dir(_browser(browser))
    cutoff = time.time() - minutes * 60
    files = sorted((p for p in d.iterdir() if p.stat().st_mtime > cutoff and p.is_file()),
                   key=lambda p: -p.stat().st_mtime)
    return "\n".join(f"{p} ({p.stat().st_size // 1024} KB)" for p in files[:15]) or f"no downloads in the last {minutes} minutes"


def browser_close(keep_current: bool = False) -> str:
    """Close the tabs auren opened (never the user's own). keep_current leaves the last one open."""
    b = _state["link"] or _browser()
    if b.conn is None and _live(b) is None:      # nothing to close, and no reason to start the browser for it
        return "no auren tabs are open (the browser isn't connected)"
    keep = [{"tabId": _state["tab"], "status": "deliverable"}] if keep_current and _state["tab"] else []
    obu(b, "finalize-tabs", "--keep", json.dumps(keep))
    if not keep_current:
        _state["tab"] = None
    return "closed auren's browser tabs" + (" (kept the current one)" if keep_current else "")
