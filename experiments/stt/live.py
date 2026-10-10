"""Talk to the three STT setups live and compare them.

  uv run live.py
  uv run live.py --target <node>   # record from another PipeWire source (testing)

Press Enter, speak, press Enter. While you talk, the MAIN setup streams and shows
words live. When you stop, all three start at once and report what they heard
and how long after you stopped the text was ready:

  MAIN         AssemblyAI streaming, Universal-3.5 Pro, push-to-talk, vocabulary
  BACKUP       ElevenLabs Scribe v2 (batch: sent when you stop), vocabulary
  COMBINATION  Deepgram Nova-3 + Groq Whisper v3 (batch, vocabulary), merged by an LLM

Then type what you actually said to score them (or just press Enter to skip);
a running tally is kept for the session. Recordings go to recordings/live/,
results to results/live.jsonl. Ctrl-C to quit.
"""

from __future__ import annotations

import asyncio
import json
import re
import signal
import sys
import time
import wave
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import websockets

from common import RECORDINGS, RESULTS, api_key, load_vocab, term_hits, wer
from merge import ask
from providers import PROVIDERS, deepgram_nova3, elevenlabs_scribe2

groq_whisper_v3 = PROVIDERS["groq-whisper-v3"][0]

RATE, CHUNK = 16000, 1600            # 50 ms of 16-bit mono audio = 1600 bytes
LIVE_DIR = RECORDINGS / "live"
LOG = RESULTS / "live.jsonl"
MERGE_MODEL = "qwen/qwen3.8-27b"
CLEAR = "\r\x1b[2K"

def _source_args() -> list[str]:
    if "--target" not in sys.argv:
        return []
    target = sys.argv[sys.argv.index("--target") + 1]
    if target.endswith(".monitor"):   # record what plays into a sink
        return ["--target", target.removesuffix(".monitor"), "-P", "{ stream.capture.sink = true }"]
    return ["--target", target]


SOURCE = _source_args()
MIN_SEND, MAX_SEND = 1600, 32000      # AssemblyAI wants 50-1000 ms per message
WHISPER_OUTROS = {"thank you for watching", "thanks for watching", "thank you", "you",
                  "thank you very much", "bye"}   # Whisper's inventions over silence


_TRAILING_OUTRO = re.compile(r"[\s,.!?]*(thank you for watching|thanks for watching)[\s.!?]*$", re.I)


def strip_outro(text: str) -> str:
    """Drop Whisper's silence inventions, whole or tacked onto the end."""
    if text.strip(" .!?").lower() in WHISPER_OUTROS:
        return ""
    return _TRAILING_OUTRO.sub("", text).strip()


async def read_line() -> str:
    return await asyncio.to_thread(sys.stdin.readline)


def save_wav(audio: bytes) -> Path:
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    path = LIVE_DIR / f"{datetime.now():%Y%m%d-%H%M%S}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(audio)
    return path


