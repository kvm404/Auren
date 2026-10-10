"""Streaming test: send each recording at real-time speed, like a live microphone.

  uv run stream_test.py                         # AssemblyAI Universal-3.6 Pro and 3.5 Pro, with vocab
  uv run stream_test.py --model universal-3-6-pro --plain
  uv run stream_test.py --only 02               # one recording, printing every partial

Simulates push-to-talk: when the audio ends (key released), send ForceEndpoint and
time how long the final text takes. Results are added to results/transcripts.json
as "assemblyai-stream-<model>|vocab" so score.py ranks them with the batch runs;
their "seconds" is the delay after you stop talking, not the whole request.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from urllib.parse import urlencode

import websockets

from common import RECORDINGS, RESULTS, api_key, load_refs, load_vocab

URL = "wss://streaming.assemblyai.com/v3/ws"
CHUNK_MS = 50
OUT = RESULTS / "transcripts.json"


def pcm(sid: str) -> bytes:
    with wave.open(str(RECORDINGS / f"{sid}.wav")) as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2
        return w.readframes(w.getnframes())


async def stream_one(sid: str, model: str, vocab: list[str] | None, verbose: bool,
                     ptt: bool = True, english: bool = False) -> dict:
    """ptt=True: push-to-talk. Silence never ends the turn (10 s limits); only the key
    release (ForceEndpoint) does, so one recording = one turn. ptt=False: the model
    ends turns at natural pauses. Keyterms go in the URL (UpdateConfiguration got 1008)."""
    params = {"sample_rate": 16000, "encoding": "pcm_s16le", "speech_model": model}
    if ptt:
        params.update(min_turn_silence=10000, max_turn_silence=10000)
    if english:  # steer away from auto-detected Hindi on an Indian accent
        params["language_codes"] = json.dumps(["en"])
    if vocab:
        params["keyterms_prompt"] = json.dumps(vocab[:100])
    audio = pcm(sid)
    step = 16000 * 2 * CHUNK_MS // 1000
    finals: list[str] = []
    t_audio_end = t_first_partial = t_last_final = None
    t0 = time.perf_counter()

    async with websockets.connect(f"{URL}?{urlencode(params)}",
                                  additional_headers={"Authorization": api_key("assemblyai")},
                                  open_timeout=15) as ws:
        begin = json.loads(await ws.recv())
        applied = (begin.get("configuration") or {}).get("model") or (begin.get("configuration") or {}).get("speech_model")
        if verbose:
            print(f"   Begin: model={applied}")

        async def send_audio():
            nonlocal t_audio_end
            start = time.perf_counter()
            for i, off in enumerate(range(0, len(audio), step)):
                await ws.send(audio[off:off + step])
                # Pace to real time: chunk i should leave at start + i * 50 ms.
                delay = start + (i + 1) * CHUNK_MS / 1000 - time.perf_counter()
                if delay > 0:
                    await asyncio.sleep(delay)
            t_audio_end = time.perf_counter()
            await ws.send(json.dumps({"type": "ForceEndpoint"}))   # key released
            await asyncio.sleep(0.05)
            await ws.send(json.dumps({"type": "Terminate"}))

        sender = asyncio.create_task(send_audio())
        try:
            async for raw in ws:
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "Turn":
                    if t_first_partial is None and msg.get("transcript"):
                        t_first_partial = time.perf_counter()
                    if verbose:
                        print(f"   {'FINAL' if msg.get('end_of_turn') else 'part '} {msg.get('transcript')}")
                    if msg.get("end_of_turn"):
                        finals.append(msg.get("transcript", "").strip())
                        t_last_final = time.perf_counter()
                elif kind == "Termination":
                    break
                elif kind == "Error" or "error" in msg:
                    raise RuntimeError(str(msg)[:300])
        finally:
            if not sender.done():
                sender.cancel()
        try:
            await sender
        except (asyncio.CancelledError, websockets.ConnectionClosed):
            pass

    after_end = (t_last_final - t_audio_end) if t_last_final and t_audio_end else None
    return {
        "text": " ".join(f for f in finals if f),
        "seconds": round(max(after_end, 0.0), 3) if after_end is not None else None,
        "first_partial_s": round(t_first_partial - t0, 3) if t_first_partial else None,
        "model_applied": applied,
    }


async def main_async(args) -> None:
    refs, vocab = load_refs(), load_vocab()
    results = json.loads(OUT.read_text()) if OUT.exists() else {}
    sids = [args.only] if args.only else sorted(refs)
    models = [args.model] if args.model else ["universal-3-6-pro", "universal-3-5-pro"]
    mode = "plain" if args.plain else "vocab"
    for model in models:
        short = model.replace("universal-", "u").replace("-pro", "pro").replace("-", "")
        key = f"assemblyai-stream-{short}{'' if args.natural else '-ptt'}{'-en' if args.english else ''}|{mode}"
        print(f"=== {key}")
        for sid in sids:
            for attempt in range(3):
                try:
                    res = await stream_one(sid, model, None if args.plain else vocab, verbose=bool(args.only),
                                           ptt=not args.natural, english=args.english)
                    break
                except Exception as e:  # e.g. 1008 when sessions open too fast: wait and retry
                    res = {"text": "", "seconds": None, "error": str(e)[:300]}
                    await asyncio.sleep(5 * (attempt + 1))
            await asyncio.sleep(1.0)
            results.setdefault(sid, {})[key] = res
            lat = f"{res['seconds']:.2f}s after end" if res.get("seconds") is not None else res.get("error", "no final")
            print(f"  {sid} {lat:>18}  {res.get('text', '')[:80]}", flush=True)
            OUT.write_text(json.dumps(results, indent=2, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", help="universal-3-6-pro, universal-3-5-pro, universal-streaming-english")
    p.add_argument("--plain", action="store_true", help="no vocabulary")
    p.add_argument("--english", action="store_true", help="pin the language to English")
    p.add_argument("--natural", action="store_true", help="let pauses end turns (default: push-to-talk)")
    p.add_argument("--only", help="one recording id, verbose")
    asyncio.run(main_async(p.parse_args()))


if __name__ == "__main__":
    main()
