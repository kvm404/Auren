import pytest
from PIL import Image, ImageDraw

from auren.hands import spotify as sp

GREEN = (30, 215, 96)


def page(w=800, h=600, disc=(600, 120, 24), strip=True):
    img = Image.new("RGB", (w, h), (18, 18, 18))
    d = ImageDraw.Draw(img)
    cx, cy, r = disc
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=GREEN)
    d.polygon([(cx - 8, cy - 12), (cx - 8, cy + 12), (cx + 12, cy)], fill=(0, 0, 0))     # the play triangle
    d.rectangle([20, 200, 300, 212], fill=GREEN)                                         # green text: not round
    if strip:
        d.rectangle([0, h - 24, w, h], fill=GREEN)                                       # "Playing on …" strip
    return img


def test_green_button_found_by_colour_not_position():
    assert sp.find_green_button(page(disc=(600, 120, 24))) == pytest.approx((600, 120), abs=2)
    assert sp.find_green_button(page(disc=(300, 250, 30))) == pytest.approx((300, 250), abs=2)


def test_green_button_ignores_text_and_bottom_strip():
    assert sp.find_green_button(page(disc=(600, 450, 24))) is None         # lower half: the player bar's area
    assert sp.find_green_button(Image.new("RGB", (800, 600), (18, 18, 18))) is None


def fake_player(monkeypatch, tracks):
    """now_playing() walks through `tracks`; opened links and clicks are recorded."""
    log = {"uris": [], "clicks": 0}
    states = iter(tracks)
    last = {}

    def now():
        last.update(next(states, last))
        return dict(last)
    monkeypatch.setattr(sp, "ensure_running", lambda: None)
    monkeypatch.setattr(sp, "now_playing", now)
    monkeypatch.setattr(sp, "open_uri", lambda u: log["uris"].append(u))
    monkeypatch.setattr(sp, "_click_top_result", lambda: log.__setitem__("clicks", log["clicks"] + 1) or True)
    monkeypatch.setattr(sp.time, "sleep", lambda s: None)
    return log


def song(title, artist, status="Playing"):
    return {"status": status, "title": title, "artist": artist, "album": "", "track": f"/t/{title}"}


def test_play_searches_by_link_clicks_once_and_reports_the_new_track(monkeypatch):
    log = fake_player(monkeypatch, [song("Old", "A"), song("Old", "A"), song("Bogakoi Bogoli", "Zubeen Garg")])
    out = sp.act("play", "Zubeen Garg Bogakoi Bogoli")
    assert log["uris"] == ["spotify:search:Zubeen%20Garg%20Bogakoi%20Bogoli"]
    assert log["clicks"] == 1
    assert "Bogakoi Bogoli by Zubeen Garg" in out and not out.startswith("error")


def test_play_does_not_claim_success_when_nothing_changed(monkeypatch):
    fake_player(monkeypatch, [song("Old", "A")])
    out = sp.act("play", "Zubeen Garg")
    assert out.startswith("error") and "still" in out


def test_play_same_song_already_on_counts_when_it_matches_the_query(monkeypatch):
    fake_player(monkeypatch, [song("Dilruba Dilnasheen", "Rito Riba")])
    assert not sp.act("play", "dilruba dilnasheen").startswith("error")


def test_play_without_a_button_says_so(monkeypatch):
    fake_player(monkeypatch, [song("Old", "A")])
    monkeypatch.setattr(sp, "_click_top_result", lambda: False)
    t = iter(range(0, 100, 5))
    monkeypatch.setattr(sp.time, "time", lambda: next(t))
    assert "no Top result play button" in sp.act("play", "x")


def test_bad_input():
    with pytest.raises(sp.SpotifyError):
        sp.act("play", "")
    with pytest.raises(sp.SpotifyError):
        sp.open_uri("https://evil.example")
    with pytest.raises(sp.SpotifyError):
        sp.act("dance")


def test_now_playing_when_closed(monkeypatch):
    monkeypatch.setattr(sp, "running", lambda: False)
    assert sp.now_playing() == {}
    assert sp.act("now_playing") == "nothing is loaded in Spotify"
