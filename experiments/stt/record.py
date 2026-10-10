"""Record your voice for the STT test.

  uv run record.py              # go through prompts.txt (skips ones already recorded)
  uv run record.py --free 5     # then 5 commands in your own words; you type what you said
  uv run record.py --redo 04    # re-record one prompt
  uv run record.py --list       # show what is recorded

For each line: press Enter to start, speak, press Enter to stop. Then Enter to keep,
"p" to play it back, or "r" to record again. Audio is 16 kHz mono WAV in recordings/.
"""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import time
import wave

from common import HERE, RECORDINGS, REFS, load_refs


def prompts() -> list[str]:
    lines = (HERE / "prompts.txt").read_text().splitlines()
    return [l.strip() for l in lines if l.strip() and not l.startswith("#")]


def capture(path) -> float:
    input("   Press Enter to start recording… ")
    proc = subprocess.Popen(
        ["pw-record", "--rate", "16000", "--channels", "1", "--format", "s16", str(path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    start = time.perf_counter()
    input("   🔴 Recording, speak now. Press Enter to stop… ")
    time.sleep(0.3)  # don't clip the last word
    proc.send_signal(signal.SIGINT)
    proc.wait(timeout=5)
    return time.perf_counter() - start


def wav_seconds(path) -> float:
    with wave.open(str(path)) as w:
        return w.getnframes() / w.getframerate()


def record_one(sid: str, shown: str | None, refs: dict[str, str]) -> None:
    path = RECORDINGS / f"{sid}.wav"
    while True:
        capture(path)
        secs = wav_seconds(path)
        if secs < 0.5:
            print("   That was empty, let's try again.")
            continue
        choice = input(f"   Got {secs:.1f}s. Enter = keep, p = play back, r = redo: ").strip().lower()
        while choice == "p":
            subprocess.run(["pw-play", str(path)])
            choice = input("   Enter = keep, p = play again, r = redo: ").strip().lower()
        if choice != "r":
            break
    if shown:
        typed = input("   Did you say exactly the words shown? Press Enter if yes. "
                      "If not, type the words you actually said: ").strip()
        refs[sid] = shown if typed.lower() in ("", "y", "yes") else typed
    else:
        refs[sid] = input("   Type exactly what you said: ").strip()
    REFS.write_text(json.dumps(refs, indent=2, ensure_ascii=False))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--free", type=int, default=0, help="also record N commands in your own words")
    p.add_argument("--redo", help="re-record this id, e.g. 04 or f2")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()

    RECORDINGS.mkdir(exist_ok=True)
    refs = load_refs()
    scripted = {f"{i:02d}": text for i, text in enumerate(prompts(), 1)}

    if args.list:
        for sid, text in sorted(refs.items()):
            print(f"{sid}  {wav_seconds(RECORDINGS / f'{sid}.wav'):4.1f}s  {text}")
        return

    if args.redo:
        record_one(args.redo, scripted.get(args.redo), refs)
        return

    todo = [(sid, text) for sid, text in scripted.items() if sid not in refs]
    for n, (sid, text) in enumerate(todo, 1):
        print(f"\n[{n}/{len(todo)}] Say:  {text}")
        record_one(sid, text, refs)

    existing_free = [k for k in refs if k.startswith("f")]
    for n in range(args.free):
        sid = f"f{len(existing_free) + n + 1}"
        print(f"\n[free {n + 1}/{args.free}] Say any command you'd give the assistant.")
        record_one(sid, None, refs)

    print(f"\nDone: {len(refs)} recordings in {RECORDINGS}. Next: uv run run.py")


if __name__ == "__main__":
    main()
