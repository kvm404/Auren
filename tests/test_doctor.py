"""auren doctor's talk-key check, from `hyprctl binds -j` output."""

from auren.doctor import OK, WARN, talk_keys


def bind(mods: int, key: str, description: str, release: bool = False) -> dict:
    return {"modmask": mods, "key": key, "keycode": 0, "submap": "", "release": release,
            "description": description, "dispatcher": "__lua", "arg": "1"}


TALK = bind(64, "A", "Auren: talk (press again to send)")
FRESH = bind(65, "A", "Auren: talk in a fresh conversation")
SELECT_ALL = bind(64, "A", "Select all")


def test_default_keys_unbound_first():
    checks = talk_keys([TALK, FRESH, bind(68, "A", "Audio")])
    assert [(c.name, c.status, c.detail) for c in checks] == [
        ("talk key", OK, "Super+A"), ("fresh-conversation key", OK, "Super+Shift+A")]


def test_select_all_left_bound():
    checks = talk_keys([SELECT_ALL, TALK, FRESH])
    talk = checks[0]
    assert talk.status == WARN and '"Select all"' in talk.detail
    assert 'hl.unbind("SUPER + A")' in talk.fix and "Super+Alt+A" in talk.fix
    assert checks[1].status == OK


def test_spare_keys():
    checks = talk_keys([SELECT_ALL, bind(72, "A", "Auren: talk (press again to send)"),
                        bind(76, "A", "Auren: talk in a fresh conversation")])
    assert [(c.status, c.detail) for c in checks] == [(OK, "Super+Alt+A"), (OK, "Super+Ctrl+Alt+A")]


def test_spare_key_clash_suggests_only_the_unbind():
    checks = talk_keys([bind(72, "A", "Something else"), bind(72, "A", "Auren: talk (press again to send)")])
    assert checks[0].status == WARN and "move Auren" not in checks[0].fix


def test_other_free_key_is_fine_but_named():
    checks = talk_keys([bind(0, "F8", "Auren: talk (hold)"), bind(0, "F8", "Auren: send (release)", release=True),
                        bind(1, "F8", "Auren: talk in a fresh conversation")])
    assert [(c.status, c.detail) for c in checks] == [
        (OK, "F8 (the docs say Super+A)"), (OK, "Shift+F8 (the docs say Super+Shift+A)")]


def test_no_binding():
    checks = talk_keys([SELECT_ALL])
    assert [(c.name, c.status) for c in checks] == [("talk key", WARN)]
    assert "install.sh" in checks[0].fix
