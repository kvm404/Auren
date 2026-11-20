import hashlib
import io
import json
import os
import tarfile

import pytest

from auren import config, integrations, settings, tools
from auren.hands import browser as b
from auren.hands import browsers, desktop
from auren.hands import obu as hosts

EXT = hosts.EXTENSION_ID


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A machine with Helium (profiles kvm and School, both with the extension) and Brave (none yet),
    its config under tmp_path, and fake Open Browser Use hosts: `live` maps a socket to its instance id."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(config, "DATA_HOME", tmp_path / "data" / "auren")
    monkeypatch.setattr(hosts, "_known", {})
    monkeypatch.setattr(hosts, "_scan", None)
    helium = tmp_path / "config" / "net.imput.helium"
    for folder, iid in (("Default", "iid-kvm"), ("Profile 1", "iid-school")):
        (helium / folder / "Extensions" / EXT / "1.1.0_0").mkdir(parents=True)
        store = helium / folder / "Local Extension Settings" / EXT
        store.mkdir(parents=True)
        (store / "000003.log").write_bytes(b"\x00junk" + json.dumps({"instanceId": iid}).encode() + b"\x01")
    (helium / "Local State").write_text(json.dumps({"profile": {"last_used": "Profile 1", "info_cache": {
        "Default": {"name": "kvm"}, "Profile 1": {"name": "School"}}}}))
    brave = tmp_path / "config" / "BraveSoftware" / "Brave-Browser"
    (brave / "Default").mkdir(parents=True)
    (brave / "Local State").write_text(json.dumps({"profile": {"last_used": "Default", "info_cache": {"Default": {"name": "Me"}}}}))
    host = tmp_path / "bin" / "open-browser-use"
    host.parent.mkdir()
    host.write_text("#!/bin/sh\n")
    host.chmod(0o755)
    (helium / "NativeMessagingHosts").mkdir()
    (helium / "NativeMessagingHosts" / f"{hosts.HOST_NAME}.json").write_text(json.dumps({"path": str(host)}))

    st = {"live": {}, "default": "helium", "installed": {"helium", "brave"}, "running": set(), "spawned": []}
    sockets = tmp_path / "sockets"
    sockets.mkdir()

    def add(name: str, iid: str) -> str:
        path = sockets / f"{name}.sock"
        path.write_text("")
        st["live"][str(path)] = iid
        return str(path)
    st["add"] = add
    monkeypatch.setattr(hosts, "cli", lambda: str(host))
    monkeypatch.setattr(hosts, "_sockets", lambda: list(st["live"]))
    monkeypatch.setattr(hosts, "_instance", lambda binary, sock: st["live"].get(sock))
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: None)
    monkeypatch.setattr(hosts, "_listener_pid", lambda sock: None)
    monkeypatch.setattr(browsers, "default_desktop_id", lambda: {"helium": "helium", "brave": "brave-browser"}.get(st["default"], ""))
    monkeypatch.setattr(browsers, "installed", lambda kind: kind.key in st["installed"])
    monkeypatch.setattr(browsers, "running", lambda kind: kind.key in st["running"])
    monkeypatch.setattr(browsers, "command", lambda kind: kind.commands[0])
    monkeypatch.setattr(browsers, "flatpak", lambda kind: False)
    monkeypatch.setattr(desktop, "hide_scratchpad", lambda: False)
    monkeypatch.setattr(desktop, "spawn", lambda cmd: st["spawned"].append(cmd))
    monkeypatch.setattr(hosts.time, "sleep", lambda s: None)
    monkeypatch.setattr(b.time, "sleep", lambda s: None)
    monkeypatch.setattr(b, "_state", {"tab": None, "link": None})
    monkeypatch.setattr(b, "RECONNECT_WAIT", 0)
    st["helium"], st["brave"], st["host"] = helium, brave, host
    return st


HELIUM, BRAVE = browsers.BY_KEY["helium"], browsers.BY_KEY["brave"]


