import json

import pytest

from auren.hands import herdr
from auren.hands.herdr import Agent, HerdrError, _slug, reply_after, resolve

LIVE = [
    Agent("claude", "claude", "working", "wT:p2Z", "/home/krvm/Projects/auren", "PROJECTS", "1"),
    Agent(None, "codex", "idle", "w1:p4", "/home/krvm/Projects/voisu", "PROJECTS", "voisu"),
    Agent("notes", "codex", "done", "w9:p1", "/home/krvm/Obsidian", "auren", "notes"),
]


def test_resolve_by_name_pane_and_kind():
    assert resolve("claude", live=LIVE).pane_id == "wT:p2Z"
    assert resolve("Claude Code", live=LIVE).pane_id == "wT:p2Z"
    assert resolve("cloud code", live=LIVE).pane_id == "wT:p2Z"     # misheard
    assert resolve("w9:p1", live=LIVE).name == "notes"


def test_resolve_codex_needs_hint():
    with pytest.raises(HerdrError) as e:
        resolve("codex", live=LIVE)
    assert e.value.code == "ambiguous"
    assert resolve("codex", hint="voisu", live=LIVE).pane_id == "w1:p4"
    assert resolve("codex", hint="obsidian", live=LIVE).name == "notes"


def test_resolve_missing_kind_lists_running():
    with pytest.raises(HerdrError) as e:
        resolve("opencode", live=LIVE)
    assert e.value.code == "agent_not_found" and "claude" in e.value.message


CODEX_SCREEN = """
  >_ OpenAI Codex (v0.161.0)
     /tmp

⚠ Heads up, you have less than 50% of your 5h limit left. Run /status for a breakdown.


› Reply with exactly: AUREN-OK and nothing else. Do not run any commands.


• AUREN-OK

  Worked for 2s • 8:49 AM

                                                        ⚠ 5h limit: 40% left · resets at 12:47 PM · /status

› Ask Codex to do anything

  GPT-6.1-Sol low · /tmp · Workspace · Context 2% used
  ← for agents · ? for shortcuts                                    ⚠ 1 warning · f2 to view
"""


def test_reply_after_codex():
    reply = reply_after(CODEX_SCREEN, "Reply with exactly: AUREN-OK and nothing else. Do not run any commands.")
    assert reply == "• AUREN-OK"


def test_slug_is_a_valid_unique_agent_name():
    assert _slug("Lecture notes: DSA!", set()) == "lecture-notes-dsa"
    assert _slug("notes", {"notes"}) == "notes-2"
    assert _slug("123 go", set()).startswith("j-")


@pytest.fixture
def fake_herdr(monkeypatch):
    """Record herdr commands (with their target) and window raises instead of running them."""
    calls = []
    def fake(*args, **kwargs):
        calls.append(args[:3] if args[1] == "focus" else args[:2])
        return {"agent": {"agent_status": "working"}}
    monkeypatch.setattr(herdr, "herdr", fake)
    monkeypatch.setattr(herdr, "agents", lambda: LIVE)
    monkeypatch.setattr(herdr.desktop, "herdr_is_open", lambda: True)
    monkeypatch.setattr(herdr.desktop, "open_herdr", lambda: calls.append(("window", "open")))
    monkeypatch.setattr(herdr.desktop, "show_herdr", lambda w=None: calls.append(("window", "up")) or True)
    return calls


def test_prompt_agent_shows_the_agent_before_sending(fake_herdr):
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert fake_herdr == [("agent", "focus", "wT:p2Z"), ("window", "up"), ("agent", "prompt")]
    assert out["view"] == "herdr is showing it"


def test_closed_herdr_is_opened_first(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr.desktop, "herdr_is_open", lambda: False)
    monkeypatch.setattr(herdr.desktop, "open_herdr", lambda: fake_herdr.append(("window", "open")) or {"address": "0x9"})
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert fake_herdr == [("window", "open"), ("agent", "focus", "wT:p2Z"), ("window", "up"), ("agent", "prompt")]
    assert out["view"] == "opened herdr on it"


def test_herdr_that_wont_open_is_reported(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr.desktop, "herdr_is_open", lambda: False)
    monkeypatch.setattr(herdr.desktop, "open_herdr", lambda: None)
    monkeypatch.setattr(herdr.desktop, "show_herdr", lambda w=None: False)
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert out["view"].startswith("not shown") and fake_herdr[-1] == ("agent", "prompt")


def test_prompt_agent_without_show_leaves_the_view(fake_herdr):
    herdr.prompt_agent("claude", "what's 2+2", wait=False)
    assert fake_herdr == [("agent", "prompt")]


