import json

import pytest

from auren import settings


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "config.toml")
    monkeypatch.setattr(settings, "LEGACY", tmp_path / "settings.json")
    return tmp_path


def test_choices_match_the_code_that_uses_them():
    from auren.brain import Brain
    from auren.voice import TUNING

    assert settings.MODES == Brain.MODES and settings.EFFORTS == Brain.EFFORTS
    for name, (default, low, high) in TUNING.items():
        k = settings.BY_FLAT[name]
        assert k.default == default and k.check(low) == low and k.check(high) == high
        with pytest.raises(ValueError):
            k.check(high + 1)


def test_the_safety_level_is_one_of_the_levels_and_defaults_to_asking(cfg):
    from auren import safety

    assert settings.SAFETY_LEVELS == safety.LEVELS
    assert settings.defaults()["safety"] == "ask"
    assert settings.set_value("safety.level", "task") == "task"
    assert settings.load()["safety"] == "task"
    with pytest.raises(ValueError, match="safety.level should be ask or task or free"):
        settings.set_value("safety.level", "yolo")
    values, problems = settings.read('[safety]\nlevel = "everything"\n')
    assert values["safety"] == "ask" and "safety.level" in problems[0]


def test_a_new_config_has_every_setting_with_its_help(cfg):
    assert settings.load() == settings.defaults()
    text = (cfg / "config.toml").read_text()
    for k in settings.KEYS:
        assert k.help in text
    assert settings.read(text) == (settings.defaults(), [])


def test_settings_json_is_moved_in_once(cfg):
    (cfg / "settings.json").write_text(json.dumps(
        {"voice": False, "brain": "codex", "effort": "high", "language": "hi", "voiceName": "Hugh",
         "speed": 1.2, "steps": 7, "volume": 0.8, "pause": 0.1, "nonsense": 1}))
    values = settings.load()
    assert {k: values[k] for k in json.loads((cfg / "settings.json.bak").read_text()) if k in values} == {"voice": False, "brain": "codex", "effort": "high", "voiceName": "Hugh",
                      "speed": 1.2, "volume": 0.8, "pause": 0.1}
    assert not (cfg / "settings.json").exists() and (cfg / "settings.json.bak").exists()
    assert settings.load() == values
    assert "language" not in values and "language" not in (cfg / "config.toml").read_text()


def test_saving_keeps_the_users_comments_and_extras(cfg):
    (cfg / "config.toml").write_text('# mine\n\n[voice]\nspeed = 1.1  # my pace\n\n[extra]\nx = 1\n')
    settings.save({"speed": 1.2, "brain": "claude"})
    text = (cfg / "config.toml").read_text()
    assert "# mine" in text and "# my pace" in text and "[extra]\nx = 1" in text
    values, problems = settings.read(text)
    assert values["speed"] == 1.2 and values["brain"] == "claude" and problems == ["unknown setting extra.x"]


@pytest.mark.parametrize("language", ["hi", "en"])
@pytest.mark.parametrize("operation", ["load", "save"])
def test_old_language_setting_is_removed_without_changing_other_preferences(cfg, language, operation):
    text = f'# my settings\nlanguage = "{language}"\n\n[voice]\nname = "Trevor"\nspeed = 1.2  # my pace\n'
    path = cfg / "config.toml"
    path.write_text(text)
    # Readers used by tools must also tolerate the setting before daemon startup migrates it.
    assert settings.read(text)[1] == [] and "language" not in settings.current()
    if operation == "save":
        assert settings.save({"volume": 0.8})
    values = settings.load()
    assert "language" not in values and "language" not in path.read_text()
    assert values["voiceName"] == "Trevor" and values["speed"] == 1.2
    assert "# my settings" in path.read_text() and "# my pace" in path.read_text()
    if operation == "save":
        assert values["volume"] == 0.8
    # Migration only rewrites once, rather than rewriting on every settings load.
    changed = path.stat().st_mtime_ns
    settings.load()
    assert path.stat().st_mtime_ns == changed
    with pytest.raises(ValueError, match="unknown setting language"):
        settings.set_value("language", language)


