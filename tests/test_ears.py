import json

from auren import ears


def test_the_connection_is_pinned_to_english():
    # Auren is English-only; unpinned, an Indian accent can come back as Hindi script.
    assert json.loads(ears.connection_params([])["language_codes"]) == ["en"]


def test_the_connection_keeps_push_to_talk_and_the_model():
    params = ears.connection_params(["herdr"])
    assert params["speech_model"] == "universal-3-5-pro"
    assert params["min_turn_silence"] == params["max_turn_silence"] == 10000
    assert json.loads(params["keyterms_prompt"]) == ["herdr"]


def test_the_vocabulary_is_capped_at_100_terms():
    assert len(json.loads(ears.connection_params([f"w{i}" for i in range(150)])["keyterms_prompt"])) == 100
