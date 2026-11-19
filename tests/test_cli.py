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


def test_setup_on_a_mac_opens_the_setup_window(monkeypatch, capsys):
    from auren.mac import app
    opened = []
    monkeypatch.setattr("sys.argv", ["auren", "setup", "--yes"])
    monkeypatch.setattr(cli.sys, "platform", "darwin")
    monkeypatch.setattr(app, "open_app", lambda: opened.append(True) or 0)
    assert cli.main() == 0
    assert opened and "setup window" in capsys.readouterr().out