def _old_install(cfg, codex_model: str) -> None:
    """A config.toml written before the Codex default changed: every value out, comments and all."""
    values = settings.defaults() | {"brain.codex_model": codex_model, "speed": 1.2}
    (cfg / "config.toml").write_text(settings.tomlkit.dumps(settings._template(values)) + "# mine\n")


def test_an_old_default_moves_to_the_new_one_once(cfg):
    _old_install(cfg, "gpt-6.1-sol")
    values = settings.load()
    assert values["brain.codex_model"] == "gpt-6-luna" and values["speed"] == 1.2
    assert "# mine" in (cfg / "config.toml").read_text()
    # Picking the old value again is a choice: it stays.
    settings.set_value("brain.codex_model", "gpt-6.1-sol")
    assert settings.load()["brain.codex_model"] == "gpt-6.1-sol"


def test_a_model_the_user_picked_is_not_moved(cfg):
    _old_install(cfg, "gpt-6.2-sol")
    assert settings.load()["brain.codex_model"] == "gpt-6.2-sol"
    assert "codex-luna" in (cfg / ".config.migrated").read_text()


def test_a_new_config_needs_no_moving(cfg):
    settings.load()
    assert set((cfg / ".config.migrated").read_text().split()) == set(settings.DEFAULT_CHANGES)
    settings.set_value("brain.codex_model", "gpt-6.1-sol")
    assert settings.load()["brain.codex_model"] == "gpt-6.1-sol"


def test_an_unreadable_config_is_moved_once_it_is_fixed(cfg):
    (cfg / "config.toml").write_text("this is = not toml [")
    settings.load()
    assert not (cfg / ".config.migrated").exists()
    _old_install(cfg, "gpt-6.1-sol")
    assert settings.load()["brain.codex_model"] == "gpt-6-luna"


@pytest.mark.parametrize("name, speed, want_speed", [("F2", 1.05, 1.0), ("M3", 1.6, 1.25), ("F1", 0.7, 0.8),
                                                     ("M1", 1.1, 1.1)])
def test_a_supertonic_config_moves_to_phonon_once(cfg, caplog, name, speed, want_speed):
    """A config.toml from before Phonon: its voice falls back to Everly (with a log line), steps goes, and a
    speed past Phonon's range is brought into it; nothing is reported as wrong or unknown afterwards."""
    path = cfg / "config.toml"
    path.write_text(f'# mine\n[voice]\nname = "{name}"\nspeed = {speed}  # my pace\nsteps = 5\nvolume = 0.7\n')
    with caplog.at_level("INFO", logger="auren.settings"):
        values = settings.load()
    assert values["voiceName"] == "Everly" and values["speed"] == want_speed and values["volume"] == 0.7
    assert f"Supertonic voice {name} is gone: using Everly" in caplog.text
    text = path.read_text()
    assert "steps" not in text and "# mine" in text and "# my pace" in text
    assert settings.read(text)[1] == []
    # Once only: a speed picked later stays, even a wrong one (that is reported, not moved).
    path.write_text(text.replace(f"speed = {want_speed}", "speed = 1.05"))
    assert settings.load()["speed"] == 1.05


def test_a_voice_name_older_than_phonon_in_a_retired_settings_json_is_the_default(cfg):
    (cfg / "settings.json").write_text(json.dumps({"voiceName": "M3", "steps": 5}))
    assert settings.load()["voiceName"] == "Everly"


def test_bad_values_fall_back_and_are_reported(cfg):
    values, problems = settings.read('[voice]\nspeed = 9\nname = "Z9"\n[extra]\nx = 1\n')
    assert values["speed"] == 1.0 and values["voiceName"] == "Everly"
    assert len(problems) == 3 and any("extra.x" in p for p in problems)
    values, problems = settings.read("this is = not toml [")
    assert values == settings.defaults() and problems


