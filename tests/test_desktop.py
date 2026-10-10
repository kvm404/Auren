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
