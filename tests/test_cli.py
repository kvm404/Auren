import pytest

from auren import cli


@pytest.mark.parametrize("command", [["chat"], ["ask", "hello"]])
@pytest.mark.parametrize("option", [["--language", "hi"], ["--male"]])
def test_language_options_are_rejected_before_starting_a_brain(command, option, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["auren", *command, *option])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["chat", "ask"])
def test_chat_help_has_no_language_options(command, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["auren", command, "--help"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 0
    help_text = capsys.readouterr().out
    assert "--language" not in help_text and "--male" not in help_text


def test_selftest_takes_its_options(monkeypatch):
    import sys

    from auren import cli
    got = {}
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("auren.mac.selftest.main", lambda argv: got.setdefault("argv", argv) and 0)
    monkeypatch.setattr(sys, "argv", ["auren", "selftest", "--quick", "--out", "r.txt"])
    cli.main()
    assert got["argv"] == ["--quick", "--out", "r.txt"]
