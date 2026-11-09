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
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: HELIUM)
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
    monkeypatch.setattr(hosts, "_browser_above", lambda pid: HELIUM)
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
