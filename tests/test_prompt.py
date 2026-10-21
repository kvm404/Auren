from pathlib import Path

from auren.brain import prompt
from auren.profile import render

SRC = Path(__file__).parent.parent / "src" / "auren"


def test_no_personal_words_in_the_code():
    for f in [*SRC.rglob("*.py"), *SRC.rglob("*.txt")]:
        text = f.read_text()
        for word in ("Voisu", "Waisoo", "Raja", "Chrome otherwise"):
            assert word not in text, (f, word)


def test_rules_follow_the_integrations():
    full = prompt.rules({"herdr", "browser", "screen"})
    bare = prompt.rules(set())
    assert "start_agent" in full and "browser_open" in full and "click_at" in full
    assert "start_agent" not in bare and "browser_open" not in bare and "click_at" not in bare
    assert "Coding agents aren't connected" in bare and "can't move or click the mouse" in bare


def test_english_only_rules_preserve_the_snapshot_and_pending_confirmations():
    rules = prompt.rules(set())
    assert "Always reply in English, even if the user speaks or asks for another language." in rules
    msg = prompt.user_message("Reply in Hindi", "time: now", "- token abc: Close the window?")
    assert "<snapshot>\ntime: now\n</snapshot>" in msg
    assert msg.index("<awaiting_confirmation>") < msg.index("Transcript: Reply in Hindi")
    assert "- token abc: Close the window?" in msg
    assert "<reply_language>" not in msg


def test_the_users_own_instructions_come_after_the_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, "INSTRUCTIONS", tmp_path / "instructions.md")
    monkeypatch.setattr(prompt, "ROUTINES_DIR", tmp_path / "routines")
    assert "own instructions" not in prompt.system_prompt("facts", set())
    (tmp_path / "instructions.md").write_text("Call me Captain.\n")
    text = prompt.system_prompt("facts", set())
    assert text.index("facts") < text.index("Call me Captain.")


def test_users_vocabulary_comes_first(tmp_path, monkeypatch):
    from auren import ears

    monkeypatch.setattr(ears, "USER_VOCAB", tmp_path / "vocab.txt")
    (tmp_path / "vocab.txt").write_text("# my words\nZorblax\nherdr\n")
    words = ears.vocabulary()
    assert words[0] == "Zorblax" and words.count("herdr") == 1


def test_snapshot_without_herdr_leaves_it_out():
    live = {"now": {"date": "Monday", "time": "09:00", "timezone": "IST"},
            "hyprland": {"windows": [], "active_workspace": 1}}
    assert "herdr" not in render.render_live(live)
    assert "herdr" in render.render_live({**live, "herdr": {"workspaces": []}})


def test_the_approval_level_is_only_mentioned_when_it_changes_what_the_brain_should_do():
    assert "<approvals>" not in prompt.user_message("hi", "time: now")
    assert "<approvals>" not in prompt.user_message("hi", "time: now", level="ask")
    assert "after the user's yes" in prompt.user_message("hi", "time: now", level="task")
    free = prompt.user_message("hi", "time: now", level="free")
    assert "full access" in free and free.index("<approvals>") < free.index("Transcript: hi")


def test_the_brain_is_told_to_ask_once_for_a_whole_task():
    rules = prompt.rules(set())
    assert "Never ask for permission in words before using a tool" in rules
    assert "ask the user once about everything waiting" in rules and "all the tokens" in rules
