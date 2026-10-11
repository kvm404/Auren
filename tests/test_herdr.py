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
    monkeypatch.setattr(herdr.desktop, "herdr_window", lambda: {"address": "0x2"})
    monkeypatch.setattr(herdr.desktop, "open_herdr", lambda: calls.append(("window", "open")))
    monkeypatch.setattr(herdr.desktop, "show_herdr", lambda: calls.append(("window", "up")) or True)
    return calls


def test_prompt_agent_shows_the_agent_before_sending(fake_herdr):
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert fake_herdr == [("agent", "focus", "wT:p2Z"), ("window", "up"), ("agent", "prompt")]
    assert out["view"] == "herdr is showing it"


def test_closed_herdr_is_opened_first(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr.desktop, "herdr_window", lambda: None)
    monkeypatch.setattr(herdr.desktop, "open_herdr", lambda: fake_herdr.append(("window", "open")) or {"address": "0x9"})
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert fake_herdr == [("window", "open"), ("agent", "focus", "wT:p2Z"), ("window", "up"), ("agent", "prompt")]
    assert out["view"] == "opened herdr on it"


def test_herdr_that_wont_open_is_reported(fake_herdr, monkeypatch):
    monkeypatch.setattr(herdr.desktop, "herdr_window", lambda: None)
    monkeypatch.setattr(herdr.desktop, "show_herdr", lambda: False)
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
    herdr.start_agent("codex", str(tmp_path), name="notes-job", show=True)
    assert fake_herdr == [("agent", "start"), ("tab", "focus", "t9"), ("window", "up")]


@pytest.mark.parametrize("error", [herdr.desktop.DesktopError("hyprland: no"), ValueError("bad JSON"), KeyError("pid")])
def test_showing_never_blocks_the_task(fake_herdr, monkeypatch, error):
    def broken():
        raise error
    monkeypatch.setattr(herdr.desktop, "show_herdr", broken)
    out = herdr.prompt_agent("claude", "fix the tests", wait=False, show=True)
    assert out["status"] == "working" and fake_herdr[-1] == ("agent", "prompt")
    assert out["view"].startswith("not shown")
