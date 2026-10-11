import pytest

from auren.hands import desktop as d

WINDOWS = [
    {"address": "0x1", "class": "brave-browser", "initialClass": "brave-browser", "title": "GitHub - auren",
     "workspace": {"id": 1, "name": "1"}, "focusHistoryID": 1, "mapped": True},
    {"address": "0x2", "class": "foot", "initialClass": "foot", "title": "arch: PROJECTS",
     "workspace": {"id": -98, "name": "special:scratchpad"}, "focusHistoryID": 0, "mapped": True},
    {"address": "0x3", "class": "Spotify", "initialClass": "Spotify", "title": "Song - Artist",
     "workspace": {"id": 3, "name": "3"}, "focusHistoryID": 2, "mapped": True},
]


class FakeProfile:
    def static(self):
        return {"apps": {
            "apps": [{"name": "Visual Studio Code", "id": "com.microsoft.VSCode", "wmclass": "Code"},
                     {"name": "Obsidian", "id": "md.obsidian.Obsidian"},
                     {"name": "Files", "id": "org.gnome.Nautilus"}],
            "webapps": [{"name": "YT Music", "id": "YT Music", "url": "https://music.youtube.com/"},
                        {"name": "WhatsApp", "id": "WhatsApp", "url": "https://web.whatsapp.com/"}],
            "tuis": [], "not_installed": ["Firefox", "Spotify"]}}


@pytest.fixture(autouse=True)
def fake_windows(monkeypatch):
    monkeypatch.setattr(d, "windows", lambda: WINDOWS)


def test_find_window_by_class_title_and_case():
    assert d.find_window("spotify")["address"] == "0x3"
    assert d.find_window("brave")["address"] == "0x1"
    assert d.find_window("github")["address"] == "0x1"       # title words
    with pytest.raises(d.DesktopError, match="Open windows"):
        d.find_window("discord")


def test_find_app_names_and_spoken_words():
    p = FakeProfile()
    assert d.find_app("VS Code", p)["id"] == "com.microsoft.VSCode"
    assert d.find_app("music", p)["kind"] == "webapp"
    assert d.find_app("obsidian", p)["name"] == "Obsidian"
    assert d.find_app("file manager", p)["name"] == "Files"


def test_find_app_not_installed():
    with pytest.raises(d.DesktopError, match="Firefox is not installed"):
        d.find_app("firefox", FakeProfile())
    with pytest.raises(d.DesktopError, match="no installed app"):
        d.find_app("notion", FakeProfile())


def test_close_window_reports_when_app_refuses(monkeypatch):
    monkeypatch.setattr(d, "dispatch", lambda lua: None)
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    out = d.close_window("brave")                       # window list never changes: the app showed a dialog
    assert "still open" in out and "dialog" in out


def test_close_window_confirms_when_gone(monkeypatch):
    calls = {"n": 0}
    def wins():
        calls["n"] += 1
        return WINDOWS if calls["n"] == 1 else [w for w in WINDOWS if w["address"] != "0x1"]
    monkeypatch.setattr(d, "windows", wins)
    monkeypatch.setattr(d, "dispatch", lambda lua: None)
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    assert d.close_window("brave").startswith("closed brave-browser")


def test_herdr_window_is_the_terminal_herdr_runs_in(monkeypatch):
    wins = [dict(w, pid=p) for w, p in zip(WINDOWS, (100, 200, 300))]
    monkeypatch.setattr(d, "windows", lambda: wins)
    monkeypatch.setattr(d, "_herdr_clients", lambda: [202])
    monkeypatch.setattr(d, "_parent", {202: 201, 201: 200, 200: 1}.get)     # foot -> shell -> herdr
    assert d.herdr_window()["address"] == "0x2"
    monkeypatch.setattr(d, "_herdr_clients", lambda: [])
    assert d.herdr_window() is None


def test_herdr_window_with_one_terminal_process_for_all_windows(monkeypatch):
    monkeypatch.setattr(d, "_herdr_clients", lambda: [202])
    monkeypatch.setattr(d, "_parent", {202: 200, 200: 1}.get)
    monkeypatch.setattr(d, "windows", lambda: [dict(w, pid=200) for w in WINDOWS])
    assert d.herdr_window()["address"] == "0x2"           # the one on the scratchpad
    monkeypatch.setattr(d, "windows", lambda: [dict(w, pid=200) for w in WINDOWS if w["address"] != "0x2"])
    assert d.herdr_window() is None                       # can't tell which: don't guess


def test_herdr_clients_skip_the_server_and_one_off_commands(monkeypatch):
    monkeypatch.setattr(d, "sh", lambda cmd, check=True: "4415 /usr/bin/herdr server\n936038 herdr\n"
                                                         "937000 herdr agent list\n938000 herdr --session work\n"
                                                         "939000 herdr session attach work\n")
    assert d._herdr_clients() == [936038, 938000, 939000]


@pytest.mark.parametrize("showing, toggles", [
    ([""], True),                              # hidden: show it
    (["special:scratchpad"], False),           # showing on the focused monitor
    (["", "special:scratchpad"], False),       # showing on another monitor: don't move it
])
def test_show_herdr_opens_the_scratchpad_only_when_hidden(monkeypatch, showing, toggles):
    sent = []
    monkeypatch.setattr(d, "herdr_window", lambda: WINDOWS[1])
    monkeypatch.setattr(d, "monitors", lambda: [{"focused": i == 0, "specialWorkspace": {"name": n}}
                                                for i, n in enumerate(showing)])
    monkeypatch.setattr(d, "dispatch", sent.append)
    assert d.show_herdr()
    assert sent[-1] == 'hl.dsp.focus({ window = "address:0x2" })'
    assert (sent[0] == 'hl.dsp.workspace.toggle_special("scratchpad")') == toggles and len(sent) == 1 + toggles


def test_open_herdr_launches_it_and_waits_for_the_window(monkeypatch):
    spawned, found = [], iter([None, None, WINDOWS[1]])
    monkeypatch.setattr(d, "hide_scratchpad", lambda: False)
    monkeypatch.setattr(d, "spawn", spawned.append)
    monkeypatch.setattr(d, "herdr_window", lambda: next(found))
    monkeypatch.setattr(d.time, "sleep", lambda s: None)
    assert d.open_herdr()["address"] == "0x2"
    assert spawned == [["omarchy-launch-terminal", "herdr"]]
