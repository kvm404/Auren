import pytest

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
