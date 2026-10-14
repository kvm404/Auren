import json

import pytest

from auren import ears, vocab


@pytest.fixture
def lists(tmp_path, monkeypatch):
    monkeypatch.setattr(vocab, "DATA", tmp_path)
    (tmp_path / "vocab.txt").write_text("# built in\nherdr\nOmarchy\n")
    monkeypatch.setattr(vocab, "_tell_daemon", lambda: "")
    monkeypatch.setattr(ears, "USER_VOCAB", tmp_path / "mine.txt")
    return tmp_path


def test_add_creates_the_file_and_keeps_the_word(lists):
    assert vocab.add("  Zorblax ") == (True, 'Added "Zorblax".')
    assert vocab.mine() == ["Zorblax"]
    assert "Zorblax" in ears.vocabulary()


def test_add_refuses_what_the_built_in_list_has(lists):
    ok, message = vocab.add("HERDR")
    assert not ok and "built-in" in message
    assert vocab.mine() == []


def test_add_refuses_repeats_ignoring_case_and_spacing(lists):
    vocab.add("Zorblax  Prime")
    ok, message = vocab.add("zorblax prime")
    assert not ok and "your list" in message
    assert vocab.mine() == ["Zorblax Prime"]


@pytest.mark.parametrize("word", ["", "   ", "#note", "x", "y" * 41])
def test_add_refuses_words_the_ears_would_drop(lists, word):
    assert vocab.add(word)[0] is False
    assert vocab.mine() == []


def test_add_refuses_when_the_list_is_full(lists):
    (lists / "mine.txt").write_text("".join(f"word{i}\n" for i in range(vocab.LIMIT - 2)))
    ok, message = vocab.add("Extra")
    assert not ok and "full (98 words)" in message


def test_the_limit_follows_the_built_in_list(lists):
    assert vocab.capacity() == vocab.LIMIT - 2
    (lists / "vocab.txt").write_text("herdr\nOmarchy\nCodex\n")       # the built-in list grows
    assert vocab.capacity() == vocab.LIMIT - 3
    (lists / "vocab.txt").write_text("herdr\n")                          # ... or shrinks
    assert vocab.capacity() == vocab.LIMIT - 1


def test_the_limit_counts_what_the_recognizer_keeps(lists):
    (lists / "vocab.txt").write_text("herdr\nHERDR\nx\nOmarchy\n# comment\n")   # a repeat and a too-short line
    assert vocab.defaults() == ["herdr", "Omarchy"]
    assert vocab.capacity() == vocab.LIMIT - 2
    (lists / "mine.txt").write_text("".join(f"word{i}\n" for i in range(vocab.capacity() - 1)) + "WORD0\n")
    assert vocab.add("Extra")[0] is True      # a repeat in the file doesn't use a slot
    assert vocab.add("Another")[0] is False


def test_remove_keeps_comments_and_other_words(lists):
    (lists / "mine.txt").write_text("# mine\nAlpha\nBeta\n")
    assert vocab.remove("alpha")[0] is True
    assert (lists / "mine.txt").read_text() == "# mine\nBeta\n"
    assert vocab.remove("Gamma")[0] is False


def test_json_output_for_the_panel(lists, capsys):
    assert vocab.main(["add", "Zorblax", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": True, "message": 'Added "Zorblax".', "mine": ["Zorblax"], "defaults": ["herdr", "Omarchy"],
                   "limit": vocab.LIMIT - 2}
    assert vocab.main(["add", "herdr", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_usage(lists, capsys):
    assert vocab.main(["add"]) == 2
    assert vocab.main(["bogus", "x"]) == 2


def test_spacing_in_the_file_does_not_hide_a_repeat_or_a_removal(lists):
    (lists / "mine.txt").write_text("Zorblax   Prime\n")
    assert vocab.add("zorblax prime")[0] is False
    assert vocab.remove("Zorblax Prime")[0] is True
    assert vocab.mine() == []


def test_a_word_the_user_adds_is_never_filtered_as_an_app_name(lists):
    vocab.add("LibreOffice Writer")
    assert "LibreOffice Writer" in ears.vocabulary()


def test_two_edits_at_once_both_land(lists):
    import threading

    def run(w):
        with vocab._locked():
            vocab.add(w)

    threads = [threading.Thread(target=run, args=(f"Word{i}",)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(vocab.mine()) == sorted(f"Word{i}" for i in range(8))


def test_the_panel_hears_when_the_daemon_didnt_reload(lists, monkeypatch, capsys):
    monkeypatch.undo()
    monkeypatch.setattr(ears, "USER_VOCAB", lists / "mine.txt")
    monkeypatch.setattr(vocab, "DATA", lists)
    monkeypatch.setattr("auren.ctl.send", lambda line, timeout=5: "error: boom")
    assert vocab.main(["add", "Zorblax", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] and "restart Auren" in out["message"] and out["mine"] == ["Zorblax"]


def test_a_folder_that_cant_be_written_gives_a_message_not_a_crash(lists, monkeypatch, capsys):
    def boom(text):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(vocab, "_write", boom)
    assert vocab.main(["add", "Zorblax", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is False and "Permission denied" in out["message"]