async def main_setup(vocab: list[str], recorded: asyncio.Future) -> dict:
    """MAIN: capture the mic until Enter, streaming to AssemblyAI the whole time.

    Resolves `recorded` with (audio, t_stop) the moment the mic closes, so the
    batch setups can start in parallel while AssemblyAI finishes.
    """
    params = {"sample_rate": RATE, "encoding": "pcm_s16le", "speech_model": "universal-3-5-pro",
              "min_turn_silence": 10000, "max_turn_silence": 10000,
              "keyterms_prompt": json.dumps(vocab[:100])}
    mic = await asyncio.create_subprocess_exec(
        "pw-record", *SOURCE, "--raw", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    audio = bytearray()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    stop_time: list[float] = []
    result = {"text": "", "seconds": None, "error": None}

    async def read_mic():
        while chunk := await mic.stdout.read(CHUNK):
            audio.extend(chunk)
            await queue.put(chunk)
        await queue.put(None)                       # mic closed: recording is over
        recorded.set_result((bytes(audio), stop_time[0] if stop_time else time.perf_counter()))

    async def stop_on_enter():
        await read_line()
        stop_time.append(time.perf_counter())
        mic.send_signal(signal.SIGINT)

    reader = asyncio.create_task(read_mic())        # capture starts before the socket is open
    stopper = asyncio.create_task(stop_on_enter())
    print("   🔴 Listening… press Enter to stop", flush=True)
    try:
        async with websockets.connect(
                f"wss://streaming.assemblyai.com/v3/ws?{urlencode(params)}",
                additional_headers={"Authorization": api_key("assemblyai")}, open_timeout=10) as ws:
            await ws.recv()                          # Begin

            async def send():
                pending = bytearray()                # audio buffered before connect goes first
                while (chunk := await queue.get()) is not None:
                    pending.extend(chunk)
                    while len(pending) >= MIN_SEND:
                        n = min(len(pending), MAX_SEND)
                        await ws.send(bytes(pending[:n]))
                        del pending[:n]
                if pending:                          # pad the tail up to the 50 ms minimum
                    await ws.send(bytes(pending) + b"\0" * (MIN_SEND - len(pending)))
                await ws.send(json.dumps({"type": "ForceEndpoint"}))
                await ws.send(json.dumps({"type": "Terminate"}))

            sender = asyncio.create_task(send())
            finals = []
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "Turn":
                    text = msg.get("transcript", "")
                    if msg.get("end_of_turn"):
                        finals.append(text.strip())
                        # A turn can also end on its own after 10 s of silence, before you
                        # press Enter; then the text was ready early (0 s).
                        result["seconds"] = (time.perf_counter() - stop_time[0]) if stop_time else 0.0
                        result["before_stop"] = not stop_time
                    elif text and not stop_time:
                        print(f"{CLEAR}   💬 {text[-110:]}", end="", flush=True)
                elif msg.get("type") == "Termination":
                    break
            await sender
            result["text"] = " ".join(f for f in finals if f)
    except Exception as e:
        result["error"] = str(e)[:200]
    finally:
        await stopper
        await reader
        await mic.wait()
    return result


async def call(fn, *args) -> dict:
    start = time.perf_counter()
    try:
        res = {"text": await asyncio.to_thread(fn, *args), "error": None}
    except Exception as e:
        res = {"text": "", "error": str(e)[:200]}
    res["own_s"] = time.perf_counter() - start
    return res


async def backup_setup(wav: Path, vocab: list[str], t_stop: float) -> dict:
    res = await call(elevenlabs_scribe2, wav, vocab)
    res["seconds"] = time.perf_counter() - t_stop
    return res


async def combination_setup(wav: Path, vocab: list[str], t_stop: float) -> dict:
    dg, gq = await asyncio.gather(call(deepgram_nova3, wav, vocab), call(groq_whisper_v3, wav, vocab))
    gq["text"] = strip_outro(gq["text"])
    texts = [r["text"] for r in (dg, gq) if r["text"]]
    res = {"deepgram": dg["text"] or dg["error"], "groq": gq["text"] or gq["error"],
           "deepgram_s": dg["own_s"], "groq_s": gq["own_s"],
           "heard_s": time.perf_counter() - t_stop, "error": None}
    if not texts:
        return {**res, "text": "", "seconds": None, "error": "both providers failed"}
    vocab_line = ", ".join(vocab)
    prompt = (f"Vocabulary: {vocab_line}\n\nTranscript A: {dg['text']}\nTranscript B: {gq['text']}"
              if len(texts) == 2 else f"Vocabulary: {vocab_line}\n\nTranscript: {texts[0]}")
    try:
        res["text"], _ = await asyncio.to_thread(ask, MERGE_MODEL, prompt)
    except Exception as e:
        res["text"], res["error"] = texts[0], f"merge failed, showing one transcript: {e}"[:200]
    res["seconds"] = time.perf_counter() - t_stop
    return res


def show(name: str, res: dict) -> None:
    secs = f"{res['seconds']:.2f}s" if res.get("seconds") is not None else "—"
    if res.get("before_stop"):
        secs = "ready early"
    text = res.get("text") or (f"(failed: {res['error']})" if res.get("error") else "(heard nothing)")
    print(f"   {name:12} {secs:>11}  {text}")


async def one_round(vocab: list[str]) -> tuple[float, dict, dict, dict, Path] | None:
    recorded: asyncio.Future = asyncio.get_running_loop().create_future()
    main_task = asyncio.create_task(main_setup(vocab, recorded))
    audio, t_stop = await recorded                   # you pressed Enter and the mic closed
    secs = len(audio) / (RATE * 2)
    if secs < 0.4:
        await main_task
        return None
    wav = save_wav(audio)
    # All three now run at once, exactly as they would in use.
    main, backup, combo = await asyncio.gather(
        main_task, backup_setup(wav, vocab, t_stop), combination_setup(wav, vocab, t_stop))
    return secs, main, backup, combo, wav


async def run() -> None:
    vocab = load_vocab()
    for key in ("assemblyai", "elevenlabs", "deepgram", "groq"):
        if not api_key(key):
            raise SystemExit(f"missing {key} key (see README)")
    RESULTS.mkdir(exist_ok=True)
    tally = {"MAIN": [0, 0], "BACKUP": [0, 0], "COMBINATION": [0, 0]}   # errors, words
    print(__doc__.split("\n\n", 2)[2].split("\n\nThen")[0].strip(), "\n")
    n = 0
    while True:
        n += 1
        print(f"[{n}] Press Enter, then speak…", end=" ", flush=True)
        await read_line()
        out = await one_round(vocab)
        print(CLEAR, end="")
        if out is None:
            print("   (too short, try again)\n")
            continue
        secs, main, backup, combo, wav = out
        print(f"   {secs:.1f}s recorded · text ready this long after you pressed Enter:")
        show("MAIN", main)
        show("BACKUP", backup)
        show("COMBINATION", combo)
        print(f"   {'':12}↳ Deepgram {combo.get('deepgram_s', 0):4.2f}s  {combo.get('deepgram') or '(heard nothing)'}")
        print(f"   {'':12}↳ Groq     {combo.get('groq_s', 0):4.2f}s  {combo.get('groq') or '(heard nothing)'}")
        if combo.get("heard_s") and combo.get("seconds"):
            print(f"   {'':12}↳ LLM merge {combo['seconds'] - combo['heard_s']:.2f}s")

        said = (await asyncio.to_thread(
            input, "   What did you actually say? (Enter to skip scoring) ")).strip()
        record = {"wav": wav.name, "seconds": round(secs, 2), "said": said or None,
                  "main": main, "backup": backup, "combination": combo}
        if said:
            parts = []
            for name, res in (("MAIN", main), ("BACKUP", backup), ("COMBINATION", combo)):
                e, w = wer(said, res.get("text") or "")
                tally[name][0] += e
                tally[name][1] += w
                _, _, missed = term_hits(said, res.get("text") or "", vocab)
                parts.append(f"{name} {e} err" + (f" (missed {', '.join(missed)})" if missed else ""))
            print("   this one:    " + " · ".join(parts))
            print("   session WER: " + " · ".join(f"{k} {v[0] / v[1]:.1%}" for k, v in tally.items()))
        with LOG.open("a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        print()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        print("\nbye")
