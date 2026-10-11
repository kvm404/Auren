"""Browser tools: work in the user's real, logged-in browser through Open Browser Use.

- Each browser runs its own Open Browser Use native host. Helium (the default) uses the
  default /tmp/open-browser-use; Chrome's uses /tmp/open-browser-use-chrome (its manifest
  must point at a wrapper that passes --socket-dir). Brave isn't supported: its host would
  share Helium's socket folder, so "brave" would silently drive Helium.
- auren works in tabs it opens itself (an Open Browser Use session, grouped as
  "auren" in the browser). It never closes or reuses the user's own tabs.
- Pages are read as text plus a numbered list of links, buttons and fields;
  `browser_click` / `browser_type` act on those numbers (or on visible text).
- Downloads are detected by watching the download folder for a new finished file.
- If the browser isn't connected (not running, or just started and the extension
  hasn't connected yet), auren starts it if needed, waits for the extension
  (up to CONNECT_WAIT s) and retries once.
- Page text is untrusted: the tool server wraps it before the brain sees it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

HOME = Path.home()
DEFAULT = "helium"
SOCKET_DIRS = {"helium": "/tmp/open-browser-use", "chrome": "/tmp/open-browser-use-chrome"}
BOOKMARK_FILES = {"helium": HOME / ".config/net.imput.helium/Default/Bookmarks",
                  "chrome": HOME / ".config/google-chrome/Default/Bookmarks"}
PREFERENCES = {"helium": HOME / ".config/net.imput.helium/Default/Preferences",
               "chrome": HOME / ".config/google-chrome/Default/Preferences"}
EXECUTABLES = {"helium": "helium", "chrome": "google-chrome-stable"}
PROCESS_NAMES = {"helium": "helium", "chrome": "chrome"}
CONNECT_WAIT = 25
SESSION = "auren"
MAX_TEXT = 5000

_state = {"tab": None, "browser": None}


class BrowserError(RuntimeError):
    pass


def _browser(name: str | None) -> str:
    n = (name or _state["browser"] or DEFAULT).strip().lower()
    n = {"google chrome": "chrome", "google-chrome": "chrome"}.get(n, n)
    if n not in SOCKET_DIRS:
        raise BrowserError(f"browser must be {' or '.join(SOCKET_DIRS)}, not {name}")
    return n


def _running(browser: str) -> bool:
    return subprocess.run(["pgrep", "-x", PROCESS_NAMES[browser]], capture_output=True).returncode == 0


def ensure_connected(browser: str, wait: float = CONNECT_WAIT) -> None:
    """Make sure the browser's Open Browser Use extension is reachable: start the browser if it isn't
    running, then wait for the extension to connect (it takes a few seconds after a start)."""
    try:
        obu(browser, "ping", timeout=5, retry=False)
        return
    except BrowserError:
        pass
    started = False
    if not _running(browser):
        from . import desktop
        desktop.hide_scratchpad()            # new windows open on a showing special workspace
        desktop.spawn(["uwsm-app", "--", EXECUTABLES[browser]])
        started = True
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(0.5)
        try:
            obu(browser, "ping", timeout=5, retry=False)
            time.sleep(0.5 if not started else 1.5)    # let a fresh browser finish restoring its windows
            return
        except BrowserError:
            continue
    name = browser.title()
    if started:
        raise BrowserError(f"I started {name}, but its Open Browser Use extension didn't connect within "
                           f"{wait:.0f} seconds. Is the extension installed and on in {name}?")
    raise BrowserError(f"{name} is running, but its Open Browser Use extension isn't connected. Is it on "
                       "(and in the profile that's open)?")


class _NotConnected(BrowserError):
    pass


def obu(browser: str, *args: str, timeout: float = 30, retry: bool = True) -> dict:
    """Run an Open Browser Use command. If the browser isn't connected, start/wait for it and retry once."""
    try:
        return _obu(browser, *args, timeout=timeout)
    except _NotConnected:
        if not retry:
            raise
    ensure_connected(browser)
    return _obu(browser, *args, timeout=timeout)


def _obu(browser: str, *args: str, timeout: float = 30) -> dict:
    cmd = ["open-browser-use", *args, "--session-id", f"{SESSION}-{browser}", "--socket-dir", SOCKET_DIRS[browser]]
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
            raise _NotConnected(f"{browser.title()} isn't connected to auren (Open Browser Use)")
        raise BrowserError(f"browser: {str(msg).strip()[:300]}")
    return d.get("result", d)


def _eval(browser: str, tab: int, js: str, timeout: float = 30):
    params = json.dumps({"expression": js, "returnByValue": True, "awaitPromise": True})
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
  const sel = 'a[href], button, [role=button], [role=link], [role=tab], [role=menuitem], input, select, textarea, summary, [onclick]';
  const items = []; let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el) || n >= 150) continue;
    const text = label(el); const type = el.getAttribute('type') || '';
    if (!text && !['text','search','email','password','number'].includes(type) && el.tagName !== 'TEXTAREA') continue;
    n += 1; el.setAttribute('data-auren-id', String(n)); el.setAttribute('data-auren-label', text);
    const item = {id: n, kind: el.tagName.toLowerCase() + (type ? ':' + type : ''), text};
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