def test_start_agent_shows_the_tab_once_the_agent_runs(fake_herdr, monkeypatch, tmp_path):
    monkeypatch.setattr(herdr, "jobs", lambda: [])
    monkeypatch.setattr(herdr, "_load_jobs", lambda: {})
    monkeypatch.setattr(herdr, "_save_jobs", lambda jobs: None)
    monkeypatch.setattr(herdr, "_new_tab", lambda label, cwd: ("t9", "w9:p9"))
    monkeypatch.setattr(herdr, "_wait_for_shell", lambda pane: None)
    monkeypatch.setattr(herdr, "trust_folder", lambda kind, cwd: False)
    monkeypatch.setattr(herdr, "screen", lambda pane, lines=120: "› ")
    herdr.start_agent("codex", str(tmp_path), name="notes-job", show=True)
    assert fake_herdr == [("agent", "start"), ("tab", "focus", "t9"), ("window", "up")]


@pytest.mark.parametrize("error", [herdr.desktop.DesktopError("hyprland: no"), ValueError("bad JSON"), KeyError("pid")])
def test_showing_never_blocks_the_task(fake_herdr, monkeypatch, error):
    def broken(w=None):
        raise error
    monkeypatch.setattr(herdr.desktop, "show_herdr", broken)
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert out["status"] == "working" and fake_herdr[-1] == ("agent", "prompt")
    assert out["view"].startswith("not shown")


def test_herdr_open_but_ambiguous_is_not_opened_again(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr.desktop, "show_herdr", lambda w=None: False)    # can't tell which window
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert ("window", "open") not in fake_herdr and out["view"].startswith("not shown")


def test_view_survives_a_stalled_prompt(fake_herdr, monkeypatch):
    def stalled(*args, **kwargs):
        if args[:2] == ("agent", "prompt"):
            raise HerdrError("agent_prompt_stalled", "no echo")
        return {}
    monkeypatch.setattr(herdr, "herdr", stalled)
    monkeypatch.setattr(herdr, "screen", lambda pane, lines=120: "")
    out = herdr.prompt_agent("claude", "fix the tests", wait=True, show=True)
    assert out["status"] == "agent_prompt_stalled" and out["view"] == "herdr is showing it"


# --- model, effort and folder trust --------------------------------------------------------

@pytest.mark.parametrize("said, model", [
    ("Opus 5.5", "claude-opus-5-5"), ("claude opus 5.5", "claude-opus-5-5"), ("sonnet", "sonnet"),
    ("Haiku 5.5", "claude-haiku-5-5"), ("fable 5 point 2", "claude-fable-5-2"), ("opus 5", "claude-opus-5"),
    ("claude-sonnet-5-5", "claude-sonnet-5-5"), ("opus 5.5[1m]", "claude-opus-5-5[1m]"), ("opus", "opus")])
def test_claude_models_as_said(said, model):
    assert herdr.claude_model(said) == model


@pytest.mark.parametrize("said, model", [
    ("GPT 6.1 Sol", "gpt-6.1-sol"), ("gpt-6-luna", "gpt-6-luna"), ("gpt 5.6 luna", "gpt-5.6-luna")])
def test_codex_models_as_said(said, model):
    assert herdr.codex_model(said) == model


def test_agent_flags():
    assert herdr.agent_flags("claude", "Opus 5.5", "high effort") == (
        ["--model", "claude-opus-5-5", "--effort", "high"], {"model": "claude-opus-5-5", "effort": "high"})
    assert herdr.agent_flags("claude", effort="extra high")[0] == ["--effort", "xhigh"]
    assert herdr.agent_flags("codex", "gpt 6.1 sol", "max") == (
        ["--model", "gpt-6.1-sol", "-c", "model_reasoning_effort=xhigh"], {"model": "gpt-6.1-sol", "effort": "xhigh"})
    assert herdr.agent_flags("claude") == ([], {})
    with pytest.raises(HerdrError, match="effort"):
        herdr.agent_flags("claude", effort="turbo")
    with pytest.raises(HerdrError, match="effort"):
        herdr.agent_flags("grok", effort="high")
    assert herdr.agent_flags("agy", "gemini 3 pro")[0] == ["--model", "gemini-3-pro"]
    with pytest.raises(HerdrError, match="model"):
        herdr.agent_flags("opencode", "anthropic/claude-opus-5-5")


