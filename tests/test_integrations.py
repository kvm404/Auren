import pytest

from auren import integrations, settings, tools


@pytest.fixture
def missing(monkeypatch):
    gone = set()
    monkeypatch.setattr(integrations, "_installed", lambda name: (False, f"{name} isn't here") if name in gone else (True, ""))
    return gone


def test_tools_of_missing_or_disabled_integrations_are_hidden(tmp_path, missing):
    rt = tools.Runtime(tmp_path / "t.sock")
    assert "list_agents" in rt.offered()
    missing.add("herdr")
    integrations._cache.clear()
    assert "list_agents" not in rt.offered() and "run_command" in rt.offered()
    assert "isn't available" in rt.call("daemon", "list_agents", {})[0]
    settings.save({"integrations.browser": False})
    assert integrations.state("browser") == ("off", "turned off in config.toml")
    assert "browser_open" not in rt.offered()
    assert integrations.state("herdr") == ("missing", "herdr isn't here")


def test_default_agent(monkeypatch, tmp_path):
    have = {"codex", "opencode"}
    monkeypatch.setattr(integrations, "agent_installed", lambda k: k in have)
    monkeypatch.setattr(integrations, "OMARCHY_AGENT", tmp_path / "agent")
    assert integrations.default_agent() == "codex"                  # first installed
    (tmp_path / "agent").write_text("opencode\n")
    assert integrations.default_agent() == "opencode"               # Omarchy's default
    settings.save({"agents.default": "claude"})
    assert integrations.default_agent() == "claude"                 # config wins


def test_commands_run_in_the_background_without_herdr(tmp_path, missing, monkeypatch):
    from auren.hands import shell

    monkeypatch.setattr(shell, "JOBS_DIR", tmp_path / "jobs")
    missing.add("herdr")
    rt = tools.Runtime(tmp_path / "t.sock")
    rt.new_turn("daemon")
    import json
    out = json.loads(rt.call("daemon", "run_command", {"command": "echo hello; exit 3", "folder": str(tmp_path)})[0])
    assert out["status"] == "finished" and out["exit_code"] == 3 and "hello" in out["output_tail"]
    slow = json.loads(rt.call("daemon", "run_command", {"command": "sleep 30", "wait": False})[0])
    assert slow["status"] == "running"
    assert "finished" not in rt.call("daemon", "job_status", {"job": slow["job"]})[0]
    assert rt.call("daemon", "close_job", {"job": slow["job"]})[0] == f"closed {slow['job']}"
