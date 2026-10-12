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