def test_each_socket_is_matched_to_its_browser_and_profile(machine):
    machine["add"]("a", "iid-kvm")
    machine["add"]("b", "iid-school")
    machine["add"]("dead", None)
    found = {c.socket.rsplit("/", 1)[1]: (c.kind.key, c.profile.name) for c in hosts.connections()}
    assert found == {"a.sock": ("helium", "kvm"), "b.sock": ("helium", "School")}


def test_an_unknown_instance_is_told_by_the_browser_that_started_it(machine, monkeypatch):
    sock = machine["add"]("x", "iid-elsewhere")
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: (HELIUM, False))
    c = hosts.connections()[0]
    assert c.socket == sock and c.kind == HELIUM and c.profile is None      # two Helium profiles have it


def _copy_of_kvm_in_brave(machine):
    """Brave's profile holding the same instance id as Helium's kvm (it was imported from there)."""
    store = machine["brave"] / "Profile 2" / "Local Extension Settings" / EXT
    store.mkdir(parents=True)
    (store / "000005.ldb").write_bytes(b"instanceId" + b"iid-kvm")
    (machine["brave"] / "Profile 2" / "Extensions" / EXT).mkdir(parents=True)
    ls = json.loads((machine["brave"] / "Local State").read_text())
    ls["profile"]["info_cache"]["Profile 2"] = {"name": "Kvm"}
    (machine["brave"] / "Local State").write_text(json.dumps(ls))


def test_the_same_instance_id_in_two_browsers_goes_by_the_browser_that_started_the_host(machine, monkeypatch):
    _copy_of_kvm_in_brave(machine)
    machine["add"]("kvm", "iid-kvm")
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: (HELIUM, False))
    [c] = hosts.connections()
    assert (c.kind, c.profile.name) == (HELIUM, "kvm")
    assert b._browser().label() == "Helium (kvm)"


def test_without_its_parent_only_a_running_browser_counts(machine):
    _copy_of_kvm_in_brave(machine)
    machine["add"]("kvm", "iid-kvm")
    machine["running"].add("helium")
    [c] = hosts.connections()
    assert (c.kind, c.profile.name) == (HELIUM, "kvm")
    machine["running"].add("brave")                 # both running: it can't tell, so it isn't used
    hosts.forget_scan()
    hosts._known.clear()
    assert hosts.connections() == []


def test_two_browsers_sharing_an_instance_id_are_both_seen(machine, monkeypatch):
    _copy_of_kvm_in_brave(machine)
    helium = machine["add"]("h", "iid-kvm")
    brave = machine["add"]("b", "iid-kvm")
    parent = {helium: (HELIUM, False), brave: (BRAVE, False)}
    monkeypatch.setattr(hosts, "_listener_pid", lambda sock: sock)
    monkeypatch.setattr(hosts, "_browser_above", lambda sock: parent[sock])
    found = {(c.kind.key, c.profile.name) for c in hosts.connections()}
    assert found == {("helium", "kvm"), ("brave", "Kvm")}
    assert hosts.choose("brave").conn.socket == brave


def test_a_host_with_its_own_data_folder_isnt_used(machine, monkeypatch):
    machine["add"]("x", "iid-elsewhere")
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: (BRAVE, True))
    assert hosts.connections() == []


def test_a_custom_user_data_dir_is_told_from_the_browsers_own(machine, monkeypatch):
    args = {1: [b"brave", b"--user-data-dir=/tmp/throwaway", b""], 2: [b"brave", b""],
            3: [b"brave", f"--user-data-dir={machine['brave']}".encode(), b""]}
    monkeypatch.setattr(hosts, "_cmdline", lambda pid: args[pid])
    assert hosts._own_data_dir(1, BRAVE) and not hosts._own_data_dir(2, BRAVE) and not hosts._own_data_dir(3, BRAVE)


def test_a_host_of_unknown_profile_isnt_taken_for_the_one_wanted(machine, monkeypatch):
    machine["add"]("x", "iid-new")                  # its storage doesn't name it, and Helium has two profiles
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: (HELIUM, False))
    [c] = hosts.connections()
    assert c.kind == HELIUM and c.profile is None and hosts._known == {}      # looked at again next time
    settings.save({"browser.profile": "kvm"})
    t = hosts.choose()
    assert t.conn is None and t.profile.name == "kvm"
    assert not hosts.matches(c, HELIUM, t.profile) and hosts.matches(c, HELIUM, None)


