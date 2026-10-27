import io
import json
import os
import struct
import tempfile
import threading
import time
from pathlib import Path

import pytest

from auren import settings, tab_host, tools
from auren.hands import tabs

OPEN = [
    {"id": 17, "title": "Shopping - Amazon.in", "url": "https://www.amazon.in/", "windowId": 4,
     "index": 2, "active": False},
    {"id": 18, "title": "Project notes", "url": "https://notes.example/", "windowId": 4,
     "index": 0, "active": True},
]


@pytest.fixture
def short_runtime(monkeypatch):
    # Unix sockets have a 108-byte path limit; pytest's descriptive temp paths can exceed it.
    with tempfile.TemporaryDirectory(prefix="auren-tabs-") as directory:
        monkeypatch.setattr(tabs, "DIRECTORY", Path(directory) / "bridges")
        yield


@pytest.fixture
def browser(monkeypatch):
    calls = []
    monkeypatch.setattr(tabs, "_connection", lambda name: {"browser": "brave"})

    def request(connection, method, **args):
        calls.append((method, args))
        if method == "list_tabs":
            return {"tabs": [dict(tab) for tab in OPEN]}
        return dict(OPEN[0], active=True)

    monkeypatch.setattr(tabs, "_request", request)
    return calls


def test_switches_the_unique_existing_tab_without_changing_its_content(browser):
    result = tabs.switch_tab("Amazon", "brave")
    assert result["status"] == "switched" and result["tab"]["id"] == 17
    assert browser == [("list_tabs", {}), ("activate_tab", {
        "tabId": 17, "expectedUrl": "https://www.amazon.in/", "expectedWindowId": 4,
    })]


def test_missing_and_ambiguous_tabs_do_not_perform_activation(browser, monkeypatch):
    assert tabs.switch_tab("missing", "brave")["status"] == "not_found"
    monkeypatch.setattr(tabs, "_matches", lambda tab, query: True)
    assert tabs.switch_tab("shopping", "brave")["status"] == "ambiguous"
    assert all(method == "list_tabs" for method, args in browser)


def test_an_explicit_choice_is_revalidated_against_the_query(browser, monkeypatch):
    assert tabs.switch_tab("Amazon", "brave", tab_id=18)["status"] == "not_found"
    monkeypatch.setattr(tabs, "_matches", lambda tab, query: True)
    assert tabs.switch_tab("shopping", "brave", tab_id=17)["status"] == "switched"


@pytest.mark.parametrize("query,matched", [("amazon.in", True), ("https://amazon.in", True),
                                         ("amazon.com", False), ("AmaZoN", True), ("X", False)])
def test_site_matching_uses_the_hostname_and_title_words(query, matched):
    assert bool(tabs._matches(OPEN[0], query)) is matched


def test_failed_browser_activation_is_not_reported_as_success(browser, monkeypatch):
    monkeypatch.setattr(tabs, "_request", lambda connection, method, **args:
                        {"tabs": OPEN} if method == "list_tabs" else dict(OPEN[0], active=False))
    with pytest.raises(tabs.TabError, match="did not confirm"):
        tabs.switch_tab("Amazon", "brave")


def test_the_tool_does_not_use_screen_actions_or_the_send_judge(tmp_path, monkeypatch, browser):
    def forbidden(*args, **kwargs):
        pytest.fail("tab switching must not type, click, take screenshots or ask the send judge")

    for name in ("type_text", "press_keys", "click", "look", "snapshot"):
        monkeypatch.setattr(tools.screen, name, forbidden)
    runtime = tools.Runtime(tmp_path / "tools.sock")
    monkeypatch.setattr(runtime.judge, "allows", forbidden)
    runtime.new_turn("daemon")
    assert '"status": "switched"' in runtime.call("daemon", "browser_switch_tab", {"query": "Amazon", "browser": "brave"})[0]
    assert runtime.summary("daemon") == ""


