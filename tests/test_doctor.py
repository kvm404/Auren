"""auren doctor's talk-key check, from `hyprctl binds -j` output."""

from auren.doctor import OK, WARN, talk_keys


def bind(mods: int, key: str, description: str, release: bool = False) -> dict:
    return {"modmask": mods, "key": key, "keycode": 0, "submap": "", "release": release,
            "description": description, "dispatcher": "__lua", "arg": "1"}


TALK = bind(72, "A", "Auren: talk (press again to send)")
FRESH = bind(76, "A", "Auren: talk in a fresh conversation")


def test_new_keys_free():
    checks = talk_keys([TALK, FRESH, bind(68, "A", "Audio")])
    assert [(c.name, c.status, c.detail) for c in checks] == [
        ("talk key", OK, "Super+Alt+A"), ("fresh-conversation key", OK, "Super+Ctrl+Alt+A")]


def test_old_key_clashing_with_select_all():
    old = bind(64, "A", "Auren: talk (press again to send)")
    checks = talk_keys([bind(64, "A", "Select all"), old, FRESH])
    talk = checks[0]
    assert talk.status == WARN and '"Select all"' in talk.detail
    assert 'hl.unbind("SUPER + A")' in talk.fix and "Super+Alt+A" in talk.fix
    assert checks[1].status == OK


def test_other_free_key_is_fine_but_named():
    checks = talk_keys([bind(0, "F8", "Auren: talk (hold)"), bind(0, "F8", "Auren: send (release)", release=True),
                        bind(1, "F8", "Auren: talk in a fresh conversation")])
    assert [(c.status, c.detail) for c in checks] == [
        (OK, "F8 (the docs say Super+Alt+A)"), (OK, "Shift+F8 (the docs say Super+Ctrl+Alt+A)")]


def test_no_binding():
    checks = talk_keys([bind(64, "A", "Select all")])
    assert [(c.name, c.status) for c in checks] == [("talk key", WARN)]
    assert "install.sh" in checks[0].fix