def test_a_profile_that_doesnt_exist_is_an_error(machine):
    machine["add"]("s", "iid-school")
    settings.save({"browser.profile": "Shcool"})
    with pytest.raises(b.BrowserError, match="no profile called 'Shcool'.*School"):
        b._browser()


def test_choosing_prefers_the_default_browser_then_the_last_used_profile(machine):
    machine["add"]("kvm", "iid-kvm")
    machine["add"]("school", "iid-school")
    t = hosts.choose()
    assert t.kind == HELIUM and t.profile.name == "School" and t.conn      # Local State's last_used

    settings.save({"browser.profile": "kvm"})
    assert hosts.choose().profile.dir == "Default"                        # by its name or its folder
    assert hosts.choose("brave").profile is None                          # the profile belongs to Helium
    settings.save({"browser.profile": "auto"})
    assert hosts.choose(None, "Default").conn.socket.endswith("kvm.sock")


def test_not_connected_it_picks_a_browser_that_is_set_up(machine):
    machine["default"] = "brave"            # Brave is the default, but only Helium has Open Browser Use
    t = hosts.choose()
    assert t.kind == HELIUM and t.conn is None and t.profile.name == "School"
    assert hosts.not_set_up(BRAVE) == "Open Browser Use isn't registered with Brave"
    assert hosts.not_set_up(HELIUM) is None


def test_a_closed_browser_is_started_in_its_profile_and_waited_for(machine, monkeypatch):
    sock = {}

    def spawn(cmd):
        machine["spawned"].append(cmd)
        machine["running"].add("helium")
        sock["path"] = machine["add"]("new", "iid-school")
    monkeypatch.setattr(desktop, "spawn", spawn)
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return type("P", (), {"returncode": 0, "stdout": json.dumps({"result": {"tab": {"id": 7}}}), "stderr": ""})()
    monkeypatch.setattr(b.subprocess, "run", run)

    link = b._browser()
    assert b.obu(link, "open-tab", "--url", "https://example.com") == {"tab": {"id": 7}}
    assert machine["spawned"][0][-2:] == ["helium-browser", "--profile-directory=Profile 1"]
    assert calls[0][-2:] == ["--socket", sock["path"]] and "auren-helium" in calls[0]


def test_opening_a_tab_tries_again_once_when_no_tab_comes_back(machine, monkeypatch):
    machine["add"]("s", "iid-school")
    replies = iter(['{"result": {}}', '{"error": {"message": "createTab response did not include numeric tab id"}}',
                    '{"result": {"tab": {"id": 9}}}'])
    monkeypatch.setattr(b.subprocess, "run", lambda cmd, **kw: type("P", (), {"returncode": 0, "stdout": next(replies), "stderr": ""})())
    monkeypatch.setattr(b, "_wait_ready", lambda link, tab: None)
    monkeypatch.setattr(b, "browser_read", lambda tab=None: "page")
    assert b.browser_open("example.com") == "(tab 9 in Helium (School))\npage"


def test_running_but_the_extension_never_connects(machine, monkeypatch):
    machine["running"].add("helium")
    link = b._browser()
    with pytest.raises(b.BrowserError, match="running, but its Open Browser Use extension isn't connected"):
        b.ensure_connected(link, wait=0)
    assert machine["spawned"][0][-1] == "--profile-directory=Profile 1"   # a window in that profile, not a 2nd browser


def test_a_browser_without_open_browser_use_points_at_setup(machine):
    with pytest.raises(b.BrowserError, match="isn't registered with Brave.*browser_setup"):
        b.ensure_connected(b._browser("brave"))
    assert machine["spawned"] == []


