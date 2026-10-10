from auren.profile.redact import MASK, redact, redact_all


def test_secret_assignments():
    assert redact('export TAVILY_API_KEY="tvly-dev-abc"') == f"export TAVILY_API_KEY={MASK}"
    assert redact("OPENCODE_SERVER_PASSWORD=hunter2 opencode serve") == f"OPENCODE_SERVER_PASSWORD={MASK} opencode serve"
    assert redact("GITHUB_TOKEN=abc") == f"GITHUB_TOKEN={MASK}"


def test_url_credentials():
    assert redact("ffplay rtsp://cam:s3cret@192.168.1.5:554/stream1") == f"ffplay rtsp://{MASK}@192.168.1.5:554/stream1"


def test_token_shapes_and_flags():
    assert MASK in redact("curl -H 'x: sk-ant-api03-abcdefghijklmnopqrstuv'")
    assert MASK in redact("key is gsk_ABCDEFGHIJKLMNOPQRSTUV")
    assert redact("mytool --password letmein run") == f"mytool --password {MASK} run"


def test_leaves_normal_text():
    for text in ("alias gst='git status -s'", "OPENCODE_SERVER_USERNAME=kvm", "https://github.com/notifications",
                 "omarchy-launch-webapp https://web.whatsapp.com/"):
        assert redact(text) == text


def test_nested():
    data = {"aliases": [{"name": "opw", "value": "PASSWORD=x serve"}], "n": 3}
    assert redact_all(data) == {"aliases": [{"name": "opw", "value": f"PASSWORD={MASK} serve"}], "n": 3}