def test_set_value_checks_and_converts(cfg):
    assert settings.set_value("voice.enabled", "off") is False
    assert settings.set_value("voice.name", " hugh ") == "Hugh"
    for retired in ("voice.pitch", "voice.steps"):
        with pytest.raises(ValueError, match="unknown"):
            settings.set_value(retired, "2")
    with pytest.raises(ValueError, match="auto or claude or codex"):
        settings.set_value("brain.mode", "gpt")
    with pytest.raises(ValueError, match="voice.name should be Everly, Juniper, Hugh or Trevor"):
        settings.set_value("voice.name", "F2")
    assert settings.load()["voice"] is False and settings.load()["voiceName"] == "Hugh"
    assert "steps" not in settings.load()


def test_auren_config_set_and_get(cfg, monkeypatch, capsys):
    monkeypatch.setattr(settings, "_tell_daemon", lambda: "")
    assert settings.main(["set", "brain.effort", "high"]) == 0
    assert "used when Auren starts" in capsys.readouterr().out
    assert settings.main(["get", "brain.effort"]) == 0
    assert capsys.readouterr().out == "high\n"
    assert settings.main(["set", "brain.effort", "ludicrous"]) == 1
    assert settings.main(["show"]) == 0
    assert "brain.effort = \"high\"" in capsys.readouterr().out
    assert settings.main(["frobnicate"]) == 2


def test_tables_lists_and_switches(cfg):
    settings.save({"browser.sockets": {"chrome": "/tmp/obu-chrome"}, "projects.folders": ["~/work"]})
    text = (cfg / "config.toml").read_text()
    assert 'sockets = {chrome = "/tmp/obu-chrome"}' in text
    values = settings.load()
    assert values["browser.sockets"] == {"chrome": "/tmp/obu-chrome"} and values["projects.folders"] == ["~/work"]
    assert settings.set_value("integrations.herdr", "off") is False
    assert settings.set_value("integrations.herdr", "auto") == "auto"
    with pytest.raises(ValueError, match="auto, true or false"):
        settings.set_value("integrations.herdr", "maybe")
    assert settings.current()["integrations.herdr"] == "auto"
    assert settings.defaults()["projects.folders"] is not settings.defaults()["projects.folders"]


def test_a_file_with_a_mistake_is_left_alone(cfg):
    (cfg / "config.toml").write_text("[voice\nspeed = 1.1\n")       # half-way through an edit
    assert settings.save({"voice": False}) is False
    assert (cfg / "config.toml").read_text() == "[voice\nspeed = 1.1\n"
    with pytest.raises(ValueError, match="mistake"):
        settings.set_value("voice.enabled", "off")


def test_parallel_writers_keep_each_others_changes(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    monkeypatch.setattr(settings, "CONFIG_FILE", tmp_path / "auren" / "config.toml")
    monkeypatch.setattr(settings, "LEGACY", tmp_path / "auren" / "settings.json")
    settings.ensure()
    env = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path)}
    changes = [("voice.speed", "1.2"), ("voice.volume", "0.5"), ("voice.pause", "0.4"), ("brain.mode", "codex"),
               ("brain.effort", "high"), ("voice.name", "Juniper")]
    procs = [subprocess.Popen([sys.executable, "-c", f"from auren import settings; settings.set_value({p!r}, {v!r})"],
                              env=env) for p, v in changes]
    assert [p.wait() for p in procs] == [0] * len(procs)
    got = settings.load()
    assert (got["speed"], got["volume"], got["pause"], got["brain"], got["effort"], got["voiceName"]) == \
        (1.2, 0.5, 0.4, "codex", "high", "Juniper")
    assert not list((tmp_path / "auren").glob(".config.*.tmp"))


def test_tables_and_lists_can_be_set_from_the_command_line(cfg):
    assert settings.set_value("browser.sockets", '{ chrome = "/tmp/obu-chrome" }') == {"chrome": "/tmp/obu-chrome"}
    assert settings.set_value("projects.folders", '["~/work", "~/src"]') == ["~/work", "~/src"]
    assert settings.set_value("projects.folders", "~/code") == ["~/code"]
    with pytest.raises(ValueError, match="written as in config.toml"):
        settings.set_value("browser.sockets", "{ chrome = }")
    assert settings.load()["browser.sockets"] == {"chrome": "/tmp/obu-chrome"}