def test_a_dead_socket_is_found_again(machine, monkeypatch):
    old = machine["add"]("old", "iid-school")
    answers = iter([{"returncode": 1, "stdout": "", "stderr": "dial unix: connect: no such file"},
                    {"returncode": 0, "stdout": '{"result": "pong"}', "stderr": ""}])
    sockets = []

    def run(cmd, **kw):
        sockets.append(cmd[-1])
        return type("P", (), next(answers))()
    monkeypatch.setattr(b.subprocess, "run", run)
    link = b._browser()

    def restarted():
        del machine["live"][old]
        return machine["add"]("again", "iid-school")
    new = restarted()
    link.conn = hosts.Conn(old, HELIUM, link.profile, "iid-school", 0)
    assert b.obu(link, "ping") == "pong"
    assert sockets == [old, new]


def test_the_snapshots_bookmarks_come_from_the_profile_in_use(machine):
    from auren.profile import collect
    machine["add"]("k", "iid-kvm")                       # only kvm is connected, though School was used last
    label, path = collect.bookmark_file()
    assert label == "Helium (kvm)" and path == machine["helium"] / "Default" / "Bookmarks"


def test_the_integration_check_reads_only_files(machine, monkeypatch):
    def no_scan(*a, **k):
        raise AssertionError("scanned the sockets")
    monkeypatch.setattr(hosts, "connections", no_scan)
    assert hosts.set_up_somewhere()
    machine["installed"].discard("helium")
    assert not hosts.set_up_somewhere()


def test_bookmarks_come_from_the_profile_in_use(machine):
    (machine["helium"] / "Profile 1" / "Bookmarks").write_text(json.dumps({"roots": {"bookmark_bar": {
        "type": "folder", "name": "Bar", "children": [{"type": "url", "name": "Lectures", "url": "https://school.example"}]}}}))
    machine["add"]("s", "iid-school")
    assert "school.example" in b.bookmarks("lectures")


def test_registering_writes_a_manifest_only_where_none_works(machine):
    binary = machine["host"]
    assert hosts.register(HELIUM, binary) is False                 # Helium's works: left alone
    path = hosts.manifest_path(BRAVE)
    assert hosts.register(BRAVE, binary) is True
    m = json.loads(path.read_text())
    assert m["path"] == str(binary) and m["allowed_origins"] == [f"chrome-extension://{EXT}/"]
    assert m["name"] == hosts.HOST_NAME
    path.write_text(json.dumps({"path": "/gone/open-browser-use"}))     # a broken one is replaced
    assert hosts.register(BRAVE, binary) is True


def test_socket_folders_expand_variables(machine, monkeypatch):
    monkeypatch.setenv("AUREN_TEST_RUN", "/tmp/obu-test-run")
    script = machine["host"].with_name("run-host.sh")
    script.write_text('#!/bin/sh\nexec open-browser-use host --socket-dir "$AUREN_TEST_RUN/obu"\n')
    script.chmod(0o755)
    (machine["helium"] / "NativeMessagingHosts" / f"{hosts.HOST_NAME}.json").write_text(json.dumps({"path": str(script)}))
    settings.save({"browser.sockets": {"brave": "$NOT_SET_ANYWHERE/obu"}})
    dirs = hosts.socket_dirs()
    assert "/tmp/obu-test-run/obu" in dirs and not any("$" in d for d in dirs)


def test_host_scripts_name_their_socket_folders(machine):
    script = machine["host"].with_name("helium-host.sh")
    script.write_text('#!/bin/sh\nexec "$(dirname "$0")/open-browser-use" host --socket-dir /tmp/obu-test-helium\n')
    script.chmod(0o755)
    (machine["helium"] / "NativeMessagingHosts" / f"{hosts.HOST_NAME}.json").write_text(json.dumps({"path": str(script)}))
    settings.save({"browser.sockets": {"brave": "/tmp/obu-test-brave"}})
    dirs = hosts.socket_dirs()
    assert dirs[0] == "/tmp/open-browser-use" and "/tmp/obu-test-helium" in dirs and "/tmp/obu-test-brave" in dirs


def _release(binary: bytes) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz") as tar:
        info = tarfile.TarInfo("open-browser-use")
        info.size, info.mode = len(binary), 0o755
        tar.addfile(info, io.BytesIO(binary))
    return out.getvalue()


