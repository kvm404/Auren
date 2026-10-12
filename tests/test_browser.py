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
    assert len(fake["spawned"]) == 1 and fake["spawned"][0][:2] == ["uwsm-app", "--"]
    assert "chrome" in fake["spawned"][0][2]
    assert fake["calls"][0] == "open-tab" and fake["calls"][-1] == "open-tab"     # the command ran again


def test_running_but_extension_off(fake, monkeypatch):
    fake.update(running=True, connect_after=10**9)
    monkeypatch.setattr(b, "CONNECT_WAIT", 0)
    with pytest.raises(b.BrowserError, match="running, but its Open Browser Use extension isn't connected"):
        b.ensure_connected("chrome", wait=0)
    assert fake["spawned"] == []                                                    # never starts a second one


def test_the_browser_its_profile_and_socket_are_detected(tmp_path, monkeypatch):
    import json

    from auren import settings
    from auren.hands import browsers

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    helium = tmp_path / "net.imput.helium"
    (helium / "Profile 2").mkdir(parents=True)
    (helium / "Local State").write_text(json.dumps({"profile": {"last_used": "Profile 2"}}))
    monkeypatch.setattr(browsers, "default_desktop_id", lambda: "helium-browser")
    kind = browsers.resolve()
    assert kind.key == "helium"
    assert browsers.profile_dir(kind) == helium / "Profile 2"
    assert browsers.socket_dir(kind) == "/tmp/open-browser-use"

    settings.save({"browser.name": "brave", "browser.sockets": {"brave": "/tmp/obu-brave"}})
    assert browsers.resolve().key == "brave" and browsers.resolve("Google Chrome").key == "chrome"
    assert browsers.socket_dir(browsers.BY_KEY["brave"]) == "/tmp/obu-brave"
    assert browsers.profile_dir(browsers.BY_KEY["brave"]) is None          # not used on this machine
    with pytest.raises(b.BrowserError, match="work with chrome"):
        b._browser("firefox")