def test_claude_folder_is_trusted_keeping_the_rest(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    cfg = tmp_path / ".claude.json"
    cfg.write_text(json.dumps({"numStartups": 3, "projects": {"/a": {"allowedTools": ["x"]}}}))
    assert herdr.trust_folder("claude", "/a") and herdr.trust_folder("claude", "/b")
    data = json.loads(cfg.read_text())
    assert data["numStartups"] == 3 and data["projects"]["/a"] == {"allowedTools": ["x"], "hasTrustDialogAccepted": True}
    assert data["projects"]["/b"] == {"hasTrustDialogAccepted": True}
    assert not herdr.trust_folder("claude", "/a")          # already trusted: not written again


def test_unreadable_claude_config_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    (tmp_path / ".claude.json").write_text("{half")
    assert not herdr.trust_folder("claude", "/a") and (tmp_path / ".claude.json").read_text() == "{half"


def test_codex_folder_is_trusted_unless_already_decided(tmp_path, monkeypatch):
    import tomllib
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "gpt-6-luna"\n\n[projects."/no"]\ntrust_level = "untrusted"\n')
    assert herdr.trust_folder("codex", "/home/u/my.proj")
    assert not herdr.trust_folder("codex", "/no") and not herdr.trust_folder("codex", "/home/u/my.proj")
    data = tomllib.loads(cfg.read_text())
    assert data["model"] == "gpt-6-luna" and data["projects"]["/no"]["trust_level"] == "untrusted"
    assert data["projects"]["/home/u/my.proj"]["trust_level"] == "trusted"


CLAUDE_TRUST = """ Accessing workspace:
 /tmp/x
 Quick safety check: Is this a project you created or one you trust?
 Claude Code'll be able to read, edit, and execute files here.
 ❯ No, exit
   Yes, I trust this folder
 Enter to confirm · Esc to cancel"""
CODEX_TRUST = """> You are in /tmp/x
  Do you trust the contents of this directory?
› 1. Yes, continue
  2. No, quit
  Press enter to continue"""


def test_trust_keys_pick_yes_whatever_comes_first():
    assert herdr._trust_keys(CLAUDE_TRUST) == ["down", "enter"]
    assert herdr._trust_keys(CODEX_TRUST) == ["enter"]
    assert herdr._trust_keys("› Ask Codex to do anything") == []
    assert herdr._trust_keys("Do you trust the contents of this directory?\n(garbled)") is None


@pytest.fixture
def new_tab(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr, "jobs", lambda: [])
    monkeypatch.setattr(herdr, "_load_jobs", lambda: {})
    monkeypatch.setattr(herdr, "_save_jobs", lambda jobs: None)
    monkeypatch.setattr(herdr, "_new_tab", lambda label, cwd: ("t9", "w9:p9"))
    monkeypatch.setattr(herdr, "_wait_for_shell", lambda pane: None)
    monkeypatch.setattr(herdr.time, "sleep", lambda s: None)
    trusted = []
    monkeypatch.setattr(herdr, "trust_folder", lambda kind, cwd: trusted.append((kind, cwd)) or True)
    return trusted


def test_start_agent_with_model_and_effort_trusts_the_folder(new_tab, monkeypatch, tmp_path):
    started = []
    def fake(*args, **kwargs):
        started.append(args)
        return {}
    monkeypatch.setattr(herdr, "herdr", fake)
    monkeypatch.setattr(herdr, "screen", lambda pane, lines=120: "› ")
    out = herdr.start_agent("claude code", str(tmp_path), name="fix", model="opus 5.5", effort="high")
    assert new_tab == [("claude", str(tmp_path))]
    assert started[0][-5:] == ("--", "--model", "claude-opus-5-5", "--effort", "high")
    assert out["model"] == "claude-opus-5-5" and out["effort"] == "high"


def test_a_bad_effort_opens_no_tab(new_tab, monkeypatch, tmp_path):
    monkeypatch.setattr(herdr, "_new_tab", lambda label, cwd: pytest.fail("opened a tab"))
    with pytest.raises(HerdrError):
        herdr.start_agent("claude", str(tmp_path), effort="turbo")


def test_a_trust_question_is_answered(new_tab, monkeypatch, tmp_path):
    calls = []
    def fake(*args, **kwargs):
        calls.append(args)
        if args[:2] == ("agent", "start"):
            raise HerdrError("timeout", "agent not ready")
        return {}
    monkeypatch.setattr(herdr, "herdr", fake)
    screens = iter([CLAUDE_TRUST, "› ", "› "])
    monkeypatch.setattr(herdr, "screen", lambda pane, lines=120: next(screens))
    out = herdr.start_agent("claude", str(tmp_path), name="fix")
    assert ("pane", "send-keys", "w9:p9", "down", "enter") in calls and out["status"] == "started"


def test_a_failed_start_without_a_trust_question_is_an_error(new_tab, monkeypatch, tmp_path):
    def fake(*args, **kwargs):
        if args[:2] == ("agent", "start"):
            raise HerdrError("timeout", "agent not ready")
        return {}
    monkeypatch.setattr(herdr, "herdr", fake)
    monkeypatch.setattr(herdr, "screen", lambda pane, lines=120: "command not found: claude")
    with pytest.raises(HerdrError, match="timeout"):
        herdr.start_agent("claude", str(tmp_path), name="fix")