def test_the_binary_is_downloaded_once_and_checked(machine, monkeypatch):
    data = _release(b"\x7fELF fake")
    fetched = []
    monkeypatch.setattr(hosts, "_fetch", lambda url: fetched.append(url) or data)
    monkeypatch.setattr(hosts, "_arch", lambda: "amd64")
    monkeypatch.setattr(hosts, "SHA256", {"amd64": "0" * 64})
    with pytest.raises(hosts.SetupError, match="checksum"):
        hosts.install_binary()
    assert not hosts.own_binary().exists()

    monkeypatch.setattr(hosts, "SHA256", {"amd64": hashlib.sha256(data).hexdigest()})
    path = hosts.install_binary()
    assert path == hosts.own_binary() and path.read_bytes() == b"\x7fELF fake" and os.access(path, os.X_OK)
    assert hosts.installed_version() == hosts.VERSION
    hosts.install_binary()
    assert len(fetched) == 2                    # the second install found it there
    assert fetched[0].endswith(f"open-browser-use-cli-{hosts.VERSION}-linux-amd64.tar.gz")


def test_a_broken_download_is_a_setup_error(machine, monkeypatch):
    import http.client

    def fetch(url):
        raise http.client.IncompleteRead(b"half")
    monkeypatch.setattr(hosts, "_fetch", fetch)
    monkeypatch.setattr(hosts, "_arch", lambda: "amd64")
    with pytest.raises(hosts.SetupError, match="couldn't download"):
        hosts.install_binary()


def test_setup_registers_and_opens_the_store_page(machine, monkeypatch):
    own = machine["host"].with_name("own")
    monkeypatch.setattr(hosts, "install_binary", lambda: own)
    r = hosts.setup(BRAVE)
    assert r.wrote_manifest and r.opened_store and r.connected is None
    assert json.loads(hosts.manifest_path(BRAVE).read_text())["path"] == str(own)
    assert machine["spawned"][0][-2:] == ["brave", hosts.STORE_URL]

    machine["spawned"].clear()
    machine["add"]("s", "iid-school")
    r = hosts.setup(HELIUM)                    # already set up and connected: nothing opened
    assert not r.wrote_manifest and not r.opened_store and r.connected.profile.name == "School"
    assert machine["spawned"] == []


def test_omarchy_names_the_real_default_browser(tmp_path, monkeypatch):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, out in (("omarchy-cmd-default-browser", "helium.desktop"), ("xdg-settings", "omarchy-browser.desktop")):
        (bin_dir / name).write_text(f"#!/bin/sh\necho {out}\n")
        (bin_dir / name).chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    assert browsers.default_desktop_id() == "helium"
    (bin_dir / "omarchy-cmd-default-browser").unlink()
    assert browsers.default_desktop_id() == ""          # the wrapper alone names no browser


def test_unknown_browsers_are_refused():
    with pytest.raises(b.BrowserError, match="work with chrome"):
        b._browser("firefox")


# --- the integration and the setup tool -------------------------------------------------------------------

def test_browser_tools_are_offered_while_not_set_up_but_not_when_off(tmp_path, machine, monkeypatch):
    monkeypatch.setattr(integrations, "_installed", lambda name: (False, "not set up") if name == "browser" else (True, ""))
    rt = tools.Runtime(tmp_path / "t.sock")
    assert {"browser_open", "browser_setup"} <= set(rt.offered())
    from auren.brain import prompt
    assert "call browser_setup" in prompt.rules()
    settings.save({"integrations.browser": False})
    integrations._cache.clear()
    assert "browser_open" not in rt.offered() and "browser_setup" in rt.offered()


def test_without_a_chromium_browser_the_browser_tools_arent_offered(tmp_path, machine, monkeypatch):
    monkeypatch.setattr(integrations, "_installed", lambda name: (False, "no Chromium browser") if name == "browser" else (True, ""))
    rt = tools.Runtime(tmp_path / "t.sock")
    assert "browser_open" in rt.offered()
    machine["installed"].clear()
    integrations._cache.clear()
    assert "browser_open" not in rt.offered()