def _wait_ready(browser: str, tab: int, timeout: float = 15) -> None:
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

def browser_open(url: str, browser: str = DEFAULT) -> str:
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
        # With only web-app windows open, the browser makes a new normal window but the reply has no
        # tab id; the second try opens the tab in that window.
        if "tab id" not in str(e):
            raise
        r = obu(b, "open-tab", "--url", url, timeout=40)
    tab = (r.get("tab") or {}).get("id")
    if not tab:
        raise BrowserError(f"couldn't open a tab: {str(r)[:200]}")
    _state.update(tab=tab, browser=b)
    _wait_ready(b, tab)
    return f"(tab {tab} in {b})\n" + browser_read(tab)


def _front(browser: str, tab: int) -> None:
    """Bring auren's tab to the front of its window. Chrome doesn't lay out background tabs, so their
    visible text reads as empty (Google Drive rows, for example)."""
    try:
        if _eval(browser, tab, "document.visibilityState", timeout=5) != "visible":
            obu(browser, "cdp", "--tab-id", str(tab), "--method", "Page.bringToFront", "--params", "{}")
            time.sleep(0.8)
    except BrowserError:
        pass


def browser_read(tab: int | None = None) -> str:
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    _front(b, t)
    return _format(_eval(b, t, _READ_JS))


def browser_click(target: str, tab: int | None = None, expect_download: bool = False) -> str:
    """Click item number `target` from the last read (or the first item whose text matches `target`)."""
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    _front(b, t)
    before = _download_snapshot(b) if expect_download else None
    js = """
    ((target) => {
      let el = /^\\d+$/.test(target) ? document.querySelector(`[data-auren-id="${target}"]`) : null;
      if (!el) {
        const want = target.toLowerCase().trim();
        const all = [...document.querySelectorAll('[data-auren-id]')];
        const lab = e => (e.getAttribute('data-auren-label') || '').toLowerCase();
        const words = want.split(/[^a-z0-9]+/).filter(Boolean);
        el = all.find(e => lab(e) === want) || all.find(e => lab(e).includes(want))
          || all.find(e => words.every(w => lab(e).split(/[^a-z0-9]+/).includes(w)));
      }
      if (!el) return {ok: false};
      el.scrollIntoView({block: 'center'});
      if (el.tagName === 'A' && el.target === '_blank' && el.href && !el.hasAttribute('download')) {
        location.href = el.href; return {ok: true, text: (el.getAttribute('data-auren-label') || '').slice(0, 60), navigated: true};
      }
      el.click();
      return {ok: true, text: (el.getAttribute('data-auren-label') || '').slice(0, 60)};
    })(%s)
    """ % json.dumps(str(target))
    r = _eval(b, t, js)
    if not r or not r.get("ok"):
        raise BrowserError(f"nothing to click matches '{target}' (read the page again for current numbers)")
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
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    js = """((target) => { const all = [...document.querySelectorAll('[data-auren-id]')];
      const lab = e => (e.getAttribute('data-auren-label') || '');
      if (/^\\d+$/.test(target)) { const e = document.querySelector(`[data-auren-id="${target}"]`); return e ? lab(e) : null; }
      const want = target.toLowerCase(); const e = all.find(e => lab(e).toLowerCase().includes(want));
      return e ? lab(e) : null; })(%s)""" % json.dumps(str(target))
    return _eval(b, t, js)


def browser_type(target: str, text: str, submit: bool = False, tab: int | None = None) -> str:
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    js = """
    ((target, text, submit) => {
      const el = document.querySelector(`[data-auren-id="${target}"]`);
      if (!el) return false;
      el.focus();
      const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value');
      setter && setter.set ? setter.set.call(el, text) : (el.value = text);
      el.dispatchEvent(new Event('input', {bubbles: true}));
      el.dispatchEvent(new Event('change', {bubbles: true}));
      if (submit) {
        el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', code: 'Enter', keyCode: 13, bubbles: true}));
        if (el.form) el.form.requestSubmit ? el.form.requestSubmit() : el.form.submit();
      }
      return true;
    })(%s, %s, %s)
    """ % (json.dumps(str(target)), json.dumps(text), "true" if submit else "false")
    if not _eval(b, t, js):
        raise BrowserError(f"no field number {target} (read the page again)")
    if submit:
        _wait_ready(b, t)
        return f"typed and submitted\n\n" + browser_read(t)
    return "typed"


def browser_scroll(direction: str = "down", tab: int | None = None) -> str:
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    _eval(b, t, f"window.scrollBy(0, {'' if direction == 'down' else '-'}window.innerHeight * 0.9)")
    time.sleep(0.6)
    return browser_read(t)


# --- Excalidraw ---------------------------------------------------------------------------
# Excalidraw draws on a canvas, so the page tools can't draw there. Instead this pastes elements in
# Excalidraw's own "excalidraw-api/clipboard" format, which accepts its element skeleton API (shapes with
# `label`, arrows bound by `start`/`end` ids) and pastes them, selected, at the last pointer position.
# Its paste handler ignores the event unless focus is inside the editor and the last pointer position is
# over the canvas, so the script moves a synthetic pointer to the middle of the view first.