def test_tab_navigation_is_separate_from_page_automation_and_can_be_disabled(tmp_path):
    runtime = tools.Runtime(tmp_path / "tools.sock")
    settings.save({"integrations.browser": False})
    assert "browser_switch_tab" in runtime.offered()
    settings.save({"integrations.tabs": False})
    assert "browser_switch_tab" not in runtime.offered()
    assert "isn't available" in runtime.call("daemon", "browser_switch_tab", {"query": "Amazon"})[0]


def test_missing_browser_connection_does_not_fall_through_to_another_browser(monkeypatch):
    monkeypatch.setattr(tabs, "connections", lambda: [{"browser": "chrome", "socket": "unused"}])
    with pytest.raises(tabs.TabError, match="not connected in brave"):
        tabs._connection("brave")
    monkeypatch.setattr(tabs, "connections", lambda: [{"browser": "brave"}, {"browser": "brave"}])
    with pytest.raises(tabs.TabError, match="profile"):
        tabs._connection("brave")


def test_native_frames_handle_fragmented_input_and_reject_oversize_or_truncated_messages():
    payload = json.dumps({"method": "list_tabs"}).encode()

    class Fragmented(io.BytesIO):
        def read(self, size=-1):
            return super().read(min(size, 2))

    assert tab_host.read_frame(Fragmented(struct.pack("=I", len(payload)) + payload)) == {"method": "list_tabs"}
    for data in [b"\x00", struct.pack("=I", 2_000_000), struct.pack("=I", 20) + b"{}"]:
        with pytest.raises(ValueError):
            tab_host.read_frame(io.BytesIO(data))


def test_real_socket_and_native_host_round_trip_with_a_fake_browser(short_runtime):
    incoming_r, incoming_w = os.pipe()
    outgoing_r, outgoing_w = os.pipe()
    source, output = os.fdopen(incoming_r, "rb"), os.fdopen(outgoing_w, "wb", buffering=0)
    extension_in, extension_out = os.fdopen(outgoing_r, "rb"), os.fdopen(incoming_w, "wb", buffering=0)
    errors = []

    def send(message):
        data = json.dumps(message).encode()
        extension_out.write(struct.pack("=I", len(data)) + data)

    def host():
        try:
            tab_host.serve(source, output, "brave")
        except Exception as error:
            errors.append(error)

    def extension():
        send({"event": "hello"})
        for _ in range(2):
            request = tab_host.read_frame(extension_in)
            result = {"tabs": OPEN} if request["method"] == "list_tabs" else dict(OPEN[0], active=True)
            send({"id": request["id"], "result": result})
        extension_out.close()

    host_thread, extension_thread = threading.Thread(target=host), threading.Thread(target=extension)
    host_thread.start()
    extension_thread.start()
    try:
        deadline = time.monotonic() + 2
        while not tabs.connections() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not errors, errors
        assert tabs.switch_tab("Amazon", "brave")["status"] == "switched"
        extension_thread.join(2)
        host_thread.join(2)
        assert not errors and not host_thread.is_alive() and not extension_thread.is_alive()
        assert list(tabs.DIRECTORY.iterdir()) == []
    finally:
        for stream in (source, output, extension_in, extension_out):
            stream.close()


def test_native_host_installation_only_registers_the_packaged_extension(tmp_path, monkeypatch):
    base = tmp_path / "brave"
    base.mkdir()
    monkeypatch.setattr(tab_host.browsers, "config_dir", lambda kind: base if kind.key == "brave" else None)
    extension = Path(__file__).parents[1] / "browser-extension"
    executable = tmp_path / "auren-tab-host"
    paths = tab_host.install(extension, executable)
    assert len(paths) == 1
    manifest = json.loads(paths[0].read_text())
    assert manifest["path"] == str(executable)
    assert manifest["name"] == "com.auren.tabs"
    assert len(manifest["allowed_origins"]) == 1
    assert manifest["allowed_origins"][0].startswith("chrome-extension://")
