"""One function per cloud STT provider: transcribe(wav_path, vocab or None) -> text.

Settings mirror what Voisu found works (Nova-3 with keyterms; Whisper large-v3
at temperature 0 with a vocabulary prompt), plus two newer candidates.
All calls are batch (whole file); accuracy matches the streaming versions,
but latency here includes uploading the file.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

import httpx

from common import api_key

TIMEOUT = httpx.Timeout(60.0, connect=10.0)
_client = httpx.Client(timeout=TIMEOUT, headers={"User-Agent": "parth-stt-experiment/0.1"})


def _check(r: httpx.Response) -> httpx.Response:
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r


def _whisper_prompt(vocab: list[str], max_chars: int = 800) -> str:
    """Comma-separated glossary under Whisper's ~224-token prompt budget."""
    out = ""
    for term in vocab:
        nxt = f"{out}, {term}" if out else term
        if len(nxt) > max_chars:
            break
        out = nxt
    return out


def deepgram_nova3(wav: Path, vocab: list[str] | None) -> str:
    params = [("model", "nova-3"), ("smart_format", "true"), ("punctuate", "true"), ("language", "en")]
    params += [("keyterm", t) for t in (vocab or [])[:100]]
    r = _check(_client.post("https://api.deepgram.com/v1/listen", params=params,
                            headers={"Authorization": f"Token {api_key('deepgram')}",
                                     "Content-Type": "audio/wav"},
                            content=wav.read_bytes()))
    return r.json()["results"]["channels"][0]["alternatives"][0]["transcript"]


def _groq(model: str) -> Callable[[Path, list[str] | None], str]:
    def transcribe(wav: Path, vocab: list[str] | None) -> str:
        data = {"model": model, "temperature": "0", "language": "en", "response_format": "json"}
        if vocab:
            data["prompt"] = _whisper_prompt(vocab)
        r = _check(_client.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                headers={"Authorization": f"Bearer {api_key('groq')}"},
                                data=data, files={"file": (wav.name, wav.read_bytes(), "audio/wav")}))
        return r.json()["text"].strip()
    return transcribe


def elevenlabs_scribe2(wav: Path, vocab: list[str] | None) -> str:
    data: dict = {"model_id": "scribe_v2", "language_code": "en", "tag_audio_events": "false"}
    if vocab:
        data["keyterms"] = [t for t in vocab if len(t) <= 50][:1000]  # sent as repeated fields
    r = _check(_client.post("https://api.elevenlabs.io/v1/speech-to-text",
                            headers={"xi-api-key": api_key("elevenlabs")},
                            data=data, files={"file": (wav.name, wav.read_bytes(), "audio/wav")}))
    return r.json()["text"].strip()


def assemblyai_u35pro(wav: Path, vocab: list[str] | None) -> str:
    headers = {"authorization": api_key("assemblyai")}
    base = "https://api.assemblyai.com/v2"
    upload = _check(_client.post(f"{base}/upload", headers=headers, content=wav.read_bytes())).json()
    body: dict = {"audio_url": upload["upload_url"], "speech_models": ["universal-3-5-pro"],
                  "language_code": "en"}
    if vocab:
        body["keyterms_prompt"] = [t for t in vocab if len(t.split()) <= 6][:1000]
    job = _check(_client.post(f"{base}/transcript", headers=headers, json=body)).json()
    while True:
        res = _check(_client.get(f"{base}/transcript/{job['id']}", headers=headers)).json()
        if res["status"] == "completed":
            return (res.get("text") or "").strip()
        if res["status"] == "error":
            raise RuntimeError(res.get("error", "transcription failed"))
        time.sleep(0.3)


# name -> (function, key it needs)
PROVIDERS: dict[str, tuple[Callable[[Path, list[str] | None], str], str]] = {
    "deepgram-nova3": (deepgram_nova3, "deepgram"),
    "groq-whisper-v3": (_groq("whisper-large-v3"), "groq"),
    "groq-whisper-turbo": (_groq("whisper-large-v3-turbo"), "groq"),
    "elevenlabs-scribe2": (elevenlabs_scribe2, "elevenlabs"),
    "assemblyai-u35pro": (assemblyai_u35pro, "assemblyai"),
}


def available() -> list[str]:
    return [name for name, (_, key) in PROVIDERS.items() if api_key(key)]