_EXCALIDRAW_JS = r"""
(async (elements) => {
  const root = document.querySelector('.excalidraw');
  if (!root) return {error: 'this tab is not an Excalidraw canvas'};
  const x = innerWidth / 2, y = innerHeight / 2;
  const canvas = document.elementFromPoint(x, y);
  if (!canvas || canvas.tagName !== 'CANVAS') return {error: 'something covers the middle of the canvas (a dialog or menu?)'};
  for (const t of ['pointermove', 'mousemove'])
    canvas.dispatchEvent(new PointerEvent(t, {clientX: x, clientY: y, bubbles: true, pointerId: 1, pointerType: 'mouse'}));
  root.focus();
  const count = () => JSON.parse(localStorage.getItem('excalidraw') || '[]').filter(e => !e.isDeleted).length;
  const before = count();
  const dt = new DataTransfer();
  dt.setData('text/plain', JSON.stringify({type: 'excalidraw-api/clipboard', elements}));
  document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true, cancelable: true}));
  await new Promise(r => setTimeout(r, 1500));       // excalidraw.com saves the scene shortly after a change
  return {added: count() - before};
})(%s)
"""


def excalidraw_draw(elements: list[dict], tab: int | None = None) -> str:
    """Paste Excalidraw skeleton elements into the Excalidraw page in the current auren tab."""
    if not isinstance(elements, list) or not elements or not all(isinstance(e, dict) and e.get("type") for e in elements):
        raise BrowserError("elements must be a non-empty list of objects, each with a type")
    b, t = _state["browser"] or DEFAULT, _tab(tab)
    _front(b, t)
    r = _eval(b, t, _EXCALIDRAW_JS % json.dumps(elements)) or {}
    if r.get("error"):
        raise BrowserError(r["error"])
    if r.get("added", 0) <= 0:
        # Other Excalidraw hosts may not save to localStorage, so this isn't proof of failure.
        return "pasted, but couldn't confirm anything was added: look at the screen to check"
    return f"drew {r['added']} elements (they're selected; ctrl+z undoes them)"


# --- bookmarks ---------------------------------------------------------------------------

def _bookmarks(browser: str) -> list[tuple[str, str]]:
    path = BOOKMARK_FILES[browser]
    if not path.exists():
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


def bookmarks(query: str = "", browser: str = DEFAULT) -> str:
    b = _browser(browser)
    words = query.lower().split()
    hits = [(name, url) for name, url in _bookmarks(b) if all(w in name.lower() for w in words)]
    if not hits:
        return f"no {b} bookmarks match '{query}'"
    return "\n".join(f"{name} → {_mask_url(url)}" for name, url in hits[:30]) + \
        ("\n(open one with browser_open('bookmark:<name>'))")


def _bookmark_url(name: str, browser: str) -> str:
    want = name.strip().lower()
    marks = _bookmarks(browser)
    for test in (lambda n: n.lower() == want, lambda n: n.lower().endswith("/" + want), lambda n: want in n.lower()):
        hits = [url for n, url in marks if test(n)]
        if hits:
            return hits[0]
    raise BrowserError(f"no bookmark called '{name}' in {browser}")


# --- downloads ------------------------------------------------------------------------------

def download_dir(browser: str) -> Path:
    try:
        prefs = json.loads(PREFERENCES[browser].read_text())
        d = prefs.get("download", {}).get("default_directory") or prefs.get("savefile", {}).get("default_directory")
        if d:
            return Path(d)
    except (OSError, json.JSONDecodeError):
        pass
    return HOME / "Downloads"


def _download_snapshot(browser: str) -> dict[str, float]:
    d = download_dir(browser)
    return {p.name: p.stat().st_mtime for p in d.iterdir()} if d.is_dir() else {}


def _wait_download(browser: str, before: dict[str, float], timeout: float = 60) -> str | None:
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


def recent_downloads(browser: str = DEFAULT, minutes: int = 30) -> str:
    d = download_dir(_browser(browser))
    cutoff = time.time() - minutes * 60
    files = sorted((p for p in d.iterdir() if p.stat().st_mtime > cutoff and p.is_file()),
                   key=lambda p: -p.stat().st_mtime)
    return "\n".join(f"{p} ({p.stat().st_size // 1024} KB)" for p in files[:15]) or f"no downloads in the last {minutes} minutes"


def browser_close(keep_current: bool = False) -> str:
    """Close the tabs auren opened (never the user's own). keep_current leaves the last one open."""
    b = _state["browser"] or DEFAULT
    keep = [{"tabId": _state["tab"], "status": "deliverable"}] if keep_current and _state["tab"] else []
    obu(b, "finalize-tabs", "--keep", json.dumps(keep))
    if not keep_current:
        _state["tab"] = None
    return "closed auren's browser tabs" + (" (kept the current one)" if keep_current else "")
