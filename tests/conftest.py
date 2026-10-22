import pytest

from auren import settings


@pytest.fixture(autouse=True)
def _own_config(tmp_path_factory, monkeypatch):
    """Tests never read the developer's own ~/.config/auren/config.toml."""
    d = tmp_path_factory.mktemp("config")
    monkeypatch.setattr(settings, "CONFIG_FILE", d / "config.toml")
    monkeypatch.setattr(settings, "LEGACY", d / "settings.json")


@pytest.fixture(autouse=True)
def _every_integration(monkeypatch):
    """Tool tests don't depend on what's installed here (test_integrations covers that)."""
    from auren import integrations
    monkeypatch.setattr(integrations, "_installed", lambda name: (True, ""))
    monkeypatch.setattr(integrations, "_cache", {})


@pytest.fixture(autouse=True)
def _no_notices(monkeypatch):
    """Nothing pops up on the developer's screen: notices are recorded in notice.shown instead."""
    from auren import notice
    shown = []
    monkeypatch.setattr(notice, "_spawn", shown.append)
    monkeypatch.setattr(notice, "_seen", [0.0, False])
    monkeypatch.setattr(notice, "_osd_at", 0.0)
    monkeypatch.setattr(notice, "_opening", [])
    monkeypatch.setattr(notice, "_mascot_running", lambda: False)
    monkeypatch.setattr(notice, "shown", shown, raising=False)
    return shown