def test_browser_setup_asks_then_sets_up_and_turns_the_tools_on(tmp_path, machine, monkeypatch):
    settings.save({"integrations.browser": False})
    done = []
    monkeypatch.setattr(hosts, "setup", lambda kind: done.append(kind) or hosts.SetupResult(kind, "/x", True, True, None))
    rt = tools.Runtime(tmp_path / "t.sock")
    rt.set_level("free")
    rt.new_turn("daemon")
    asked = json.loads(rt.call("daemon", "browser_setup", {"browser": "brave"})[0])
    assert asked["status"] == "needs_confirmation" and "Brave" in asked["ask_user"] and done == []
    rt.new_turn("daemon")
    [outcome] = rt.answer("daemon", True)
    assert done == [BRAVE] and outcome.ok and "next fresh conversation" in outcome.result
    assert settings.current()["integrations.browser"] == "auto"


def test_a_browser_macos_hides_is_set_up_when_its_host_is_connected(machine, monkeypatch):
    # macOS without Full Disk Access: the folder can't be listed, so no profile and no extension show
    monkeypatch.setattr(hosts, "with_extension", lambda kind: [])
    monkeypatch.setattr(hosts, "readable", lambda kind: kind != HELIUM)
    monkeypatch.setattr(hosts, "connections", lambda fresh=False: [])
    assert not hosts.set_up_somewhere()
    monkeypatch.setattr(hosts, "connections", lambda fresh=False: [hosts.Conn("s", HELIUM, None, "iid", 0)])
    assert hosts.set_up_somewhere()
    monkeypatch.setattr(hosts, "registered", lambda kind: False)    # the manifest is in that folder too
    assert hosts.set_up_somewhere()
    monkeypatch.setattr(hosts, "readable", lambda kind: True)       # readable and empty: really not set up
    assert not hosts.set_up_somewhere()


def test_readable_tells_a_folder_kept_from_this_process(machine, monkeypatch):
    real = os.listdir

    def listdir(path):
        if "helium" in str(path):
            raise PermissionError(1, "Operation not permitted")
        return real(path)
    monkeypatch.setattr(os, "listdir", listdir)
    assert not hosts.readable(HELIUM)
    assert hosts.readable(browsers.BY_KEY["brave"])


# --- real input (CDP Input events) ---------------------------------------------------------------------------

class FakeTab:
    """A tab behind a fake Open Browser Use: records the CDP calls, and answers auren's page scripts by what
    they do (`page` holds the answers a test sets)."""

    def __init__(self):
        self.calls, self.page = [], {"point": {"x": 120.5, "y": 40.0}, "moved": True, "reached": {"down": True, "clicked": True},
                                     "mode": "plain", "focus": {"focus": "in", "selected": True}, "typed": ["ok"],
                                     "form": False}

    def obu(self, link, *args, timeout=30, retry=True):
        assert args[0] == "cdp"
        method, params = args[4], json.loads(args[6])
        if method != "Runtime.evaluate":
            self.calls.append((method, params))
            if params.get("type") in self.page.get("failing", ()):
                raise b.BrowserError("browser: lost")
            return {}
        js, p = params["expression"], self.page
        if "point: aim(el, false)" in js:
            value = {"ok": True, "text": "Send", "point": p["point"]}
        elif "mode: plain(el)" in js:
            value = {"mode": p["mode"], "point": p["point"], "before": ""}
        elif "__aurenAim.moved" in js:
            value = p["moved"]
        elif "delete window.__aurenAim" in js:
            value = p["reached"]
        elif "el.click(); return true" in js:
            self.calls.append(("script click", None))
            value = True
        elif "focusIn(box(target)" in js:
            value = p["focus"]
        elif "typed(box(target)" in js:
            r = p["typed"].pop(0) if len(p["typed"]) > 1 else p["typed"][0]
            value = {"r": r, "now": "old draft hello"} if r == "differs" else {"r": r}
        elif "setValue(el, text)" in js:
            self.calls.append(("set value", None))
            value = True
        elif "requestSubmit" in js:
            value = "form" if p["form"] else "key"
            if p["form"]:
                self.calls.append(("request submit", None))
        elif "execCommand('insertText'" in js:
            self.calls.append(("execCommand", None))
            value = True
        elif "visibilityState" in js:
            value = "visible"
        else:
            value = None
        return {"result": {"type": "object", "value": value}}

    def input(self, method=None):
        return [(m, p) for m, p in self.calls if method is None or m == method]


