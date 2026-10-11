import pytest

from auren.hands import browser as b
from auren.hands import desktop


@pytest.fixture
def fake(monkeypatch):
    """A browser that connects `connect_after` pings after it is started."""
    st = {"running": False, "pings": 0, "connect_after": 3, "spawned": [], "calls": []}

    def _obu(browser, *args, timeout=30):
        st["calls"].append(args[0])
        if not st["running"] or st["pings"] < st["connect_after"]:
            if args[0] == "ping" and st["running"]:
                st["pings"] += 1
            raise b._NotConnected("not connected")
        return {"ok": True}

    monkeypatch.setattr(b, "_obu", _obu)
    monkeypatch.setattr(b, "_running", lambda browser: st["running"])
    monkeypatch.setattr(b.time, "sleep", lambda s: None)
    monkeypatch.setattr(desktop, "hide_scratchpad", lambda: False)
    monkeypatch.setattr(desktop, "spawn", lambda cmd: (st["spawned"].append(cmd), st.update(running=True)))
    return st


def test_starts_the_browser_waits_and_retries(fake):
    assert b.obu("chrome", "open-tab", "--url", "https://example.com") == {"ok": True}
    assert fake["spawned"] == [["uwsm-app", "--", "google-chrome-stable"]]
    assert fake["calls"][0] == "open-tab" and fake["calls"][-1] == "open-tab"     # the command ran again


def test_running_but_extension_off(fake, monkeypatch):
    fake.update(running=True, connect_after=10**9)
    monkeypatch.setattr(b, "CONNECT_WAIT", 0)
    with pytest.raises(b.BrowserError, match="running, but its Open Browser Use extension isn't connected"):
        b.ensure_connected("chrome", wait=0)
    assert fake["spawned"] == []                                                    # never starts a second one


def test_open_tab_retries_when_the_reply_has_no_tab_id(monkeypatch):
    replies = [b.BrowserError("browser: createTab response did not include numeric tab id"), {"tab": {"id": 7}}]

    def obu(browser, *args, **kw):
        if args[0] != "open-tab":
            return {}
        r = replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(b, "obu", obu)
    monkeypatch.setattr(b, "_wait_ready", lambda browser, tab: None)
    monkeypatch.setattr(b, "browser_read", lambda tab: "page")
    assert b.browser_open("excalidraw.com") == "(tab 7 in helium)\npage"
    assert replies == []


@pytest.mark.parametrize("elements", [[], [{}], [{"x": 1}], "rectangle"])
def test_excalidraw_draw_rejects_bad_elements(elements):
    with pytest.raises(b.BrowserError, match="elements must be"):
        b.excalidraw_draw(elements)
