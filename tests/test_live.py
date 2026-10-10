import numpy as np

from auren import live


def test_describe_fills_in_arguments():
    assert live.describe("launch_app", {"app": "WhatsApp", "workspace": "3"}) == ("Opening WhatsApp", False)
    assert live.describe("click_at", {"x": 10, "y": 20, "target": "Allow button"}) == ("Clicking Allow button", True)


def test_describe_drops_missing_or_meaningless_arguments():
    assert live.describe("browser_click", {"target": "7"}) == ("Clicking", False)    # an item number
    assert live.describe("toggle", {}) == ("Switching", False)
    assert live.describe("some_new_tool", {}) == ("Working", False)


def test_loudness_scale():
    assert live.loudness(np.zeros(1000)) == 0.0
    quiet = live.loudness(0.005 * np.sin(np.arange(4410)))
    loud = live.loudness(0.3 * np.sin(np.arange(4410)))
    assert 0 < quiet < 0.3 < loud <= 1.0


def test_envelope_rate():
    assert len(live.envelope(np.zeros(44100), 44100)) == 20


def test_level_file_is_rewritten_in_place(tmp_path):
    lv = live.Level(tmp_path / "level")
    lv.set(0.5)
    assert (tmp_path / "level").read_text() == "0.500\n"
    lv.set(2)                                   # clamped
    assert (tmp_path / "level").read_text() == "1.000\n"
    lv.set(0)
    assert (tmp_path / "level").read_text() == "0.000\n"