@pytest.fixture
def tab(machine, monkeypatch):
    t = FakeTab()
    monkeypatch.setattr(b, "obu", t.obu)
    monkeypatch.setattr(b, "_wait_ready", lambda link, tab, timeout=15: None)
    monkeypatch.setattr(b, "browser_read", lambda tab=None: "page")
    monkeypatch.setattr(b, "_state", {"tab": 3, "link": object()})
    return t


def test_a_click_is_a_real_mouse_click_at_the_middle_of_the_element(tab):
    assert b.browser_click("7").startswith("clicked “Send”")
    mouse = tab.input("Input.dispatchMouseEvent")
    assert [p["type"] for _, p in mouse] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert all((p["x"], p["y"]) == (120.5, 40.0) for _, p in mouse)
    assert mouse[1][1]["button"] == "left" and mouse[1][1]["clickCount"] == 1
    assert ("script click", None) not in tab.calls


def test_a_covered_element_gets_a_script_click_instead(tab):
    tab.page["point"] = None                  # aim found something on top of it
    b.browser_click("7")
    assert tab.calls == [("script click", None)]


def test_when_the_mouse_lands_elsewhere_nothing_is_pressed(tab):
    tab.page["moved"] = False                 # the page moved under it (zoom, a banner sliding in)
    b.browser_click("7")
    assert [p["type"] for _, p in tab.input("Input.dispatchMouseEvent")] == ["mouseMoved"]
    assert tab.calls[-1] == ("script click", None)


def test_a_click_that_got_there_is_not_clicked_again(tab):
    tab.page["reached"] = {"down": True, "clicked": False}    # it acted on the press and went away
    b.browser_click("7")
    assert ("script click", None) not in tab.calls


def test_a_rich_text_box_is_typed_into_with_real_input(tab):
    tab.page["mode"] = "rich"
    assert b.browser_type("5", "hello there") == "typed"
    assert tab.input("Input.insertText") == [("Input.insertText", {"text": "hello there"})]
    assert not tab.input("Input.dispatchKeyEvent")            # no Enter: in a chat box it would send


def test_a_plain_field_is_typed_with_insert_text_and_checked(tab):
    assert b.browser_type("2", "weather") == "typed"
    assert [m for m, _ in tab.calls] == ["Input.dispatchMouseEvent"] * 3 + ["Input.insertText"]


def test_a_field_that_didnt_take_it_gets_its_value_set(tab):
    tab.page["typed"] = ["no"]
    assert b.browser_type("2", "weather") == "typed"
    assert tab.calls[-1] == ("set value", None)


def test_a_rich_box_that_doesnt_take_it_says_to_use_the_screen(tab):
    tab.page.update(mode="rich", typed=["no"])
    with pytest.raises(b.BrowserError, match="click_at and use type_text"):
        b.browser_type("5", "hello")
    assert ("execCommand", None) in tab.calls and ("set value", None) not in tab.calls


def test_joined_lines_are_left_as_typed_and_nothing_is_pressed(tab):
    tab.page.update(mode="rich", typed=["lines"])
    assert b.browser_type("5", "one\ntwo") == "typed, but the box joined the lines (typed as one line)"
    assert not tab.input("Input.dispatchKeyEvent") and len(tab.input("Input.insertText")) == 1


@pytest.mark.parametrize("found", ["lines", "differs", "unchecked"])
def test_a_box_that_doesnt_hold_just_the_text_isnt_sent(tab, found):
    tab.page.update(mode="rich", typed=[found])
    with pytest.raises(b.BrowserError, match="typed, but didn't send") as e:
        b.browser_type("5", "hello", submit=True)
    assert "old draft" not in str(e.value)            # errors aren't wrapped as page text
    assert not tab.input("Input.dispatchKeyEvent") and ("request submit", None) not in tab.calls


def test_a_box_that_reads_otherwise_says_what_it_reads(tab):
    tab.page.update(mode="rich", typed=["differs"])
    assert b.browser_type("5", "hello") == "typed, but the box now reads “old draft hello”: check it"
    assert ("execCommand", None) not in tab.calls     # something went in: never typed again


def test_an_editor_that_cant_be_read_back_says_so(tab):
    tab.page.update(mode="rich", typed=["unchecked"])
    out = b.browser_type("5", "hello")
    assert out.startswith("typed at the cursor") and "read the page" in out


def test_typing_aims_away_from_links_and_buttons_in_the_box(tab, monkeypatch):
    seen = []
    real = tab.obu
    monkeypatch.setattr(b, "obu", lambda link, *a, **k: seen.append(a[6]) or real(link, *a, **k))
    b.browser_type("5", "hello")
    b.browser_click("7")
    assert any("aim(el, true)" in x for x in seen) and any("aim(el, false)" in x for x in seen)


def test_a_late_press_isnt_allowed_once_the_watch_has_expired():
    assert "!window.__aurenAim.expired" in b._AIM_READY_JS
    assert "st.moved = false; st.expired = true" in b._AIM_JS


def test_a_release_that_fails_after_the_press_got_there_is_an_error(tab):
    tab.page.update(failing={"mouseReleased"}, reached={"down": True, "clicked": False})
    with pytest.raises(b.BrowserError, match="may not have finished"):
        b.browser_click("7")
    assert ("script click", None) not in tab.calls


def test_the_mouse_is_released_even_when_the_press_fails(tab):
    tab.page.update(failing={"mousePressed"}, reached={"down": False, "clicked": False})
    b.browser_click("7")
    assert [p["type"] for _, p in tab.input("Input.dispatchMouseEvent")] == ["mouseMoved", "mousePressed", "mouseReleased"]
    assert tab.calls[-1] == ("script click", None)         # nothing got there: a script click, once


def test_keys_carry_no_native_key_code(tab):
    tab.page["mode"] = "rich"
    b.browser_type("5", "hi", submit=True)
    assert all("nativeVirtualKeyCode" not in p for _, p in tab.input("Input.dispatchKeyEvent"))


def test_submitting_outside_a_form_presses_a_real_enter_once(tab):
    tab.page["mode"] = "rich"
    b.browser_type("5", "running late", submit=True)
    keys = tab.input("Input.dispatchKeyEvent")
    assert [(p["type"], p["key"], p["windowsVirtualKeyCode"]) for _, p in keys] == [("keyDown", "Enter", 13), ("keyUp", "Enter", 13)]
    assert keys[0][1]["text"] == "\r" and keys[0][1]["modifiers"] == 0
    assert ("request submit", None) not in tab.calls


def test_submitting_in_a_form_submits_it_and_presses_nothing(tab):
    tab.page["form"] = True
    b.browser_type("2", "weather", submit=True)
    assert ("request submit", None) in tab.calls and not tab.input("Input.dispatchKeyEvent")


@pytest.mark.parametrize("platform, modifier", [("linux", b.CTRL), ("darwin", b.META)])
def test_select_all_uses_the_platforms_shortcut(tab, monkeypatch, platform, modifier):
    monkeypatch.setattr(b.sys, "platform", platform)
    tab.page["focus"] = {"focus": "in", "selected": False}    # selecting from the page didn't take
    b.browser_type("2", "weather")
    down, up = tab.input("Input.dispatchKeyEvent")
    assert (down[1]["key"], down[1]["code"], down[1]["modifiers"]) == ("a", "KeyA", modifier)
    assert down[1]["commands"] == ["selectAll"] and "text" not in down[1] and up[1]["type"] == "keyUp"


def test_a_field_that_cant_be_focused_gets_its_value_set(tab):
    tab.page.update(point=None, focus={"focus": "none"})
    b.browser_type("2", "weather")
    assert tab.calls == [("set value", None)]


def test_scripts_run_in_a_block_so_their_helpers_can_be_declared_again(tab, monkeypatch):
    seen = []
    monkeypatch.setattr(b, "obu", lambda link, *a, **k: seen.append(json.loads(a[6])["expression"]) or tab.obu(link, *a, **k))
    b._eval(None, 3, "const find = 1; find")
    assert seen == ["{\nconst find = 1; find\n}"]
